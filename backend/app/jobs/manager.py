"""In-process async job queue for the audio -> tab pipeline.

This is a local, single-user app — there is exactly one "worker" (this
process) and no need for the operational overhead of Redis/Celery. An
`asyncio` task per job plus an in-memory job table covers tracking, SSE
progress fan-out, and result storage with nothing extra to install or run.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..models import schemas
from ..pipeline import gp_export, ingest, separation, transcription
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


@dataclass
class Job:
    id: str
    settings: schemas.TabSettings
    upload_path: Path
    work_dir: Path
    status: schemas.JobStatus
    result: Optional[schemas.JobResult] = None
    _subscribers: list[asyncio.Queue] = field(default_factory=list)

    def publish(self, *, stage: schemas.JobStage, progress: float, message: str, error: Optional[str] = None) -> None:
        self._emit(schemas.JobStatus(
            id=self.id,
            stage=stage,
            progress=max(0.0, min(1.0, progress)),
            message=message,
            error=error,
        ))

    def _emit(self, status: schemas.JobStatus) -> None:
        """Push a fully-built status to `self.status` and every subscriber.

        Used directly (rather than via `publish`) for the terminal "done"
        status, so the caller can build `self.result` first and hand both to
        subscribers in the same step — otherwise a client could see "done" over
        SSE and fetch `/result` before it exists (see `_run_pipeline`).
        """
        self.status = status
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


class JobManager:
    def __init__(self, jobs_root: Path):
        self._jobs: dict[str, Job] = {}
        self._jobs_root = jobs_root
        self._jobs_root.mkdir(parents=True, exist_ok=True)

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    async def submit(self, *, filename: str, content: bytes, settings: schemas.TabSettings) -> Job:
        suffix = Path(filename).suffix.lower()
        if suffix not in ACCEPTED_EXTENSIONS:
            raise JobError(f"Формат файла «{suffix}» не поддерживается")

        job_id = uuid.uuid4().hex
        work_dir = self._jobs_root / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        upload_path = work_dir / f"{UPLOAD_FILENAME_STEM}{suffix}"
        upload_path.write_bytes(content)

        job = Job(
            id=job_id,
            settings=settings,
            upload_path=upload_path,
            work_dir=work_dir,
            status=schemas.JobStatus(id=job_id, stage="queued", progress=0.0, message="В очереди на обработку"),
        )
        self._jobs[job_id] = job
        asyncio.create_task(_run_pipeline(job))
        return job

    def discard(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job is not None:
            shutil.rmtree(job.work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

async def _run_pipeline(job: Job) -> None:
    try:
        job.publish(stage="extracting_audio", progress=0.0, message="Извлекаем звук из файла")
        await _run_blocking(ingest.extract_audio, job.upload_path, job.extracted_audio_path)
        job.publish(stage="extracting_audio", progress=1.0, message="Звук извлечён")

        duration_seconds = await _run_blocking(ingest.audio_duration_seconds, job.extracted_audio_path)

        await _run_with_estimated_progress(
            job,
            stage="separating",
            message="Выделяем партию гитары из микса (это самый долгий шаг)",
            estimated_seconds=max(20.0, duration_seconds * _SEPARATION_SECONDS_PER_AUDIO_SECOND),
            fn=separation.separate_guitar_stem,
            args=(job.extracted_audio_path, job.guitar_stem_path),
        )

        notes = await _run_with_estimated_progress(
            job,
            stage="transcribing",
            message="Распознаём ноты по высоте звука",
            estimated_seconds=max(8.0, duration_seconds * _TRANSCRIPTION_SECONDS_PER_AUDIO_SECOND),
            fn=transcription.transcribe_notes,
            args=(job.guitar_stem_path,),
        )

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

        job.publish(stage="exporting", progress=0.0, message="Собираем файл для Guitar Pro")
        samples, sample_rate = await _run_blocking(_load_mono_samples, job.guitar_stem_path)
        tempo = await _run_blocking(gp_export.estimate_tempo, samples, sample_rate)
        await _run_blocking(
            gp_export.export_gp5,
            tracks,
            fretboard,
            job.tab_path,
            title=Path(job.upload_path).stem,
            artist="Fretwise",
            tempo=tempo,
        )

        # Build the result *before* announcing "done" — otherwise a client could
        # see the terminal status over SSE and request /result before it exists.
        final_status = schemas.JobStatus(id=job.id, stage="done", progress=1.0, message="Готово!")
        job.result = _build_result(
            job, status=final_status, tracks=tracks, fretboard=fretboard, tempo=tempo, duration_seconds=duration_seconds
        )
        job._emit(final_status)

    except JobError as exc:
        job.publish(stage="error", progress=job.status.progress, message="Не получилось обработать файл", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - surfacing the real cause to the user beats a generic 500 page
        job.publish(stage="error", progress=job.status.progress, message="Не получилось обработать файл", error=str(exc))


def _track_name(index: int, total: int) -> str:
    """Plain "Guitar" when `separate_voices` found only one part — matching the
    old single-track label exactly — and "Guitar 1"/"Guitar 2"/... once it
    auto-detects more than one simultaneous performance in the stem."""
    return "Guitar" if total == 1 else f"Guitar {index + 1}"


def _build_result(
    job: Job, *, status: schemas.JobStatus, tracks, fretboard: Fretboard, tempo: int, duration_seconds: float
) -> schemas.JobResult:
    tab = schemas.TabData(
        tuning=list(job.settings.tuning),
        capo=job.settings.capo,
        tempo_bpm=tempo,
        duration_seconds=duration_seconds,
        tracks=[
            schemas.TabTrack(
                name=_track_name(index, len(tracks)),
                events=[
                    schemas.TabEvent(
                        time=event.time,
                        duration=event.duration,
                        notes=[
                            schemas.TabNote(string=note.string, fret=note.fret, technique=note.technique)
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
        tab=tab,
        original_audio_url=f"/api/jobs/{job.id}/audio/original",
        guitar_audio_url=f"/api/jobs/{job.id}/audio/guitar",
        gp5_url=f"/api/jobs/{job.id}/export.gp5",
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
