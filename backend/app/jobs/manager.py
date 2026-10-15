"""In-process async job queue for the audio -> tab pipeline.

This is a local, single-user app — there is exactly one "worker" (this
process) and no need for the operational overhead of Redis/Celery. An
`asyncio` task per job plus an in-memory job table covers tracking, SSE
progress fan-out, and result storage with nothing extra to install or run.

Being in-process doesn't excuse it from the basics a long-running server
needs, though:

- **Bounded concurrency.** Demucs alone can take several GB of RAM per
  track; two uploads at once used to start two separations side by side and
  could take the machine down. Jobs now wait their turn behind a semaphore
  (`FRETWISE_MAX_CONCURRENT_JOBS`, default 1), and "queued" is a real stage
  that tells the user where they stand.
- **Bounded disk usage.** Every job keeps its upload, a decoded WAV and the
  separated stem — easily hundreds of MB per song. Finished jobs expire after
  `FRETWISE_JOB_TTL_HOURS` (default 24), and job folders left behind by a
  previous run (the table is in memory, so a restart forgets them) are swept
  on startup.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterable, Optional

from ..models import schemas
from ..pipeline import gp_export, ingest, rhythm, separation, transcription
from . import edits
from ..pipeline.fretboard import Fretboard
from ..pipeline.tab_builder import build_multi_track_tab

ACCEPTED_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg",
    ".mp4", ".mov", ".mkv", ".webm", ".avi",
}

UPLOAD_FILENAME_STEM = "upload"
EXTRACTED_AUDIO_FILENAME = "audio.wav"
GUITAR_STEM_FILENAME = "guitar.wav"
TAB_FILENAME = "tab.gp5"

FALLBACK_TITLE = "fretwise"
MAX_TITLE_LENGTH = 100

DEFAULT_MAX_CONCURRENT_JOBS = 1
DEFAULT_JOB_TTL_HOURS = 24.0
MAX_CONCURRENT_JOBS_ENV = "FRETWISE_MAX_CONCURRENT_JOBS"
JOB_TTL_HOURS_ENV = "FRETWISE_JOB_TTL_HOURS"

# How often the background sweeper looks for expired jobs. Expiry is measured
# in hours, so checking every few minutes is plenty — and `submit` purges too,
# so a busy server never lets expired jobs pile up between sweeps.
_PURGE_INTERVAL_SECONDS = 600.0

# Job folders are named after `uuid4().hex`. Only folders matching that exact
# shape are ever deleted by the startup sweep, so pointing `jobs_root` at a
# directory that also holds other things can't cost anyone their files.
_JOB_DIR_RE = re.compile(r"^[0-9a-f]{32}$")

_UPLOAD_CHUNK_BYTES = 1024 * 1024

TERMINAL_STAGES = frozenset({"done", "error", "cancelled"})
_TERMINAL_STAGES = TERMINAL_STAGES

# Order of the pipeline's working stages, for the remaining-time estimate.
_WORK_STAGES = ("extracting_audio", "separating", "transcribing", "arranging", "exporting")

CANCELLING_MESSAGE = "Отменяем после текущего шага…"
CANCELLED_MESSAGE = "Обработка остановлена"

# Coarse elapsed-time-vs-estimate progress for stages with no progress hook in
# the underlying library (see `separation.py`'s docstring on why this can't be
# exact). These are rough multipliers of the source clip's duration, tuned to
# "feels about right on CPU" rather than measured — the bar settles at 92% and
# jumps to 100% on completion either way, so a wrong guess only changes how
# fast it crawls, never whether it finishes.
_SEPARATION_SECONDS_PER_AUDIO_SECOND = 1.6
_TRANSCRIPTION_SECONDS_PER_AUDIO_SECOND = 0.35
_PROGRESS_POLL_INTERVAL = 0.4
_PROGRESS_CEILING = 0.92


class JobError(Exception):
    """Raised inside the pipeline to report a user-facing failure message."""


class EmptyUploadError(JobError):
    """The uploaded file had no content at all."""


class UploadTooLargeError(JobError):
    """The upload exceeded the size limit while it was being written to disk."""


class JobBusyError(JobError):
    """The job is still queued or running, so it can't be deleted yet."""


class JobFinishedError(JobError):
    """The job already reached a terminal stage, so there is nothing to cancel."""


class JobCancelled(Exception):
    """Raised at a pipeline checkpoint once the user has asked to cancel."""


