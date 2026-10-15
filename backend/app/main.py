"""FastAPI entry point: wires the job manager to the upload/status/result routes.

Run with: uvicorn app.main:app --reload --port 8000
The Vite dev server proxies `/api` here (see `frontend/vite.config.ts`), so no
CORS headers are strictly required in development — but the app is meant to be
opened from a browser as a genuinely local tool, and being permissive about
local origins costs nothing and saves a class of "why won't this load" reports.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import jobs as jobs_api
from .api import tabs as tabs_api
from .api import upload as upload_api
from .jobs.manager import JobManager, job_ttl_hours_from_env, max_concurrent_jobs_from_env

DEFAULT_JOBS_ROOT = Path(__file__).resolve().parent.parent / "data" / "jobs"


def create_app(
    jobs_root: Optional[Path] = None,
    *,
    max_concurrent_jobs: Optional[int] = None,
    job_ttl_hours: Optional[float] = None,
) -> FastAPI:
    """Build a fresh app instance bound to its own `JobManager`.

    Factored out (rather than module-level singletons) so tests can point a
    job manager at a throwaway directory instead of `data/jobs` — running the
    suite shouldn't leave real job artifacts behind in the repo.

    Limits default to the `FRETWISE_*` environment variables (see
    `jobs/manager.py`); explicit arguments win, which keeps tests independent
    of whatever the developer's shell happens to export.
    """
    manager = JobManager(
        jobs_root or DEFAULT_JOBS_ROOT,
        max_concurrent_jobs=max_concurrent_jobs if max_concurrent_jobs is not None else max_concurrent_jobs_from_env(),
        job_ttl_hours=job_ttl_hours if job_ttl_hours is not None else job_ttl_hours_from_env(),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Startup sweeps job folders orphaned by the previous run and starts
        # the expiry loop; doing it here (not at import) means merely importing
        # `app.main` never deletes anything.
        await manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(title="Fretwise", description="Аудио/видео в табулатуру для гитары", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],  # includes DELETE for job cleanup
        allow_headers=["*"],
    )

    app.include_router(upload_api.get_router(manager))
    app.include_router(jobs_api.get_router(manager))
    app.include_router(tabs_api.get_router(manager))

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
