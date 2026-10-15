"""GET /api/jobs/{id} — job status, /events — live progress via SSE,
POST /api/jobs/{id}/cancel — stop a job, and DELETE /api/jobs/{id} — drop a
finished job and its files."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from sse_starlette.sse import EventSourceResponse

from ..jobs.manager import TERMINAL_STAGES, JobBusyError, JobFinishedError, JobManager
from ..models import schemas



def get_router(manager: JobManager) -> APIRouter:
    router = APIRouter(prefix="/api/jobs", tags=["jobs"])

    @router.get("/{job_id}", response_model=schemas.JobStatus)
    async def get_status(job_id: str) -> schemas.JobStatus:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        return job.status

    @router.post("/{job_id}/cancel", response_model=schemas.JobStatus)
    async def cancel_job(job_id: str) -> schemas.JobStatus:
        try:
            return manager.cancel(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Задача не найдена") from None
        except JobFinishedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.delete("/{job_id}", status_code=204)
    async def delete_job(job_id: str) -> Response:
        try:
            manager.delete(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Задача не найдена") from None
        except JobBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=204)

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
                    if status.stage in TERMINAL_STAGES:
                        return
            finally:
                job.unsubscribe(queue)

        return EventSourceResponse(event_stream())

    return router
