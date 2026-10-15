"""POST /api/jobs — accept an uploaded audio/video file plus arrangement
settings, and enqueue it for processing."""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError

from ..jobs.manager import EmptyUploadError, JobError, JobManager, UploadTooLargeError
from ..models import schemas
from .errors import describe_validation_error

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

# Multipart framing (boundaries, part headers, the settings JSON) adds a little
# on top of the file itself; a request whose declared size is beyond the limit
# by more than this cannot possibly contain an acceptable file.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024


async def _iter_chunks(file: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        yield chunk


def _too_large_message() -> str:
    return f"Файл слишком большой (лимит — {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ)"


def get_router(manager: JobManager) -> APIRouter:
    router = APIRouter(prefix="/api/jobs", tags=["jobs"])

    @router.post("", response_model=schemas.JobStatus)
    async def create_job(
        request: Request,
        file: UploadFile = File(...),
        settings: str = Form(...),
    ) -> schemas.JobStatus:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD_BYTES:
            raise HTTPException(status_code=413, detail=_too_large_message())

        try:
            parsed_settings = schemas.TabSettings.model_validate_json(settings)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=describe_validation_error(exc)) from exc

        try:
            job = await manager.submit(
                filename=file.filename or "upload",
                chunks=_iter_chunks(file),
                settings=parsed_settings,
                max_bytes=MAX_UPLOAD_BYTES,
            )
        except UploadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except EmptyUploadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except JobError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await file.close()

        return job.status

    return router
