from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers.extract import router as extract_router
from app.routers.ingest import router as ingest_router
from app.services import extract as extract_service
from app.services import usage_quota


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
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

app.include_router(ingest_router)
app.include_router(extract_router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "libretto-printer-api",
        "gemini_configured": settings.gemini_configured,
    }


@app.get("/config")
def public_config():
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
def quota_status(page_count: int | None = None):
    usage = usage_quota.get_usage()
    payload = {
        **usage,
    }
    if page_count is not None:
        payload.update(usage_quota.needs_over_quota_confirmation(page_count))
    return payload
