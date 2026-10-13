from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.config import settings
from app.services import gemini_client
from app.services import ingest as ingest_service
from app.services import usage_quota
from app.services.gemini_errors import (
    GeminiDailyQuotaError,
    MSG_DAILY_QUOTA,
    QuotaSignal,
    wait_until_iso,
)

logger = logging.getLogger(__name__)

# running | paused | done | error — plus de "started" (fusionné dans running)
ACTIVE_STATUSES = frozenset({"running"})
RESUMABLE_STATUSES = frozenset({"paused"})
_VALID_STATUSES = frozenset({"running", "paused", "done", "error"})
_H2_RE = re.compile(r"(?m)^##[ \t]")
_worker_lock = threading.Lock()
_running_sessions: set[str] = set()
_last_gemini_call_at: float | None = None
_pace_lock = threading.Lock()


def _normalize_job(job: dict) -> dict:
    """Compat : started → running ; anciens pass1/pass2 → done/error."""
    status = job.get("status")
    if status == "started":
        job = dict(job)
        job["status"] = "running"
        return job
    if status in _VALID_STATUSES:
        return job
    job = dict(job)
    pages_done = int(job.get("pages_done") or 0)
    page_count = int(job.get("pdf_page_count") or 0)
    if status in {"pass1", "pass2"} and page_count > 0 and pages_done >= page_count:
        job["status"] = "done"
    else:
        job["status"] = "error"
        if not job.get("error"):
            job["error"] = (
                f"Ancien statut '{status}' incompatible — "
                "relancer POST /extract/sessions/{id}/start"
            )
    return job


def _job_path(session_id: str) -> Path:
    return settings.sessions_dir / session_id / "extract_job.json"


def _libretto_path(session_id: str) -> Path:
    return settings.sessions_dir / session_id / "libretto.md"


