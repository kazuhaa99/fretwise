"""GET /api/jobs/{id} — job status, and /events — live progress via SSE."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ..jobs.manager import JobManager
from ..models import schemas

_TERMINAL_STAGES = {"done", "error"}


def get_router(manager: JobManager) -> APIRouter:
    router = APIRouter(prefix="/api/jobs", tags=["jobs"])

    @router.get("/{job_id}", response_model=schemas.JobStatus)
    async def get_status(job_id: str) -> schemas.JobStatus:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return job.status

    @router.get("/{job_id}/events")
    async def stream_events(job_id: str, request: Request) -> EventSourceResponse:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")

        async def event_stream():
            queue = job.subscribe()
            try:
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        status = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    yield {"data": status.model_dump_json()}
                    if status.stage in _TERMINAL_STAGES:
                        return
            finally:
                job.unsubscribe(queue)

        return EventSourceResponse(event_stream())

    return router
