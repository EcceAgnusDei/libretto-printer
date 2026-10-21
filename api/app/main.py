from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.routers.extract import router as extract_router
from app.routers.ingest import router as ingest_router
from app.services import extract as extract_service
from app.services import usage_quota

logger = logging.getLogger(__name__)


class ForceHttpsMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not settings.force_https:
            return await call_next(request)
        forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        scheme = forwarded or request.url.scheme
        if scheme.lower() == "https":
            return await call_next(request)
        return Response(
            content='{"detail":"HTTPS requis"}',
            status_code=400,
            media_type="application/json",
        )


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    purged = extract_service.purge_expired_sessions()
    if purged:
        logger.info("Startup purged %s expired session(s)", purged)
    extract_service.start_supervisor()
    try:
        yield
    finally:
        extract_service.stop_supervisor()


app = FastAPI(
    title="libretto-printer API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(ForceHttpsMiddleware)

app.include_router(ingest_router)
app.include_router(extract_router)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "libretto-printer-api",
        "gemini_configured": settings.gemini_configured,
    }


@app.get("/config")
def public_config() -> dict[str, object]:
    usage = usage_quota.get_usage()
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "max_pages": settings.max_pages_per_session,
        "batch_size": settings.batch_size,
        "gemini_max_batches_per_day": usage["batches_limit"],
        "batches_used_today": usage["batches_used"],
        "batches_remaining_today": usage["batches_remaining"],
    }


@app.get("/quota")
def quota_status(page_count: int | None = None) -> dict[str, object]:
    usage = usage_quota.get_usage()
    payload: dict[str, object] = {**usage}
    if page_count is not None:
        payload.update(usage_quota.needs_over_quota_confirmation(page_count))
    return payload
