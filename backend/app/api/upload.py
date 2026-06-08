"""POST /api/jobs — accept an uploaded audio/video file plus arrangement
settings, and enqueue it for processing."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from ..jobs.manager import JobError, JobManager
from ..models import schemas

MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def get_router(manager: JobManager) -> APIRouter:
    router = APIRouter(prefix="/api/jobs", tags=["jobs"])

    @router.post("", response_model=schemas.JobStatus)
    async def create_job(file: UploadFile = File(...), settings: str = Form(...)) -> schemas.JobStatus:
        try:
            parsed_settings = schemas.TabSettings.model_validate_json(settings)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=f"Некорректные настройки: {exc}") from exc

        content = await file.read()
        if not content:
            raise HTTPException(status_code=422, detail="Файл пуст")
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Файл слишком большой (лимит — 500 МБ)")

        try:
            job = await manager.submit(filename=file.filename or "upload", content=content, settings=parsed_settings)
        except JobError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        return job.status

    return router
