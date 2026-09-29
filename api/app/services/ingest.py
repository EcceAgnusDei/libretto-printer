from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.config import settings

ALLOWED_PDF_TYPES = frozenset(
    {
        "application/pdf",
        "application/x-pdf",
        "application/acrobat",
        "applications/vnd.pdf",
        "text/pdf",
        "text/x-pdf",
    }
)
PDF_MAGIC = b"%PDF"
SESSION_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def assert_safe_session_id(session_id: str) -> str:
    """Reject path traversal and non-uuid session ids."""
    if not SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="session_id invalide")
    return session_id


@dataclass
class SessionMeta:
    session_id: str
    source_name: str
    page_count: int
    pdf_page_count: int | None
    rendered_count: int
    has_pdf: bool

    @property
    def done(self) -> bool:
        return (
            self.has_pdf
            and self.pdf_page_count is not None
            and self.rendered_count >= self.pdf_page_count
        )


def _session_dir(session_id: str) -> Path:
    assert_safe_session_id(session_id)
    base = settings.sessions_dir.resolve()
    path = (base / session_id).resolve()
    if not path.is_relative_to(base):
        raise HTTPException(status_code=400, detail="session_id invalide")
    return path


def _pages_dir(session_id: str) -> Path:
    return _session_dir(session_id) / "pages"


def _meta_path(session_id: str) -> Path:
    return _session_dir(session_id) / "meta.json"


def _pdf_path(session_id: str) -> Path:
    return _session_dir(session_id) / "source.pdf"


def _page_url(session_id: str, filename: str) -> str:
    return f"/ingest/sessions/{session_id}/pages/{filename}"


def _load_meta(session_id: str) -> SessionMeta:
    path = _meta_path(session_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session introuvable")
    data = json.loads(path.read_text(encoding="utf-8"))
    return SessionMeta(**data)


def _save_meta(meta: SessionMeta) -> None:
    path = _meta_path(meta.session_id)
    path.write_text(
        json.dumps(
            {
                "session_id": meta.session_id,
                "source_name": meta.source_name,
                "page_count": meta.page_count,
                "pdf_page_count": meta.pdf_page_count,
                "rendered_count": meta.rendered_count,
                "has_pdf": meta.has_pdf,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def create_session(source_name: str = "") -> SessionMeta:
    settings.sessions_dir.mkdir(parents=True, exist_ok=True)
    session_id = uuid.uuid4().hex
    directory = _session_dir(session_id)
    directory.mkdir(parents=True, exist_ok=False)
    _pages_dir(session_id).mkdir(parents=True, exist_ok=False)
    meta = SessionMeta(
        session_id=session_id,
        source_name=source_name or "untitled",
        page_count=0,
        pdf_page_count=None,
        rendered_count=0,
        has_pdf=False,
    )
    _save_meta(meta)
    return meta


def get_session(session_id: str) -> SessionMeta:
    return _load_meta(session_id)


def delete_session(session_id: str) -> None:
    directory = _session_dir(session_id)
    if not directory.exists():
        raise HTTPException(status_code=404, detail="Session introuvable")
    shutil.rmtree(directory)


def require_source_pdf(session_id: str) -> Path:
    """Ensure session meta says PDF and source.pdf exists on disk."""
    meta = _load_meta(session_id)
    if not meta.has_pdf or meta.pdf_page_count is None:
        raise HTTPException(
            status_code=400,
            detail="Aucun PDF attaché à cette session",
        )
    path = _pdf_path(session_id)
    if not path.is_file():
        raise HTTPException(
            status_code=400,
            detail="PDF source manquant sur le disque",
        )
    return path


def list_pages(session_id: str, offset: int = 0, limit: int | None = None) -> list[dict]:
    _load_meta(session_id)
    pages_dir = _pages_dir(session_id)
    files = sorted(pages_dir.glob("page_*.png"))
    if limit is None:
        selected = files[offset:]
    else:
        selected = files[offset : offset + limit]
    result: list[dict] = []
    for path in selected:
        index = int(path.stem.split("_")[1])
        with Image.open(path) as img:
            width, height = img.size
        result.append(
            {
                "index": index,
                "filename": path.name,
                "width": width,
                "height": height,
                "url": _page_url(session_id, path.name),
            }
        )
    return result


def get_page_path(session_id: str, filename: str) -> Path:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide")
    path = _pages_dir(session_id) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Page introuvable")
    return path


def _normalize_image(image: Image.Image) -> Image.Image:
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")
    max_edge = settings.max_image_edge
    width, height = image.size
    longest = max(width, height)
    if longest > max_edge:
        scale = max_edge / longest
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )
    return image


def _save_page_image(session_id: str, index: int, image: Image.Image) -> dict:
    image = _normalize_image(image)
    filename = f"page_{index:04d}.png"
    path = _pages_dir(session_id) / filename
    image.save(path, format="PNG", optimize=True)
    width, height = image.size
    return {
        "index": index,
        "filename": filename,
        "width": width,
        "height": height,
        "url": _page_url(session_id, filename),
    }


def _format_max_size(max_bytes: int) -> str:
    mo = max_bytes / (1024 * 1024)
    if mo >= 1:
        rounded = int(mo) if mo.is_integer() else round(mo, 1)
        return f"{rounded} Mo"
    ko = max_bytes / 1024
    rounded = int(ko) if ko.is_integer() else round(ko, 1)
    return f"{rounded} Ko"


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read upload in chunks; reject as soon as size exceeds the limit."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Fichier trop volumineux "
                    f"(max {_format_max_size(max_bytes)})"
                ),
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(
            status_code=400,
            detail=f"Fichier vide: {file.filename or 'upload'}",
        )
    return data


def _assert_pdf_payload(filename: str, content_type: str, raw: bytes) -> None:
    name = filename or "source.pdf"
    ctype = (content_type or "").lower().split(";")[0].strip()
    looks_pdf_name = name.lower().endswith(".pdf")
    looks_pdf_type = ctype in ALLOWED_PDF_TYPES or ctype == ""
    if not looks_pdf_name and not looks_pdf_type:
        raise HTTPException(status_code=400, detail="Seul un PDF est accepté")
    # ISO 32000: "%PDF" appears in the first 1024 bytes.
    if PDF_MAGIC not in raw[:1024]:
        raise HTTPException(
            status_code=400,
            detail="Ce fichier n'est pas un PDF valide (en-tête manquant)",
        )


def _open_and_validate_pdf(raw: bytes) -> int:
    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="PDF illisible") from exc

    try:
        if doc.needs_pass:
            raise HTTPException(
                status_code=400,
                detail="PDF protégé par mot de passe non supporté",
            )

        page_count = doc.page_count
        if page_count < 1:
            raise HTTPException(status_code=400, detail="PDF sans pages")
        if page_count > settings.max_pages_per_session:
            raise HTTPException(
                status_code=400,
                detail=(
                    "PDF trop long "
                    f"(max {settings.max_pages_per_session} pages)"
                ),
            )
        # Touch first page to catch truncated / corrupt trailers early.
        try:
            doc.load_page(0)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="PDF illisible") from exc
        return page_count
    finally:
        doc.close()


