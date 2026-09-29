from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pymupdf
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.config import settings

ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}
ALLOWED_PDF_TYPE = "application/pdf"
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
        if self.has_pdf and self.pdf_page_count is not None:
            return self.rendered_count >= self.pdf_page_count
        return self.page_count > 0


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


def _thumbnail_path(session_id: str) -> Path:
    return _session_dir(session_id) / "thumbnail.png"


def _page_url(session_id: str, filename: str) -> str:
    return f"/ingest/sessions/{session_id}/pages/{filename}"


def _thumbnail_url(session_id: str) -> str:
    return f"/ingest/sessions/{session_id}/thumbnail"


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


def get_thumbnail_path(session_id: str) -> Path:
    _load_meta(session_id)
    path = _thumbnail_path(session_id)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Miniature introuvable")
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


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"Fichier vide: {file.filename}")
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Fichier trop volumineux (max {max_bytes} octets)",
        )
    return data


async def append_image_batch(
    session_id: str,
    files: list[UploadFile],
) -> tuple[SessionMeta, list[dict]]:
    meta = _load_meta(session_id)
    if meta.has_pdf:
        raise HTTPException(
            status_code=400,
            detail="Cette session contient un PDF; utilisez le rendu par lots",
        )
    batch_size = settings.ingest_batch_size
    if not files:
        raise HTTPException(status_code=400, detail="Aucun fichier fourni")
    if len(files) > batch_size:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {batch_size} pages par envoi",
        )
    if meta.page_count + len(files) > settings.max_pages_per_session:
        raise HTTPException(status_code=400, detail="Nombre de pages maximal dépassé")

    appended: list[dict] = []
    next_index = meta.page_count
    for upload in files:
        content_type = (upload.content_type or "").lower()
        if content_type not in ALLOWED_IMAGE_TYPES and not (
            upload.filename or ""
        ).lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            raise HTTPException(
                status_code=400,
                detail=f"Type non supporté: {upload.filename or content_type}",
            )
        raw = await _read_upload(upload, settings.max_upload_bytes)
        try:
            with Image.open(BytesIO(raw)) as img:
                img.load()
                page = _save_page_image(session_id, next_index, img.copy())
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Image illisible: {upload.filename}",
            ) from exc
        appended.append(page)
        next_index += 1

    if not meta.source_name or meta.source_name == "untitled":
        meta.source_name = files[0].filename or meta.source_name
    meta.page_count = next_index
    meta.rendered_count = next_index
    _save_meta(meta)
    return meta, appended


async def attach_pdf(session_id: str, file: UploadFile) -> tuple[SessionMeta, dict]:
    meta = _load_meta(session_id)
    if meta.page_count > 0 or meta.has_pdf:
        raise HTTPException(
            status_code=400,
            detail="La session contient déjà des pages ou un PDF",
        )
    content_type = (file.content_type or "").lower()
    filename = file.filename or "source.pdf"
    if content_type not in (ALLOWED_PDF_TYPE, "application/x-pdf", "") and not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Seul un PDF est accepté")

    raw = await _read_upload(file, settings.max_upload_bytes)
    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="PDF illisible") from exc

    try:
        page_count = doc.page_count
        if page_count < 1:
            raise HTTPException(status_code=400, detail="PDF sans pages")
        if page_count > settings.max_pages_per_session:
            raise HTTPException(status_code=400, detail="PDF trop long")

        zoom = settings.render_dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        thumb = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        thumb = _normalize_image(thumb)
        thumb.save(_thumbnail_path(session_id), format="PNG", optimize=True)
        thumb_info = {
            "width": thumb.size[0],
            "height": thumb.size[1],
            "url": _thumbnail_url(session_id),
        }
    finally:
        doc.close()

    _pdf_path(session_id).write_bytes(raw)
    meta.source_name = filename
    meta.has_pdf = True
    meta.pdf_page_count = page_count
    meta.rendered_count = 0
    meta.page_count = 0
    _save_meta(meta)
    return meta, thumb_info


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
