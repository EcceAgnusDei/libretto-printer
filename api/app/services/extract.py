from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from fastapi import HTTPException

from app.config import settings
from app.services import gemini_client
from app.services import ingest as ingest_service
from app.services import usage_quota
from app.services.gemini_errors import (
    GeminiDailyQuotaError,
    MSG_DAILY_QUOTA,
    MSG_RATE_LIMIT,
    MSG_RESUME_COOLDOWN,
    QuotaSignal,
    next_gemini_reset_iso,
    parse_wait_until,
    wait_until_iso,
)

logger = logging.getLogger(__name__)

JobDict = dict[str, object]

# running | paused | done | error — plus de "started" (fusionné dans running)
ACTIVE_STATUSES = frozenset({"running"})
RESUMABLE_STATUSES = frozenset({"paused"})
_VALID_STATUSES = frozenset({"running", "paused", "done", "error"})
_H2_RE = re.compile(r"(?m)^##[ \t]")
_worker_lock = threading.Lock()
_running_sessions: set[str] = set()
_last_gemini_call_at: float | None = None
_pace_lock = threading.Lock()
_resume_lock = threading.Lock()
_resume_last_at: dict[str, float] = {}


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _normalize_job(job: JobDict) -> JobDict:
    """Compat : started → running ; anciens pass1/pass2 → done/error."""
    status = job.get("status")
    if status == "started":
        job = dict(job)
        job["status"] = "running"
        return job
    if status in _VALID_STATUSES:
        return job
    job = dict(job)
    pages_done = _as_int(job.get("pages_done"))
    page_count = _as_int(job.get("pdf_page_count"))
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


def _load_job(session_id: str) -> JobDict | None:
    path = _job_path(session_id)
    if not path.exists():
        return None
    try:
        loaded = cast(object, json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    job: JobDict = {}
    for key, value in cast(dict[object, object], loaded).items():
        job[str(key)] = value
    return job


def _save_job(job: JobDict) -> None:
    job["updated_at"] = _now_iso()
    path = _job_path(str(job["session_id"]))
    _ = path.write_text(
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
    _ = _libretto_path(session_id).write_text(text, encoding="utf-8")


def apply_section_update(markdown: str, updated_section: str) -> str:
    prefix, _section = split_current_section(markdown)
    updated = updated_section.strip()
    if not updated:
        return markdown
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return f"{prefix}{updated}\n"


def is_worker_alive(session_id: str) -> bool:
    with _worker_lock:
        return session_id in _running_sessions


def job_is_stale(job: JobDict) -> bool:
    """running sur disque mais plus de thread worker (ex. restart API)."""
    return job.get("status") == "running" and not is_worker_alive(
        str(job["session_id"])
    )


def assert_preconditions(
    session_id: str,
    *,
    allow_active: bool = False,
) -> ingest_service.SessionMeta:
    """Vérifie que l'extraction peut démarrer."""
    _ = ingest_service.assert_safe_session_id(session_id)

    if not settings.gemini_configured:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY non configurée",
        )

    meta = ingest_service.get_session(session_id)
    _ = ingest_service.require_source_pdf(session_id)
    _ = ingest_service.require_pages_rendered(session_id)

    if meta.pdf_page_count is None or meta.pdf_page_count < 1:
        raise HTTPException(
            status_code=400,
            detail="Aucune page à extraire",
        )

    existing = _load_job(session_id)
    if existing and not allow_active:
        normalized = _normalize_job(existing)
        if normalized.get("status") in ACTIVE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"Extraction déjà en cours (status={normalized['status']})",
            )

    return meta