def _page_png_path(session_id: str, page_index: int) -> Path:
    return (
        settings.sessions_dir
        / session_id
        / "pages"
        / f"page_{page_index:04d}.png"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_job(session_id: str) -> dict | None:
    path = _job_path(session_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _save_job(job: dict) -> None:
    job["updated_at"] = _now_iso()
    path = _job_path(job["session_id"])
    path.write_text(
        json.dumps(job, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _total_batches(page_count: int, batch_size: int) -> int:
    if page_count <= 0:
        return 0
    return math.ceil(page_count / batch_size)


def split_current_section(markdown: str) -> tuple[str, str]:
    """Découpe prefix + section depuis le dernier titre ##."""
    matches = list(_H2_RE.finditer(markdown))
    if not matches:
        return "", markdown
    idx = matches[-1].start()
    return markdown[:idx], markdown[idx:]


def read_libretto(session_id: str) -> str:
    path = _libretto_path(session_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_libretto(session_id: str, content: str) -> None:
    text = content.strip()
    if text:
        text += "\n"
    _libretto_path(session_id).write_text(text, encoding="utf-8")


def apply_section_update(markdown: str, updated_section: str) -> str:
    prefix, _section = split_current_section(markdown)
    updated = updated_section.strip()
    if not updated:
        return markdown
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return f"{prefix}{updated}\n"


def assert_preconditions(session_id: str) -> ingest_service.SessionMeta:
    """Vérifie que l'extraction peut démarrer."""
    ingest_service.assert_safe_session_id(session_id)

    if not settings.gemini_configured:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY non configurée",
        )

    meta = ingest_service.get_session(session_id)
    ingest_service.require_source_pdf(session_id)
    ingest_service.require_pages_rendered(session_id)

    if meta.pdf_page_count is None or meta.pdf_page_count < 1:
        raise HTTPException(
            status_code=400,
            detail="Aucune page à extraire",
        )

    existing = _load_job(session_id)
    if existing:
        normalized = _normalize_job(existing)
        if normalized.get("status") in ACTIVE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"Extraction déjà en cours (status={normalized['status']})",
            )

    return meta


def start_job(session_id: str) -> dict:
    """Ouvre le job et lance la passe unique en arrière-plan."""
    meta = assert_preconditions(session_id)
    assert meta.pdf_page_count is not None

    batch_size = settings.batch_size
    page_count = meta.pdf_page_count
    now = _now_iso()

    write_libretto(session_id, "")

    job = {
        "session_id": session_id,
        "status": "running",
        "source_name": meta.source_name,
        "pdf_page_count": page_count,
        "batch_size": batch_size,
        "total_batches": _total_batches(page_count, batch_size),
        "pages_done": 0,
        "current_batch": 0,
        "error": None,
        "wait": None,
        "created_at": now,
        "updated_at": now,
    }
    _save_job(job)
    _spawn_worker(session_id)
    return job


def resume_job(session_id: str) -> dict:
    """Reprend une extraction pausée (quota journalier) sans effacer le progrès."""
    assert_preconditions(session_id)

    job = _load_job(session_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Aucune extraction démarrée pour cette session",
        )
    job = _normalize_job(job)

    if job.get("status") in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Extraction déjà en cours (status={job['status']})",
        )

    if not _is_daily_resumable(job):
        raise HTTPException(
            status_code=400,
            detail=(
                "Aucune pause quota journalier à reprendre "
                f"(status={job.get('status')})"
            ),
        )

    pages_done = int(job.get("pages_done") or 0)
    page_count = int(job.get("pdf_page_count") or 0)
    if page_count < 1 or pages_done >= page_count:
        raise HTTPException(
            status_code=400,
            detail="Extraction déjà terminée — rien à reprendre",
        )

    wait = job.get("wait") if isinstance(job.get("wait"), dict) else {}
    source = wait.get("source")
    # Quota serveur (ou legacy sans source) : re-vérifier le plafond AVANT running.
    # source=gemini : ne pas bloquer sur le compteur local (branche distincte).
    if source != "gemini":
        try:
            usage_quota.assert_under_daily_limit()
        except GeminiDailyQuotaError:
            job["status"] = "paused"
            job["error"] = MSG_DAILY_QUOTA
            job["wait"] = {
                "reason": "daily",
                "message": MSG_DAILY_QUOTA,
                "wait_seconds": None,
                "wait_until": usage_quota.next_reset_iso(),
                "source": "server",
            }
            _save_job(job)
            raise HTTPException(status_code=429, detail=MSG_DAILY_QUOTA)

    job["status"] = "running"
    job["error"] = None
    job["wait"] = None
    _save_job(job)
    _spawn_worker(session_id)
    return job


def _set_job_wait(session_id: str, signal: QuotaSignal | None) -> None:
    job = _load_job(session_id)
    if job is None:
        return
    if signal is None:
        job["wait"] = None
    else:
        wait_seconds = signal.wait_seconds
        job["wait"] = {
            "reason": signal.kind,
            "message": signal.message,
            "wait_seconds": wait_seconds,
            "wait_until": (
                wait_until_iso(wait_seconds)
                if wait_seconds is not None and wait_seconds > 0
                else None
            ),
            "source": signal.source,
        }
        if signal.kind == "daily":
            job["status"] = "paused"
            job["error"] = MSG_DAILY_QUOTA
    _save_job(job)


def _is_daily_resumable(job: dict) -> bool:
    """paused, ou ancien error+daily encore présent sur disque."""
    status = job.get("status")
    if status in RESUMABLE_STATUSES:
        return True
    wait = job.get("wait")
    if not isinstance(wait, dict):
        return False
    return status == "error" and wait.get("reason") == "daily"


def _pause_for_daily_quota(
    session_id: str,
    job: dict,
    exc: GeminiDailyQuotaError,
) -> None:
    source = exc.source
    wait_until = (
        usage_quota.next_reset_iso() if source == "server" else None
    )
    job["status"] = "paused"
    job["error"] = MSG_DAILY_QUOTA
    job["wait"] = {
        "reason": "daily",
        "message": MSG_DAILY_QUOTA,
        "wait_seconds": None,
        "wait_until": wait_until,
        "source": source,
    }
    _save_job(job)
    logger.warning(
        "Extract daily quota session=%s source=%s: %s",
        session_id[:8],
        source,
        exc,
    )


def _pace_before_gemini(_session_id: str) -> None:
    """Respecte gemini_min_interval_seconds entre deux appels (pace silencieuse)."""
    global _last_gemini_call_at
    interval = settings.gemini_min_interval_seconds
    if interval <= 0:
        return

    with _pace_lock:
        now = time.monotonic()
        last = _last_gemini_call_at
        delay = 0.0
        if last is not None:
            delay = interval - (now - last)
        if delay > 0.05:
            time.sleep(delay)
        _last_gemini_call_at = time.monotonic()


def get_job(session_id: str) -> dict:
    ingest_service.assert_safe_session_id(session_id)
    ingest_service.get_session(session_id)
    job = _load_job(session_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Aucune extraction démarrée pour cette session",
        )
    return _normalize_job(job)


def get_libretto(session_id: str) -> str:
    ingest_service.assert_safe_session_id(session_id)
    ingest_service.get_session(session_id)
    if _load_job(session_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Aucune extraction démarrée pour cette session",
        )
    return read_libretto(session_id)


def _spawn_worker(session_id: str) -> None:
    with _worker_lock:
        if session_id in _running_sessions:
            return
        _running_sessions.add(session_id)

    thread = threading.Thread(
        target=_worker_entry,
        args=(session_id,),
        name=f"extract-{session_id[:8]}",
        daemon=True,
    )
    thread.start()


def _worker_entry(session_id: str) -> None:
    try:
        run_extraction(session_id)
    finally:
        with _worker_lock:
            _running_sessions.discard(session_id)


def run_extraction(session_id: str) -> None:
    """Passe unique : lots de N pages → Gemini → mise à jour libretto.md."""
    job = _load_job(session_id)
    if job is None:
        return

    page_count = int(job["pdf_page_count"])
    batch_size = int(job.get("batch_size") or job.get("extract_batch_size") or settings.batch_size)
    pages_done = int(job.get("pages_done") or 0)

    job["status"] = "running"
    job["error"] = None
    job["wait"] = None
    _save_job(job)

    def on_wait(signal: QuotaSignal | None) -> None:
        _set_job_wait(session_id, signal)

    try:
        while pages_done < page_count:
            start = pages_done
            end = min(start + batch_size, page_count)
            batch_index = (start // batch_size) + 1
            page_indices = list(range(start, end))
            image_paths = [_page_png_path(session_id, i) for i in page_indices]
            missing = [str(p) for p in image_paths if not p.is_file()]
            if missing:
                raise FileNotFoundError(
                    f"Pages PNG manquantes pour le lot {batch_index}: {missing[:3]}"
                )

            current = read_libretto(session_id)
            _prefix, section = split_current_section(current)

            logger.info(
                "Extract session=%s batch=%s pages=%s-%s section_chars=%s",
                session_id[:8],
                batch_index,
                start + 1,
                end,
                len(section),
            )

            _pace_before_gemini(session_id)
            usage_quota.reserve_batch()
            updated_section = gemini_client.extract_libretto_markdown(
                image_paths,
                section,
                on_wait=on_wait,
            )
            new_md = apply_section_update(current, updated_section)
            write_libretto(session_id, new_md)

            pages_done = end
            job = _load_job(session_id) or job
            job["status"] = "running"
            job["pages_done"] = pages_done
            job["current_batch"] = batch_index
            job["error"] = None
            job["wait"] = None
            _save_job(job)

        job = _load_job(session_id) or job
        job["status"] = "done"
        job["pages_done"] = page_count
        job["current_batch"] = int(job.get("total_batches") or 0)
        job["error"] = None
        job["wait"] = None
        _save_job(job)
        logger.info("Extract done session=%s", session_id[:8])
    except GeminiDailyQuotaError as exc:
        job = _load_job(session_id) or job
        _pause_for_daily_quota(session_id, job, exc)
    except Exception as exc:  # noqa: BLE001 — stocke l'erreur dans le job
        logger.exception("Extract failed session=%s", session_id[:8])
        job = _load_job(session_id) or job
        job["status"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"[:500]
        job["wait"] = None
        _save_job(job)