def stage_estimates(duration_seconds: float) -> dict[str, float]:
    """Rough wall-clock seconds per stage for a clip of `duration_seconds`.

    Same multipliers that drive the progress bars, so the remaining-time
    estimate and the bars never disagree. Extraction is already over by the
    time the duration is known; it's listed for completeness.
    """
    return {
        "extracting_audio": 2.0,
        "separating": max(20.0, duration_seconds * _SEPARATION_SECONDS_PER_AUDIO_SECOND),
        "transcribing": max(8.0, duration_seconds * _TRANSCRIPTION_SECONDS_PER_AUDIO_SECOND),
        "arranging": 1.0 + duration_seconds * 0.01,
        "exporting": 2.0 + duration_seconds * 0.02,
    }


def sanitize_title(filename: str) -> str:
    """Derive a display title / download name from a client-supplied filename.

    The filename comes straight from the browser, so it is untrusted: it may
    carry a path (old IE and some tools send `C:\\Users\\...\\song.mp3`),
    control characters, characters that are illegal in filenames on Windows,
    or be absurdly long. We keep only the basename without its extension,
    drop anything a filesystem or a Content-Disposition header would choke on,
    and fall back to a neutral name if nothing usable remains. Non-ASCII text
    (Cyrillic song titles!) is kept as-is — Starlette's `FileResponse` encodes
    it into an RFC 5987 `filename*=` header so browsers download it correctly.
    """
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base.strip(".") else base
    stem = unicodedata.normalize("NFC", stem)
    cleaned = "".join(
        " " if unicodedata.category(ch).startswith("C") or ch in '<>:"/\\|?*' else ch
        for ch in stem
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned[:MAX_TITLE_LENGTH].rstrip(" .")
    return cleaned or FALLBACK_TITLE


def _env_number(name: str, default: float, *, cast, minimum: float):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r}: ожидается число") from None
    if value < minimum:
        raise ValueError(f"{name}={raw!r}: значение должно быть не меньше {minimum}")
    return value


def max_concurrent_jobs_from_env() -> int:
    """Read `FRETWISE_MAX_CONCURRENT_JOBS`, failing loudly at startup on a typo
    rather than silently running with a surprising limit."""
    return _env_number(MAX_CONCURRENT_JOBS_ENV, DEFAULT_MAX_CONCURRENT_JOBS, cast=int, minimum=1)


def job_ttl_hours_from_env() -> float:
    return _env_number(JOB_TTL_HOURS_ENV, DEFAULT_JOB_TTL_HOURS, cast=float, minimum=0.0)