def start_job(session_id: str) -> JobDict:
    """Ouvre le job et lance la passe unique en arrière-plan."""
    meta = assert_preconditions(session_id)
    assert meta.pdf_page_count is not None

    batch_size = settings.batch_size
    page_count = meta.pdf_page_count
    now = _now_iso()

    write_libretto(session_id, "")

    job: JobDict = {
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


def _assert_resume_cooldown(session_id: str) -> None:
    """Anti-spam /resume (cooldown serveur, pas contrôlé par le client)."""
    cooldown = settings.extract_resume_cooldown_seconds
    if cooldown <= 0:
        return
    now = time.monotonic()
    with _resume_lock:
        last = _resume_last_at.get(session_id)
        if last is not None and (now - last) < cooldown:
            raise HTTPException(status_code=429, detail=MSG_RESUME_COOLDOWN)
        _resume_last_at[session_id] = now


def _reject_if_wait_not_elapsed(job: JobDict) -> None:
    """Refuse la reprise/réattache tant que wait_until (disque) n'est pas passé."""
    raw_wait = job.get("wait")
    if not isinstance(raw_wait, dict):
        return
    wait = cast(JobDict, raw_wait)
    raw_until = wait.get("wait_until")
    until = parse_wait_until(raw_until if isinstance(raw_until, str) else None)
    if until is None or datetime.now(timezone.utc) >= until:
        return
    reason = wait.get("reason")
    detail = MSG_DAILY_QUOTA if reason == "daily" else MSG_RATE_LIMIT
    raise HTTPException(status_code=429, detail=detail)


def resume_job(session_id: str) -> JobDict:
    """Reprend une pause daily, ou réattache un worker orphelin (running stale)."""
    _ = assert_preconditions(session_id, allow_active=True)
    _assert_resume_cooldown(session_id)

    job = _load_job(session_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Aucune extraction démarrée pour cette session",
        )
    job = _normalize_job(job)

    if job.get("status") in ACTIVE_STATUSES:
        if is_worker_alive(session_id):
            raise HTTPException(
                status_code=409,
                detail=f"Extraction déjà en cours (status={job['status']})",
            )
        _reject_if_wait_not_elapsed(job)
        _spawn_worker(session_id)
        logger.info("Extract reattach orphan worker session=%s", session_id[:8])
        return job

    if not _is_daily_resumable(job):
        raise HTTPException(
            status_code=400,
            detail=(
                "Aucune pause quota journalier à reprendre "
                f"(status={job.get('status')})"
            ),
        )

    pages_done = _as_int(job.get("pages_done"))
    page_count = _as_int(job.get("pdf_page_count"))
    if page_count < 1 or pages_done >= page_count:
        raise HTTPException(
            status_code=400,
            detail="Extraction déjà terminée — rien à reprendre",
        )

    raw_wait = job.get("wait")
    wait: JobDict = cast(JobDict, raw_wait) if isinstance(raw_wait, dict) else {}
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
    else:
        raw_until = wait.get("wait_until")
        until = parse_wait_until(
            raw_until if isinstance(raw_until, str) else None
        )
        if until is not None and datetime.now(timezone.utc) < until:
            job["status"] = "paused"
            job["error"] = MSG_DAILY_QUOTA
            job["wait"] = {
                "reason": "daily",
                "message": MSG_DAILY_QUOTA,
                "wait_seconds": None,
                "wait_until": until.astimezone(timezone.utc).isoformat(),
                "source": "gemini",
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


def _is_daily_resumable(job: JobDict) -> bool:
    """paused, ou ancien error+daily encore présent sur disque."""
    status = job.get("status")
    if status in RESUMABLE_STATUSES:
        return True
    raw_wait = job.get("wait")
    if not isinstance(raw_wait, dict):
        return False
    wait = cast(JobDict, raw_wait)
    return status == "error" and wait.get("reason") == "daily"


def _pause_for_daily_quota(
    session_id: str,
    job: JobDict,
    exc: GeminiDailyQuotaError,
) -> None:
    source = exc.source
    if source == "server":
        wait_until = usage_quota.next_reset_iso()
    elif source == "gemini":
        wait_until = exc.wait_until or next_gemini_reset_iso()
    else:
        wait_until = None
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
        "Extract daily quota session=%s source=%s quota_id=%s wait_until=%s",
        session_id[:8],
        source,
        getattr(exc, "raw_quota_id", None),
        wait_until,
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


def get_job(session_id: str) -> JobDict:
    _ = ingest_service.assert_safe_session_id(session_id)
    _ = ingest_service.get_session(session_id)
    job = _load_job(session_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Aucune extraction démarrée pour cette session",
        )
    normalized = _normalize_job(job)
    normalized["stale"] = job_is_stale(normalized)
    return normalized


def get_libretto(session_id: str) -> str:
    _ = ingest_service.assert_safe_session_id(session_id)
    _ = ingest_service.get_session(session_id)
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

    page_count = _as_int(job.get("pdf_page_count"))
    batch_size = _as_int(
        job.get("batch_size") or job.get("extract_batch_size"),
        settings.batch_size,
    )
    pages_done = _as_int(job.get("pages_done"))

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
        job["current_batch"] = _as_int(job.get("total_batches"))
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
