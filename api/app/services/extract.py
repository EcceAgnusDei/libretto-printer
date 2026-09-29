from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.config import settings
from app.services import ingest as ingest_service

ACTIVE_STATUSES = frozenset({"started", "pass1", "pass2"})


def _job_path(session_id: str) -> Path:
    return settings.sessions_dir / session_id / "extract_job.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_job(session_id: str) -> dict | None:
    path = _job_path(session_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _save_job(job: dict) -> None:
    path = _job_path(job["session_id"])
    path.write_text(
        json.dumps(job, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def start_job(session_id: str) -> dict:
    if not settings.gemini_configured:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY non configurée",
        )

    meta = ingest_service.get_session(session_id)
    ingest_service.require_source_pdf(session_id)

    existing = _load_job(session_id)
    if existing and existing.get("status") in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Extraction déjà en cours (status={existing['status']})",
        )

    now = _now_iso()
    job = {
        "session_id": session_id,
        "status": "started",
        "source_name": meta.source_name,
        "pdf_page_count": meta.pdf_page_count,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    _save_job(job)
    return job


def get_job(session_id: str) -> dict:
    ingest_service.get_session(session_id)
    job = _load_job(session_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Aucune extraction démarrée pour cette session",
        )
    return job
