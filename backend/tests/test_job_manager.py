"""Unit tests for `JobManager` housekeeping that doesn't need the HTTP layer:
filename sanitizing, streaming uploads, expiry, orphan sweeping, env parsing.

The pipeline itself is replaced by a no-op here — these tests are about the
manager's bookkeeping, not about audio.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from app.jobs import manager as manager_module
from app.jobs.manager import (
    EmptyUploadError,
    JobBusyError,
    JobError,
    JobManager,
    UploadTooLargeError,
    job_ttl_hours_from_env,
    max_concurrent_jobs_from_env,
    sanitize_title,
)
from app.models import schemas

SETTINGS = schemas.TabSettings(tuning=["E2", "A2", "D3", "G3", "B3", "E4"])
BACKEND_ROOT = Path(__file__).resolve().parent.parent


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


@pytest.fixture
def instant_pipeline(monkeypatch):
    async def fake_pipeline(job):
        job.publish(stage="done", progress=1.0, message="Готово!")

    monkeypatch.setattr(manager_module, "_run_pipeline", fake_pipeline)


# -- sanitize_title ----------------------------------------------------------

@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("song.mp3", "song"),
        ("Кино — Группа крови.mp3", "Кино — Группа крови"),
        ("C:\\Users\\me\\Music\\riff.wav", "riff"),
        ("../../etc/passwd.mp3", "passwd"),
        ('bad<>:"|?*name.flac', "bad name"),
        ("tab\tand\nnewline.mp3", "tab and newline"),
        ("my.live.take.mp4", "my.live.take"),
        (".mp3", "mp3"),  # a dotfile, not an extension — `submit` rejects it anyway
        ("", "fretwise"),
        ("   .  ", "fretwise"),
    ],
)
def test_sanitize_title(filename, expected):
    assert sanitize_title(filename) == expected


def test_sanitize_title_caps_length():
    assert len(sanitize_title("я" * 500 + ".mp3")) == manager_module.MAX_TITLE_LENGTH


# -- submit: streaming upload, title, task references -------------------------

def test_submit_keeps_original_title_and_holds_task_reference(tmp_path, monkeypatch):
    release = asyncio.Event()

    async def blocking_pipeline(job):
        await release.wait()
        job.publish(stage="done", progress=1.0, message="Готово!")

    monkeypatch.setattr(manager_module, "_run_pipeline", blocking_pipeline)

    async def scenario():
        manager = JobManager(tmp_path)
        job = await manager.submit(
            filename="Мелодия.mp3", chunks=_chunks(b"abc", b"def"), settings=SETTINGS, max_bytes=100
        )
        assert job.title == "Мелодия"
        assert job.upload_path.read_bytes() == b"abcdef"
        # The running task must be strongly referenced until it finishes...
        assert len(manager._tasks) == 1
        release.set()
        await asyncio.gather(*manager._tasks)
        await asyncio.sleep(0)
        # ...and released afterwards.
        assert not manager._tasks
        assert job.status.stage == "done"
        assert job.finished_at is not None

    asyncio.run(scenario())


def test_submit_rejects_oversized_upload_and_cleans_up(tmp_path, instant_pipeline):
    async def scenario():
        manager = JobManager(tmp_path)
        with pytest.raises(UploadTooLargeError):
            await manager.submit(
                filename="big.wav", chunks=_chunks(b"x" * 60, b"x" * 60), settings=SETTINGS, max_bytes=100
            )
        assert list(tmp_path.iterdir()) == []

    asyncio.run(scenario())


def test_submit_rejects_empty_upload_and_cleans_up(tmp_path, instant_pipeline):
    async def scenario():
        manager = JobManager(tmp_path)
        with pytest.raises(EmptyUploadError):
            await manager.submit(filename="empty.wav", chunks=_chunks(), settings=SETTINGS, max_bytes=100)
        assert list(tmp_path.iterdir()) == []

    asyncio.run(scenario())


def test_submit_rejects_unknown_extension_without_touching_disk(tmp_path, instant_pipeline):
    async def scenario():
        manager = JobManager(tmp_path)
        with pytest.raises(JobError, match="не поддерживается"):
            await manager.submit(filename="notes.txt", chunks=_chunks(b"x"), settings=SETTINGS, max_bytes=100)
        assert list(tmp_path.iterdir()) == []

    asyncio.run(scenario())


# -- concurrency ---------------------------------------------------------------

def test_jobs_run_one_at_a_time_and_report_queue_position(tmp_path, monkeypatch):
    release = asyncio.Event()
    running = 0
    peak = 0

    async def blocking_pipeline(job):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1
        job.publish(stage="done", progress=1.0, message="Готово!")

    monkeypatch.setattr(manager_module, "_run_pipeline", blocking_pipeline)

    async def scenario():
        manager = JobManager(tmp_path, max_concurrent_jobs=1)
        first = await manager.submit(filename="a.wav", chunks=_chunks(b"a"), settings=SETTINGS, max_bytes=10)
        second = await manager.submit(filename="b.wav", chunks=_chunks(b"b"), settings=SETTINGS, max_bytes=10)
        third = await manager.submit(filename="c.wav", chunks=_chunks(b"c"), settings=SETTINGS, max_bytes=10)
        for _ in range(5):
            await asyncio.sleep(0)

        assert first.status.stage == "queued" and running == 1  # running, still "queued" in the fake
        assert second.status.stage == "queued" and "позиция 1" in second.status.message
        assert third.status.stage == "queued" and "позиция 2" in third.status.message

        release.set()
        await asyncio.gather(*list(manager._tasks))
        assert peak == 1
        assert all(job.status.stage == "done" for job in (first, second, third))

    asyncio.run(scenario())


# -- deletion, expiry and orphan sweeping ------------------------------------

def test_delete_refuses_running_jobs_and_removes_finished_ones(tmp_path, monkeypatch):
    release = asyncio.Event()

    async def blocking_pipeline(job):
        await release.wait()
        job.publish(stage="done", progress=1.0, message="Готово!")

    monkeypatch.setattr(manager_module, "_run_pipeline", blocking_pipeline)

    async def scenario():
        manager = JobManager(tmp_path)
        job = await manager.submit(filename="a.wav", chunks=_chunks(b"a"), settings=SETTINGS, max_bytes=10)
        with pytest.raises(JobBusyError):
            manager.delete(job.id)
        release.set()
        await asyncio.gather(*list(manager._tasks))
        manager.delete(job.id)
        assert manager.get(job.id) is None
        assert not job.work_dir.exists()
        with pytest.raises(KeyError):
            manager.delete(job.id)

    asyncio.run(scenario())


def test_purge_expired_only_removes_old_finished_jobs(tmp_path, instant_pipeline):
    async def scenario():
        manager = JobManager(tmp_path, job_ttl_hours=1.0)
        job = await manager.submit(filename="a.wav", chunks=_chunks(b"a"), settings=SETTINGS, max_bytes=10)
        await asyncio.gather(*list(manager._tasks))
        assert job.finished_at is not None

        assert manager.purge_expired(now=job.finished_at + 3599) == []
        assert manager.get(job.id) is not None

        # Just past the TTL (not exactly on it: float subtraction of large
        # monotonic timestamps can land a hair under 3600).
        assert manager.purge_expired(now=job.finished_at + 3600.5) == [job.id]
        assert manager.get(job.id) is None
        assert not job.work_dir.exists()

    asyncio.run(scenario())


def test_submit_purges_expired_jobs(tmp_path, instant_pipeline):
    async def scenario():
        manager = JobManager(tmp_path, job_ttl_hours=1.0)
        old = await manager.submit(filename="a.wav", chunks=_chunks(b"a"), settings=SETTINGS, max_bytes=10)
        await asyncio.gather(*list(manager._tasks))
        old.finished_at -= 7200  # pretend it finished two hours ago
        await manager.submit(filename="b.wav", chunks=_chunks(b"b"), settings=SETTINGS, max_bytes=10)
        assert manager.get(old.id) is None
        assert not old.work_dir.exists()

    asyncio.run(scenario())


def test_startup_sweeps_only_job_shaped_orphan_dirs(tmp_path):
    orphan = tmp_path / ("a" * 32)
    orphan.mkdir()
    (orphan / "upload.wav").write_bytes(b"x")
    not_a_job = tmp_path / "my-precious-files"
    not_a_job.mkdir()
    uppercase = tmp_path / ("A" * 32)
    uppercase.mkdir()
    stray_file = tmp_path / ("b" * 32)
    stray_file.write_bytes(b"x")

    async def scenario():
        manager = JobManager(tmp_path)
        await manager.start()
        await manager.stop()

    asyncio.run(scenario())

    assert not orphan.exists()
    assert not_a_job.exists()
    assert uppercase.exists()
    assert stray_file.exists()


# -- environment configuration ----------------------------------------------

def test_env_defaults(monkeypatch):
    monkeypatch.delenv("FRETWISE_MAX_CONCURRENT_JOBS", raising=False)
    monkeypatch.delenv("FRETWISE_JOB_TTL_HOURS", raising=False)
    assert max_concurrent_jobs_from_env() == 1
    assert job_ttl_hours_from_env() == 24.0


def test_env_overrides_and_rejects_garbage(monkeypatch):
    monkeypatch.setenv("FRETWISE_MAX_CONCURRENT_JOBS", "3")
    monkeypatch.setenv("FRETWISE_JOB_TTL_HOURS", "0.5")
    assert max_concurrent_jobs_from_env() == 3
    assert job_ttl_hours_from_env() == 0.5

    monkeypatch.setenv("FRETWISE_MAX_CONCURRENT_JOBS", "0")
    with pytest.raises(ValueError):
        max_concurrent_jobs_from_env()
    monkeypatch.setenv("FRETWISE_MAX_CONCURRENT_JOBS", "many")
    with pytest.raises(ValueError):
        max_concurrent_jobs_from_env()


# -- import hygiene ----------------------------------------------------------

def test_importing_the_app_needs_neither_torch_nor_network():
    """`separation.py` used to import torch/demucs and download ffmpeg at
    import time; the server and test suite must start without either."""
    code = (
        "import sys, app.main; "
        "heavy = [m for m in ('torch', 'demucs', 'static_ffmpeg') if m in sys.modules]; "
        "assert not heavy, heavy"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# -- cancellation, ETA, queue position ---------------------------------------

def test_cancelling_a_queued_job_is_immediate(tmp_path, monkeypatch):
    release = asyncio.Event()

    async def blocking_pipeline(job):
        await release.wait()
        job.publish(stage="done", progress=1.0, message="Готово!")

    monkeypatch.setattr(manager_module, "_run_pipeline", blocking_pipeline)

    async def scenario():
        manager = JobManager(tmp_path, max_concurrent_jobs=1)
        running = await manager.submit(filename="a.wav", chunks=_chunks(b"a"), settings=SETTINGS, max_bytes=10)
        first = await manager.submit(filename="b.wav", chunks=_chunks(b"b"), settings=SETTINGS, max_bytes=10)
        second = await manager.submit(filename="c.wav", chunks=_chunks(b"c"), settings=SETTINGS, max_bytes=10)
        for _ in range(5):
            await asyncio.sleep(0)
        assert first.status.queue_position == 1
        assert second.status.queue_position == 2

        status = manager.cancel(first.id)
        assert status.stage == "cancelled"
        assert not first.work_dir.exists()
        # The job behind it moves up.
        assert second.status.queue_position == 1

        release.set()
        await asyncio.gather(*list(manager._tasks), return_exceptions=True)
        assert running.status.stage == "done"
        assert second.status.stage == "done"
        assert first.status.stage == "cancelled"
        with pytest.raises(manager_module.JobFinishedError):
            manager.cancel(first.id)

    asyncio.run(scenario())


def test_cancelling_a_running_job_stops_after_the_current_step(tmp_path, monkeypatch):
    step_started = asyncio.Event()
    finish_step = asyncio.Event()
    reached_next_step = False

    async def pipeline_with_checkpoint(job):
        nonlocal reached_next_step
        try:
            job.publish(stage="separating", progress=0.3, message="Выделяем гитару")
            step_started.set()
            await finish_step.wait()  # the uninterruptible step
            job.check_cancelled()
            reached_next_step = True
            job.publish(stage="done", progress=1.0, message="Готово!")
        except manager_module.JobCancelled:
            job.publish(stage="cancelled", progress=job.status.progress, message=manager_module.CANCELLED_MESSAGE)

    monkeypatch.setattr(manager_module, "_run_pipeline", pipeline_with_checkpoint)

    async def scenario():
        manager = JobManager(tmp_path)
        job = await manager.submit(filename="a.wav", chunks=_chunks(b"a"), settings=SETTINGS, max_bytes=10)
        await step_started.wait()

        status = manager.cancel(job.id)
        assert status.stage == "separating"  # still working on the current step
        assert status.cancel_requested
        assert status.message == manager_module.CANCELLING_MESSAGE
        assert manager.cancel(job.id).cancel_requested  # idempotent

        finish_step.set()
        await asyncio.gather(*list(manager._tasks))
        assert not reached_next_step
        assert job.status.stage == "cancelled"
        assert not job.status.cancel_requested
        assert not job.work_dir.exists()
        assert manager.get(job.id) is not None  # status keeps answering

    asyncio.run(scenario())


def test_eta_counts_down_through_the_remaining_stages(tmp_path):
    job = manager_module.Job(
        id="x" * 32, title="t", settings=SETTINGS, upload_path=tmp_path / "u.wav", work_dir=tmp_path,
        status=schemas.JobStatus(id="x" * 32, stage="queued", progress=0.0, message=""),
    )
    job.publish(stage="extracting_audio", progress=0.0, message="")
    assert job.status.eta_seconds is None  # duration not known yet

    job.duration_seconds = 100.0
    est = manager_module.stage_estimates(100.0)
    job.publish(stage="separating", progress=0.0, message="")
    full = job.status.eta_seconds
    assert full == pytest.approx(est["separating"] + est["transcribing"] + est["arranging"] + est["exporting"], abs=0.1)
    job.publish(stage="separating", progress=0.5, message="")
    assert job.status.eta_seconds == pytest.approx(full - est["separating"] / 2, abs=0.1)
    job.publish(stage="exporting", progress=0.0, message="")
    assert job.status.eta_seconds == pytest.approx(est["exporting"], abs=0.1)
    job.publish(stage="done", progress=1.0, message="")
    assert job.status.eta_seconds == 0.0
