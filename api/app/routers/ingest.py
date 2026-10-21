from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.config import settings
from app.schemas.ingest import (
    CreateSessionResponse,
    PageInfo,
    PdfRenderBatchResponse,
    PdfUploadResponse,
    SessionDetailResponse,
)
from app.services import extract as extract_service
from app.services import ingest as ingest_service
from app.services import rate_limit

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _rate_limit_upload(request: Request) -> None:
    rate_limit.enforce(
        request,
        "upload",
        settings.rate_limit_upload_max,
    )


@router.post(
    "/sessions",
    response_model=CreateSessionResponse,
    dependencies=[Depends(_rate_limit_upload)],
)
async def create_session(source_name: str = Form(default="")):
    meta = ingest_service.create_session(source_name=source_name)
    return CreateSessionResponse(session_id=meta.session_id)


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str, offset: int = 0, limit: int = 5):
    meta = ingest_service.get_session(session_id)
    limit = min(max(limit, 1), settings.batch_size)
    pages = ingest_service.list_pages(session_id, offset=offset, limit=limit)
    return SessionDetailResponse(
        session_id=meta.session_id,
        source_name=meta.source_name,
        page_count=meta.page_count,
        pdf_page_count=meta.pdf_page_count,
        rendered_count=meta.rendered_count,
        done=meta.done,
        pages=[PageInfo(**p) for p in pages],
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str):
    extract_service.request_cancel(session_id)
    try:
        ingest_service.delete_session(session_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise


@router.post(
    "/sessions/{session_id}/pdf",
    response_model=PdfUploadResponse,
    dependencies=[Depends(_rate_limit_upload)],
)
async def upload_pdf(
    session_id: str,
    file: UploadFile = File(...),
):
    meta = await ingest_service.attach_pdf(session_id, file)
    return PdfUploadResponse(
        session_id=meta.session_id,
        source_name=meta.source_name,
        pdf_page_count=meta.pdf_page_count or 0,
    )


@router.post(
    "/sessions/{session_id}/pdf/render",
    response_model=PdfRenderBatchResponse,
)
async def render_pdf_batch(session_id: str, start_page: int | None = None):
    meta, appended = ingest_service.render_pdf_batch(session_id, start_page=start_page)
    return PdfRenderBatchResponse(
        session_id=meta.session_id,
        start_page=appended[0]["index"] if appended else meta.rendered_count,
        appended=[PageInfo(**p) for p in appended],
        rendered_count=meta.rendered_count,
        pdf_page_count=meta.pdf_page_count or 0,
        done=meta.done,
        batch_size=settings.batch_size,
    )


@router.get("/sessions/{session_id}/pages/{filename}")
async def get_page_image(session_id: str, filename: str):
    path = ingest_service.get_page_path(session_id, filename)
    return FileResponse(path, media_type="image/png")
