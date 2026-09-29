from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ExtractStatus = Literal["started", "pass1", "pass2", "done", "error"]


class ExtractStartResponse(BaseModel):
    session_id: str
    status: ExtractStatus
    source_name: str
    pdf_page_count: int


class ExtractStatusResponse(BaseModel):
    session_id: str
    status: ExtractStatus
    source_name: str
    pdf_page_count: int | None
    error: str | None = None
    created_at: str
    updated_at: str