@dataclass
class Job:
    id: str
    title: str  # sanitized original filename (no extension) — see `sanitize_title`
    settings: schemas.TabSettings
    upload_path: Path
    work_dir: Path
    status: schemas.JobStatus
    result: Optional[schemas.JobResult] = None
    finished_at: Optional[float] = None  # `time.monotonic()` when it reached a terminal stage
    duration_seconds: Optional[float] = None  # of the decoded audio, once known
    cancel_requested: bool = False
    revision: int = 0  # of the tab; bumped by every saved edit
    _subscribers: list[asyncio.Queue] = field(default_factory=list)

    @property
    def is_finished(self) -> bool:
        return self.status.stage in _TERMINAL_STAGES

    def publish(
        self,
        *,
        stage: schemas.JobStage,
        progress: float,
        message: str,
        error: Optional[str] = None,
        queue_position: Optional[int] = None,
    ) -> None:
        progress = max(0.0, min(1.0, progress))
        if self.cancel_requested and stage not in _TERMINAL_STAGES:
            message = CANCELLING_MESSAGE
        self._emit(schemas.JobStatus(
            id=self.id,
            stage=stage,
            progress=progress,
            message=message,
            error=error,
            queue_position=queue_position,
            eta_seconds=self._eta(stage, progress),
            cancel_requested=self.cancel_requested and stage not in _TERMINAL_STAGES,
        ))

    def _eta(self, stage: str, progress: float) -> Optional[float]:
        """Remaining seconds: what's left of this stage plus every later one."""
        if stage in _TERMINAL_STAGES:
            return 0.0 if stage == "done" else None
        if self.duration_seconds is None or stage not in _WORK_STAGES:
            return None
        estimates = stage_estimates(self.duration_seconds)
        index = _WORK_STAGES.index(stage)
        remaining = estimates[stage] * (1.0 - progress)
        remaining += sum(estimates[later] for later in _WORK_STAGES[index + 1:])
        return round(remaining, 1)

    def check_cancelled(self) -> None:
        """Pipeline checkpoint between steps (see `JobManager.cancel`)."""
        if self.cancel_requested:
            raise JobCancelled()

    def _emit(self, status: schemas.JobStatus) -> None:
        """Push a fully-built status to `self.status` and every subscriber.

        Used directly (rather than via `publish`) for the terminal "done"
        status, so the caller can build `self.result` first and hand both to
        subscribers in the same step — otherwise a client could see "done" over
        SSE and fetch `/result` before it exists (see `_run_pipeline`).
        """
        self.status = status
        if status.stage in _TERMINAL_STAGES and self.finished_at is None:
            self.finished_at = time.monotonic()
        for queue in list(self._subscribers):
            queue.put_nowait(status)

    def subscribe(self) -> "asyncio.Queue[schemas.JobStatus]":
        queue: "asyncio.Queue[schemas.JobStatus]" = asyncio.Queue()
        queue.put_nowait(self.status)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[schemas.JobStatus]") -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    @property
    def extracted_audio_path(self) -> Path:
        return self.work_dir / EXTRACTED_AUDIO_FILENAME

    @property
    def guitar_stem_path(self) -> Path:
        return self.work_dir / GUITAR_STEM_FILENAME

    @property
    def tab_path(self) -> Path:
        return self.work_dir / TAB_FILENAME

    @property
    def original_tab_path(self) -> Path:
        return self.work_dir / edits.ORIGINAL_TAB_FILENAME

    @property
    def edited_tab_path(self) -> Path:
        return self.work_dir / edits.EDITED_TAB_FILENAME


