from __future__ import annotations

from fastapi import APIRouter

from app.schemas.extract import (
    ExtractStartResponse,
    ExtractStatusResponse,
    LibrettoResponse,
)
from app.services import extract as extract_service

router = APIRouter(prefix="/extract", tags=["extract"])


@router.post(
    "/sessions/{session_id}/start",
    response_model=ExtractStartResponse,
)
def start_extract(session_id: str):
    job = extract_service.start_job(session_id)
    return ExtractStartResponse(
        session_id=job["session_id"],
        status=job["status"],
        source_name=job["source_name"],
        pdf_page_count=job["pdf_page_count"],
        extract_batch_size=job["extract_batch_size"],
        total_batches=job["total_batches"],
        pages_done=job["pages_done"],
        current_batch=job["current_batch"],
    )


@router.get(
    "/sessions/{session_id}/status",
    response_model=ExtractStatusResponse,
)
def extract_status(session_id: str):
    job = extract_service.get_job(session_id)
    return ExtractStatusResponse(
        session_id=job["session_id"],
        status=job["status"],
        source_name=job["source_name"],
        pdf_page_count=job.get("pdf_page_count"),
        extract_batch_size=job.get("extract_batch_size"),
        total_batches=job.get("total_batches"),
        pages_done=job.get("pages_done"),
        current_batch=job.get("current_batch"),
        error=job.get("error"),
        created_at=job["created_at"],
        updated_at=job["updated_at"],
    )


@router.get(
    "/sessions/{session_id}/libretto",
    response_model=LibrettoResponse,
)
def extract_libretto(session_id: str):
    job = extract_service.get_job(session_id)
    markdown = extract_service.get_libretto(session_id)
    return LibrettoResponse(
        session_id=job["session_id"],
        status=job["status"],
        markdown=markdown,
    )
