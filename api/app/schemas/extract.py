from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ExtractStatus = Literal["running", "paused", "done", "error"]
WaitReason = Literal["rate_limit", "daily", "queue"]
WaitSource = Literal["server", "gemini"]


class ExtractWaitInfo(BaseModel):
    reason: WaitReason
    message: str
    wait_seconds: float | None = None
    wait_until: str | None = None
    source: WaitSource | None = None


class ExtractStartResponse(BaseModel):
    session_id: str
    status: ExtractStatus
    source_name: str
    pdf_page_count: int
    batch_size: int
    total_batches: int
    pages_done: int
    current_batch: int
    wait: ExtractWaitInfo | None = None


class ExtractStatusResponse(BaseModel):
    session_id: str
    status: ExtractStatus
    source_name: str
    pdf_page_count: int | None
    batch_size: int | None = None
    total_batches: int | None = None
    pages_done: int | None = None
    current_batch: int | None = None
    error: str | None = None
    wait: ExtractWaitInfo | None = None
    stale: bool = False
    created_at: str
    updated_at: str


class LibrettoResponse(BaseModel):
    session_id: str
    status: ExtractStatus
    markdown: str