class JobManager:
    def __init__(
        self,
        jobs_root: Path,
        *,
        max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS,
        job_ttl_hours: float = DEFAULT_JOB_TTL_HOURS,
    ):
        if max_concurrent_jobs < 1:
            raise ValueError("max_concurrent_jobs must be at least 1")
        self._jobs: dict[str, Job] = {}
        self._jobs_root = jobs_root
        self._jobs_root.mkdir(parents=True, exist_ok=True)
        self._job_ttl_seconds = job_ttl_hours * 3600.0
        self._semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._waiting: list[Job] = []
        # The event loop only keeps *weak* references to tasks, so a fire-and-
        # forget `create_task` result can be garbage-collected mid-run (see the
        # warning in the `asyncio.create_task` docs). Holding them here until
        # they finish is the documented fix.
        self._tasks: set[asyncio.Task] = set()
        self._job_tasks: dict[str, asyncio.Task] = {}
        self._purge_task: Optional[asyncio.Task] = None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Called from the app's lifespan: clean up after the previous run and
        start the periodic expiry sweep."""
        self.sweep_orphaned_dirs()
        self._purge_task = asyncio.create_task(self._purge_periodically())

    async def stop(self) -> None:
        if self._purge_task is not None:
            self._purge_task.cancel()
            try:
                await self._purge_task
            except asyncio.CancelledError:
                pass
            self._purge_task = None

    async def _purge_periodically(self) -> None:
        interval = min(_PURGE_INTERVAL_SECONDS, max(self._job_ttl_seconds / 2, 1.0))
        while True:
            await asyncio.sleep(interval)
            self.purge_expired()

    def sweep_orphaned_dirs(self) -> list[str]:
        """Delete job folders on disk that no in-memory job owns.

        The job table lives in memory, so after a restart every folder from the
        previous run is unreachable through the API — nothing would ever
        delete it. Only directories named like a job id are touched.
        """
        removed = []
        for entry in self._jobs_root.iterdir():
            if entry.is_dir() and _JOB_DIR_RE.match(entry.name) and entry.name not in self._jobs:
                shutil.rmtree(entry, ignore_errors=True)
                removed.append(entry.name)
        return removed

    def purge_expired(self, now: Optional[float] = None) -> list[str]:
        """Delete finished jobs older than the TTL. Queued/running jobs are
        never touched, however long they take."""
        now = time.monotonic() if now is None else now
        expired = [
            job.id for job in self._jobs.values()
            if job.finished_at is not None and now - job.finished_at >= self._job_ttl_seconds
        ]
        for job_id in expired:
            self.discard(job_id)
        return expired

    # -- job table -----------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    async def submit(
        self,
        *,
        filename: str,
        chunks: AsyncIterable[bytes],
        settings: schemas.TabSettings,
        max_bytes: int,
    ) -> Job:
        """Stream an upload to disk and enqueue it.

        The upload is written chunk by chunk with a running size count instead
        of being read into memory whole: a 500 MB video used to mean 500 MB of
        RAM (on top of the copy Starlette had already spooled to disk) before
        the size was even checked. On any failure the half-written job folder
        is removed, so rejected uploads leave nothing behind.
        """
        suffix = Path(filename).suffix.lower()
        if suffix not in ACCEPTED_EXTENSIONS:
            raise JobError(f"Формат файла «{suffix or 'без расширения'}» не поддерживается")

        self.purge_expired()

        job_id = uuid.uuid4().hex
        work_dir = self._jobs_root / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        upload_path = work_dir / f"{UPLOAD_FILENAME_STEM}{suffix}"

        try:
            written = 0
            with upload_path.open("wb") as fh:
                async for chunk in chunks:
                    written += len(chunk)
                    if written > max_bytes:
                        limit_mb = max_bytes // (1024 * 1024)
                        raise UploadTooLargeError(f"Файл слишком большой (лимит — {limit_mb} МБ)")
                    await asyncio.to_thread(fh.write, chunk)
            if written == 0:
                raise EmptyUploadError("Файл пуст")
        except BaseException:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

        job = Job(
            id=job_id,
            title=sanitize_title(filename),
            settings=settings,
            upload_path=upload_path,
            work_dir=work_dir,
            status=schemas.JobStatus(id=job_id, stage="queued", progress=0.0, message="В очереди на обработку"),
        )
        self._jobs[job_id] = job
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        self._job_tasks[job_id] = task
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda _t, job_id=job_id: self._job_tasks.pop(job_id, None))
        return job

    def cancel(self, job_id: str) -> schemas.JobStatus:
        """Cancel a job on the user's request (`POST /api/jobs/{id}/cancel`).

        A job still waiting in line is cancelled immediately. A running one
        can't be interrupted mid-step — Demucs and Basic Pitch run in worker
        threads, and Python threads can't be stopped from outside — so it is
        flagged and stops at the next checkpoint between pipeline steps
        (`Job.check_cancelled`). Every status it publishes meanwhile says so
        ("Отменяем после текущего шага…"), and its files are removed once it
        stops.
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.is_finished:
            raise JobFinishedError("Задача уже завершена — отменять нечего")
        if job.cancel_requested:
            return job.status

        job.cancel_requested = True
        if job in self._waiting:
            task = self._job_tasks.get(job_id)
            if task is not None:
                task.cancel()
            self._finish_cancelled(job)
        else:
            current = job.status
            job.publish(stage=current.stage, progress=current.progress, message=CANCELLING_MESSAGE)
        return job.status

    def _finish_cancelled(self, job: Job) -> None:
        if job in self._waiting:
            self._waiting.remove(job)
            self._announce_queue_positions()
        if not job.is_finished:
            job.publish(stage="cancelled", progress=job.status.progress, message=CANCELLED_MESSAGE)
        # Keep the job (so its status keeps answering "cancelled") but not its
        # files; the TTL sweep forgets the record later like any finished job.
        shutil.rmtree(job.work_dir, ignore_errors=True)

    # -- manual edits ----------------------------------------------------------

    def _done_job(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status.stage != "done" or job.result is None:
            raise JobBusyError("Править можно только готовую табулатуру")
        return job

    def save_edits(self, job_id: str, base_revision: int, tracks: list[schemas.TabTrack]) -> schemas.JobResult:
        """Validate and store an edited tab, then rebuild the .gp5 from it.

        Blocking (writes files, exports .gp5) — call it from a worker thread.
        """
        job = self._done_job(job_id)
        if base_revision != job.revision:
            raise edits.EditConflictError()
        assert job.result is not None
        base = job.result.tab
        edits.validate_tracks(tracks, base)
        tab = edits.apply_tracks(base, tracks)
        edits.write_json(tab, job.edited_tab_path)
        edits.write_gp5(tab, job.tab_path, title=job.title)
        job.revision += 1
        job.result = job.result.model_copy(update={"tab": tab, "revision": job.revision, "edited": True})
        return job.result

    def revert_edits(self, job_id: str) -> schemas.JobResult:
        """Back to the tab exactly as the pipeline produced it."""
        job = self._done_job(job_id)
        assert job.result is not None
        tab = edits.read_json(job.original_tab_path)
        edits.write_gp5(tab, job.tab_path, title=job.title)
        job.edited_tab_path.unlink(missing_ok=True)
        job.revision += 1
        job.result = job.result.model_copy(update={"tab": tab, "revision": job.revision, "edited": False})
        return job.result

    def delete(self, job_id: str) -> None:
        """Delete a finished job on the user's request (`DELETE /api/jobs/{id}`).

        Running jobs are refused rather than cancelled: their pipeline step runs
        in a worker thread that can't be interrupted, so "deleting" one would
        only pull the folder out from under Demucs mid-write.
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if not job.is_finished:
            raise JobBusyError("Задача ещё выполняется — удалить её можно после завершения")
        self.discard(job_id)

    def discard(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job is not None:
            shutil.rmtree(job.work_dir, ignore_errors=True)

    # -- scheduling ----------------------------------------------------------

    async def _run(self, job: Job) -> None:
        self._waiting.append(job)
        self._announce_queue_positions()
        try:
            async with self._semaphore:
                self._waiting.remove(job)
                self._announce_queue_positions()
                await _run_pipeline(job)
            if job.status.stage == "cancelled":
                self._finish_cancelled(job)
        except asyncio.CancelledError:
            # Only `cancel()` cancels these tasks, and only while queued.
            self._finish_cancelled(job)
        finally:
            if job in self._waiting:
                self._waiting.remove(job)
                self._announce_queue_positions()

    def _announce_queue_positions(self) -> None:
        """Tell every waiting job where it stands, so "queued" means something.

        Only reached while the semaphore is actually saturated; a job that gets
        a slot straight away leaves the waiting list before anyone sees this.
        """
        for position, job in enumerate(self._waiting, start=1):
            if self._semaphore.locked():
                message = f"В очереди: позиция {position}. Начнём, как только освободится обработчик"
                job.publish(stage="queued", progress=0.0, message=message, queue_position=position)
            else:
                job.publish(stage="queued", progress=0.0, message="В очереди на обработку")


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

async def _run_pipeline(job: Job) -> None:
    try:
        job.publish(stage="extracting_audio", progress=0.0, message="Извлекаем звук из файла")
        await _run_blocking(ingest.extract_audio, job.upload_path, job.extracted_audio_path)
        job.publish(stage="extracting_audio", progress=1.0, message="Звук извлечён")

        duration_seconds = await _run_blocking(ingest.audio_duration_seconds, job.extracted_audio_path)
        job.duration_seconds = duration_seconds
        job.check_cancelled()

        await _run_with_estimated_progress(
            job,
            stage="separating",
            message="Выделяем партию гитары из микса (это самый долгий шаг)",
            estimated_seconds=max(20.0, duration_seconds * _SEPARATION_SECONDS_PER_AUDIO_SECOND),
            fn=separation.separate_guitar_stem,
            args=(job.extracted_audio_path, job.guitar_stem_path),
        )

        job.check_cancelled()

        notes = await _run_with_estimated_progress(
            job,
            stage="transcribing",
            message="Распознаём ноты по высоте звука",
            estimated_seconds=max(8.0, duration_seconds * _TRANSCRIPTION_SECONDS_PER_AUDIO_SECOND),
            fn=transcription.transcribe_notes,
            args=(job.guitar_stem_path,),
        )

        job.check_cancelled()

        job.publish(stage="arranging", progress=0.0, message="Раскладываем ноты по грифу")
        fretboard = Fretboard.from_note_names(job.settings.tuning, capo=job.settings.capo)
        tracks = await _run_blocking(
            build_multi_track_tab,
            notes,
            fretboard,
            max_fret_span=job.settings.max_fret_span,
            position_jump_weight=job.settings.position_jump_weight,
        )
        job.publish(stage="arranging", progress=1.0, message="Аппликатура подобрана")

        job.check_cancelled()

        job.publish(stage="exporting", progress=0.0, message="Собираем файл для Guitar Pro")
        samples, sample_rate = await _run_blocking(_load_mono_samples, job.guitar_stem_path)
        grid = await _run_blocking(rhythm.analyze_rhythm, samples, sample_rate)
        layout = await _run_blocking(
            gp_export.export_gp5,
            tracks,
            fretboard,
            job.tab_path,
            title=job.title,
            artist="Fretwise",
            grid=grid,
        )

        # Build the result *before* announcing "done" — otherwise a client could
        # see the terminal status over SSE and request /result before it exists.
        final_status = schemas.JobStatus(id=job.id, stage="done", progress=1.0, message="Готово!", eta_seconds=0.0)
        job.result = _build_result(
            job, status=final_status, tracks=tracks, layout=layout, duration_seconds=duration_seconds
        )
        # Kept for "revert all edits" (and for restoring jobs after a restart).
        await _run_blocking(edits.write_json, job.result.tab, job.original_tab_path)
        job._emit(final_status)

    except JobCancelled:
        job.publish(stage="cancelled", progress=job.status.progress, message=CANCELLED_MESSAGE)
    except JobError as exc:
        job.publish(stage="error", progress=job.status.progress, message="Не получилось обработать файл", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - surfacing the real cause to the user beats a generic 500 page
        job.publish(stage="error", progress=job.status.progress, message="Не получилось обработать файл", error=str(exc))


def _build_result(
    job: Job, *, status: schemas.JobStatus, tracks, layout: gp_export.Layout, duration_seconds: float
) -> schemas.JobResult:
    tab = schemas.TabData(
        tuning=list(job.settings.tuning),
        capo=job.settings.capo,
        tempo_bpm=layout.grid.tempo,
        duration_seconds=duration_seconds,
        beats_per_measure=layout.grid.beats_per_measure,
        # The exact bar lines written to the .gp5, so the in-browser tab and
        # the downloaded file agree on where every measure starts.
        measure_times=[round(t, 4) for t in layout.measure_start_times()],
        beat_times=[round(t, 4) for t in layout.beat_times()],
        tracks=[
            schemas.TabTrack(
                name=gp_export.track_name(index, len(tracks)),
                events=[
                    schemas.TabEvent(
                        time=event.time,
                        duration=event.duration,
                        notes=[
                            schemas.TabNote(
                                string=note.string,
                                fret=note.fret,
                                technique=note.technique,
                                bend_semitones=note.bend_semitones if note.technique == "bend" else None,
                            )
                            for note in event.notes
                        ],
                    )
                    for event in track_events
                ],
            )
            for index, track_events in enumerate(tracks)
        ],
    )
    return schemas.JobResult(
        job=status,
        title=job.title,
        tab=tab,
        original_audio_url=f"/api/jobs/{job.id}/audio/original",
        guitar_audio_url=f"/api/jobs/{job.id}/audio/guitar",
        gp5_url=f"/api/jobs/{job.id}/export.gp5",
        txt_url=f"/api/jobs/{job.id}/export.txt",
        midi_url=f"/api/jobs/{job.id}/export.mid",
    )


def _load_mono_samples(wav_path: Path):
    import soundfile as sf

    samples, sample_rate = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    return samples, sample_rate


async def _run_blocking(fn, *args, **kwargs):
    """Run a blocking (subprocess/CPU-bound) pipeline step off the event loop
    so SSE updates keep flowing while it runs."""
    loop = asyncio.get_running_loop()
    if kwargs:
        from functools import partial
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))
    return await loop.run_in_executor(None, fn, *args)


async def _run_with_estimated_progress(job: Job, *, stage: schemas.JobStage, message: str, estimated_seconds: float, fn, args):
    """Run an opaque long-running step, publishing elapsed-time-based progress.

    The wrapped libraries (Demucs, Basic Pitch) expose no per-chunk progress
    hook in the versions we use — see `separation.py`. Rather than fabricate a
    fake fine-grained number, we estimate from elapsed time vs. a rough guess,
    cap the crawl at `_PROGRESS_CEILING` so it never claims to finish before it
    does, and snap to 100% the moment the step actually completes.
    """
    job.publish(stage=stage, progress=0.0, message=message)
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(None, fn, *args)
    started = time.monotonic()

    while True:
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=_PROGRESS_POLL_INTERVAL)
            break
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - started
            progress = min(_PROGRESS_CEILING, elapsed / estimated_seconds) if estimated_seconds > 0 else 0.0
            job.publish(stage=stage, progress=progress, message=message)

    job.publish(stage=stage, progress=1.0, message=message)
    return result
