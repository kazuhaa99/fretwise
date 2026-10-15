"""A finished job's output: tab JSON, isolated/original audio for in-browser
playback, the downloadable .gp5 file — and manual edits to the tab
(PUT /tab, DELETE /edits; see `jobs/edits.py`)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..jobs import edits
from ..jobs.manager import JobBusyError, JobManager
from ..models import schemas

_AUDIO_KIND_TO_PATH_ATTR = {
    "original": "extracted_audio_path",
    "guitar": "guitar_stem_path",
}


def get_router(manager: JobManager) -> APIRouter:
    router = APIRouter(prefix="/api/jobs", tags=["jobs"])

    def _require_done_job(job_id: str):
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        if job.status.stage == "error":
            raise HTTPException(status_code=409, detail=job.status.error or "Обработка завершилась с ошибкой")
        if job.status.stage != "done" or job.result is None:
            raise HTTPException(status_code=409, detail="Обработка ещё не завершена")
        return job

    @router.get("/{job_id}/result", response_model=schemas.JobResult)
    async def get_result(job_id: str) -> schemas.JobResult:
        job = _require_done_job(job_id)
        assert job.result is not None
        return job.result

    @router.put("/{job_id}/tab", response_model=schemas.JobResult)
    async def save_tab(job_id: str, body: schemas.TabEditRequest) -> schemas.JobResult:
        _require_done_job(job_id)
        try:
            return await asyncio.to_thread(manager.save_edits, job_id, body.base_revision, body.tracks)
        except KeyError:
            raise HTTPException(status_code=404, detail="Задача не найдена") from None
        except JobBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except edits.EditConflictError:
            raise HTTPException(
                status_code=409,
                detail="Табулатуру уже изменили в другой вкладке. Обнови страницу, чтобы увидеть последние правки.",
            ) from None
        except edits.EditError as exc:
            raise HTTPException(status_code=422, detail=f"Правка не сохранена: {exc}") from exc

    @router.delete("/{job_id}/edits", response_model=schemas.JobResult)
    async def revert_tab(job_id: str) -> schemas.JobResult:
        _require_done_job(job_id)
        try:
            return await asyncio.to_thread(manager.revert_edits, job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Задача не найдена") from None

    @router.get("/{job_id}/audio/{kind}")
    async def get_audio(job_id: str, kind: str) -> FileResponse:
        job = _require_done_job(job_id)
        attr = _AUDIO_KIND_TO_PATH_ATTR.get(kind)
        if attr is None:
            raise HTTPException(status_code=404, detail="Неизвестный источник звука")

        path: Path = getattr(job, attr)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Аудиофайл не найден")
        return FileResponse(path, media_type="audio/wav")

    @router.get("/{job_id}/export.gp5")
    async def download_tab(job_id: str) -> FileResponse:
        job = _require_done_job(job_id)
        if not job.tab_path.exists():
            raise HTTPException(status_code=404, detail="Файл табулатуры не найден")

        # The original file's (sanitized) name, not the on-disk "upload.<ext>":
        # FileResponse emits an RFC 5987 `filename*=utf-8''...` header for
        # non-ASCII names, so Cyrillic titles download intact.
        download_name = f"{job.title}.gp5"
        return FileResponse(
            job.tab_path,
            media_type="application/octet-stream",
            filename=download_name,
        )

    return router
