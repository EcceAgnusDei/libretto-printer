from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.config import settings
from app.schemas.extract import (
    ExtractStartResponse,
    ExtractStatusResponse,
    ExtractWaitInfo,
    LibrettoResponse,
)
from app.services import extract as extract_service
from app.services import rate_limit

router = APIRouter(prefix="/extract", tags=["extract"])


def _rate_limit_extract_start(request: Request) -> None:
    rate_limit.enforce(
        request,
        "extract_start",
        settings.rate_limit_extract_start_max,
    )


def _wait_from_job(job: dict) -> ExtractWaitInfo | None:
    wait = job.get("wait")
    if not isinstance(wait, dict) or not wait.get("reason"):
        return None
    source = wait.get("source")
    return ExtractWaitInfo(
        reason=wait["reason"],
        message=wait.get("message") or "",
        wait_seconds=wait.get("wait_seconds"),
        wait_until=wait.get("wait_until"),
        source=source if source in {"server", "gemini"} else None,
    )


def _batch_size_from_job(job: dict) -> int | None:
    value = job.get("batch_size", job.get("extract_batch_size"))
    return int(value) if value is not None else None


def _start_like_response(job: dict) -> ExtractStartResponse:
    return ExtractStartResponse(
        session_id=job["session_id"],
        status=job["status"],
        source_name=job["source_name"],
        pdf_page_count=job["pdf_page_count"],
        batch_size=_batch_size_from_job(job) or 5,
        total_batches=job["total_batches"],
        pages_done=job["pages_done"],
        current_batch=job["current_batch"],
        wait=_wait_from_job(job),
    )


@router.post(
    "/sessions/{session_id}/start",
    response_model=ExtractStartResponse,
    dependencies=[Depends(_rate_limit_extract_start)],
)
def start_extract(session_id: str):
    return _start_like_response(extract_service.start_job(session_id))


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
        batch_size=_batch_size_from_job(job),
        total_batches=job.get("total_batches"),
        pages_done=job.get("pages_done"),
        current_batch=job.get("current_batch"),
        error=job.get("error"),
        wait=_wait_from_job(job),
        stale=bool(job.get("stale")),
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
