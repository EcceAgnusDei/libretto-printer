from pydantic import BaseModel, Field


class PageInfo(BaseModel):
    index: int
    filename: str
    width: int
    height: int
    url: str


class CreateSessionResponse(BaseModel):
    session_id: str


class BatchAppendResponse(BaseModel):
    session_id: str
    appended: list[PageInfo]
    page_count: int
    batch_size: int = Field(description="Max pages accepted per request")


class PdfUploadResponse(BaseModel):
    session_id: str
    source_name: str
    pdf_page_count: int
    thumbnail_url: str
    thumbnail_width: int
    thumbnail_height: int


class PdfRenderBatchResponse(BaseModel):
    session_id: str
    start_page: int
    appended: list[PageInfo]
    rendered_count: int
    pdf_page_count: int
    done: bool
    batch_size: int


class SessionDetailResponse(BaseModel):
    session_id: str
    source_name: str
    page_count: int
    pdf_page_count: int | None
    rendered_count: int
    done: bool
    pages: list[PageInfo]
