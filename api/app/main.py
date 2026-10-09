from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers.extract import router as extract_router
from app.routers.ingest import router as ingest_router

app = FastAPI(title="libretto-printer API", version="0.1.0")

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
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "max_pages": settings.max_pages_per_session,
        "ingest_batch_size": settings.ingest_batch_size,
        "extract_batch_size": settings.extract_batch_size,
    }