async def attach_pdf(session_id: str, file: UploadFile) -> SessionMeta:
    meta = _load_meta(session_id)
    if meta.has_pdf:
        raise HTTPException(
            status_code=400,
            detail="La session contient déjà un PDF",
        )
    filename = file.filename or "source.pdf"
    raw = await _read_upload(file, settings.max_upload_bytes)
    _assert_pdf_payload(filename, file.content_type or "", raw)
    page_count = _open_and_validate_pdf(raw)

    _pdf_path(session_id).write_bytes(raw)
    meta.source_name = filename
    meta.has_pdf = True
    meta.pdf_page_count = page_count
    meta.rendered_count = 0
    meta.page_count = 0
    _save_meta(meta)
    return meta


def require_pages_rendered(session_id: str) -> SessionMeta:
    """Ensure PDF pages were rendered to PNG before downstream steps."""
    meta = _load_meta(session_id)
    if not meta.has_pdf or meta.pdf_page_count is None:
        raise HTTPException(
            status_code=400,
            detail="Aucun PDF attaché à cette session",
        )
    if not meta.done:
        raise HTTPException(
            status_code=400,
            detail=(
                "Pages non rendues: "
                f"{meta.rendered_count}/{meta.pdf_page_count}. "
                "Appelez POST /ingest/sessions/{id}/pdf/render jusqu'à done=true"
            ),
        )
    pages = list(_pages_dir(session_id).glob("page_*.png"))
    if len(pages) < meta.pdf_page_count:
        raise HTTPException(
            status_code=400,
            detail="Pages PNG manquantes sur le disque; relancez le rendu",
        )
    return meta


def render_pdf_batch(session_id: str, start_page: int | None = None) -> tuple[SessionMeta, list[dict]]:
    meta = _load_meta(session_id)
    if not meta.has_pdf or meta.pdf_page_count is None:
        raise HTTPException(status_code=400, detail="Aucun PDF attaché à cette session")

    batch_size = settings.ingest_batch_size
    if start_page is None:
        start_page = meta.rendered_count
    if start_page < 0 or start_page >= meta.pdf_page_count:
        raise HTTPException(status_code=400, detail="start_page hors limites")
    if start_page != meta.rendered_count:
        raise HTTPException(
            status_code=400,
            detail=f"Rendez les pages dans l'ordre; prochain index attendu: {meta.rendered_count}",
        )

    end_page = min(start_page + batch_size, meta.pdf_page_count)
    pdf_path = _pdf_path(session_id)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF source introuvable")

    doc = pymupdf.open(pdf_path)
    appended: list[dict] = []
    try:
        zoom = settings.render_dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        for page_index in range(start_page, end_page):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            appended.append(_save_page_image(session_id, page_index, img))
    finally:
        doc.close()

    meta.rendered_count = end_page
    meta.page_count = end_page
    _save_meta(meta)
    return meta, appended
