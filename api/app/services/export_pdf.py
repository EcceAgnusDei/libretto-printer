from __future__ import annotations

import logging
import re
from html import escape
from pathlib import Path

import bleach
import markdown
from fastapi import HTTPException
from weasyprint import CSS, HTML

from app.config import settings
from app.services import ingest as ingest_service

logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
_PRINT_CSS_PATH = _ASSETS_DIR / "libretto_print.css"

_ALLOWED_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "p",
        "em",
        "strong",
        "ul",
        "ol",
        "li",
        "br",
        "blockquote",
        "hr",
    }
)
_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {}

_TITLE_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
_UNSAFE_FILENAME_RE = re.compile(r"[^\w\-.\u00C0-\u024F ]+", re.UNICODE)


def pdf_path(session_id: str) -> Path:
    return settings.sessions_dir / session_id / "libretto.pdf"


def extract_title(markdown_text: str, fallback: str) -> str:
    match = _TITLE_RE.search(markdown_text)
    if match:
        title = match.group(1).strip()
        if title:
            return title
    cleaned = fallback.strip() or "libretto"
    return cleaned


def sanitize_download_stem(name: str) -> str:
    cleaned = _UNSAFE_FILENAME_RE.sub("", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return "libretto"
    if cleaned.lower().endswith(".pdf"):
        cleaned = cleaned[: -len(".pdf")].rstrip(" .") or "libretto"
    return cleaned[:120]


def download_filename(markdown_text: str, source_name: str) -> str:
    title = extract_title(markdown_text, Path(source_name).stem)
    return f"{sanitize_download_stem(title)}.pdf"


def markdown_to_body_html(markdown_text: str) -> str:
    raw_html = markdown.markdown(
        markdown_text,
        extensions=["sane_lists", "nl2br"],
        output_format="html5",
    )
    return bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        strip=True,
    )


def build_document_html(markdown_text: str, title: str) -> str:
    body = markdown_to_body_html(markdown_text)
    if not body.strip():
        body = "<p></p>"
    safe_title = escape(title)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="fr">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        f"  <title>{safe_title}</title>\n"
        "</head>\n"
        f"<body>\n{body}\n</body>\n"
        "</html>\n"
    )


def render_pdf_bytes(markdown_text: str, title: str) -> bytes:
    html = build_document_html(markdown_text, title)
    stylesheets = [CSS(filename=str(_PRINT_CSS_PATH))]
    return HTML(string=html, base_url=str(_ASSETS_DIR)).write_pdf(
        stylesheets=stylesheets,
    )


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def is_libretto_pdf_ready(session_id: str) -> bool:
    path = pdf_path(session_id)
    if not path.is_file():
        return False
    try:
        return path.stat().st_size > 5 and path.read_bytes()[:5] == b"%PDF-"
    except OSError:
        return False


def clear_libretto_pdf(session_id: str) -> None:
    path = pdf_path(session_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove PDF session=%s", session_id[:8])
    stale_hash = path.with_suffix(path.suffix + ".sha256")
    try:
        stale_hash.unlink(missing_ok=True)
    except OSError:
        pass


def write_libretto_pdf(
    session_id: str,
    markdown_text: str,
    source_name: str,
) -> Path:
    """Génère libretto.pdf une fois (après relecture). Conservé jusqu'à purge session."""
    title = extract_title(markdown_text, Path(source_name).stem)
    logger.info("Export PDF session=%s", session_id[:8])
    pdf_bytes = render_pdf_bytes(markdown_text, title)
    if not pdf_bytes.startswith(b"%PDF"):
        raise RuntimeError("Échec de la génération du PDF")
    path = pdf_path(session_id)
    _atomic_write_bytes(path, pdf_bytes)
    return path


def get_libretto_pdf(
    session_id: str,
    *,
    markdown_text: str,
    source_name: str,
    pdf_ready: bool,
) -> tuple[Path, str]:
    """Sert le PDF déjà généré. Ne régénère pas."""
    _ = ingest_service.assert_safe_session_id(session_id)
    _ = ingest_service.get_session(session_id)
    path = pdf_path(session_id)
    if not pdf_ready or not is_libretto_pdf_ready(session_id):
        raise HTTPException(
            status_code=409,
            detail="Le PDF n'est pas encore prêt",
        )
    return path, download_filename(markdown_text, source_name)
