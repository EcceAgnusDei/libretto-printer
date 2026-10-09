from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ExtractStatus = Literal["started", "running", "done", "error"]


class ExtractStartResponse(BaseModel):
    session_id: str
    status: ExtractStatus
    source_name: str
    pdf_page_count: int
    extract_batch_size: int
    total_batches: int
    pages_done: int
    current_batch: int


class ExtractStatusResponse(BaseModel):
    session_id: str
    status: ExtractStatus
    source_name: str
    pdf_page_count: int | None
    extract_batch_size: int | None = None
    total_batches: int | None = None
    pages_done: int | None = None
    current_batch: int | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class LibrettoResponse(BaseModel):
    session_id: str
    status: ExtractStatus
    markdown: str
