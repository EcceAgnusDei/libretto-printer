from pydantic import BaseModel


class PageInfo(BaseModel):
    index: int
    filename: str
    width: int
    height: int
    url: str


class CreateSessionResponse(BaseModel):
    session_id: str


class PdfUploadResponse(BaseModel):
    session_id: str
    source_name: str
    pdf_page_count: int


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
