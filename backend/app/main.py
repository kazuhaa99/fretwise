"""FastAPI entry point: wires the job manager to the upload/status/result routes.

Run with: uvicorn app.main:app --reload --port 8000
The Vite dev server proxies `/api` here (see `frontend/vite.config.ts`), so no
CORS headers are strictly required in development — but the app is meant to be
opened from a browser as a genuinely local tool, and being permissive about
local origins costs nothing and saves a class of "why won't this load" reports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import jobs as jobs_api
from .api import tabs as tabs_api
from .api import upload as upload_api
from .jobs.manager import JobManager

DEFAULT_JOBS_ROOT = Path(__file__).resolve().parent.parent / "data" / "jobs"


def create_app(jobs_root: Optional[Path] = None) -> FastAPI:
    """Build a fresh app instance bound to its own `JobManager`.

    Factored out (rather than module-level singletons) so tests can point a
    job manager at a throwaway directory instead of `data/jobs` — running the
    suite shouldn't leave real job artifacts behind in the repo.
    """
    app = FastAPI(title="Fretwise", description="Аудио/видео в табулатуру для гитары")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    manager = JobManager(jobs_root or DEFAULT_JOBS_ROOT)

    app.include_router(upload_api.get_router(manager))
    app.include_router(jobs_api.get_router(manager))
    app.include_router(tabs_api.get_router(manager))

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
