from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse

from app.config import settings
from app.schemas.ingest import (
    BatchAppendResponse,
    CreateSessionResponse,
    PageInfo,
    PdfRenderBatchResponse,
    PdfUploadResponse,
    SessionDetailResponse,
)
from app.services import ingest as ingest_service

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(source_name: str = Form(default="")):
    meta = ingest_service.create_session(source_name=source_name)
    return CreateSessionResponse(session_id=meta.session_id)


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str, offset: int = 0, limit: int = 5):
    meta = ingest_service.get_session(session_id)
    limit = min(max(limit, 1), settings.ingest_batch_size)
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


@router.post("/sessions/{session_id}/pages", response_model=BatchAppendResponse)
async def append_pages(
    session_id: str,
    files: list[UploadFile] = File(...),
):
    meta, appended = await ingest_service.append_image_batch(session_id, files)
    return BatchAppendResponse(
        session_id=meta.session_id,
        appended=[PageInfo(**p) for p in appended],
        page_count=meta.page_count,
        batch_size=settings.ingest_batch_size,
    )


@router.post("/sessions/{session_id}/pdf", response_model=PdfUploadResponse)
async def upload_pdf(
    session_id: str,
    file: UploadFile = File(...),
):
    meta, thumbnail = await ingest_service.attach_pdf(session_id, file)
    return PdfUploadResponse(
        session_id=meta.session_id,
        source_name=meta.source_name,
        pdf_page_count=meta.pdf_page_count or 0,
        thumbnail_url=thumbnail["url"],
        thumbnail_width=thumbnail["width"],
        thumbnail_height=thumbnail["height"],
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
        batch_size=settings.ingest_batch_size,
    )


@router.get("/sessions/{session_id}/pages/{filename}")
async def get_page_image(session_id: str, filename: str):
    path = ingest_service.get_page_path(session_id, filename)
    return FileResponse(path, media_type="image/png")
