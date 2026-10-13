from __future__ import annotations

import json
import math
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import settings
from app.services.gemini_errors import GeminiDailyQuotaError, MSG_DAILY_QUOTA

_lock = threading.Lock()


def _usage_path() -> Path:
    return settings.data_dir / "gemini_usage.json"


def _today() -> str:
    return date.today().isoformat()


def _load_unlocked() -> dict:
    path = _usage_path()
    if not path.exists():
        return {"date": _today(), "batches": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"date": _today(), "batches": 0}
    if data.get("date") != _today():
        return {"date": _today(), "batches": 0}
    # Compat anciens fichiers "requests"
    batches = data.get("batches")
    if batches is None:
        batches = data.get("requests") or 0
    return {
        "date": _today(),
        "batches": int(batches),
    }


def _save_unlocked(data: dict) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = _usage_path()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def batches_needed(page_count: int, batch_size: int | None = None) -> int:
    size = batch_size if batch_size is not None else settings.batch_size
    if page_count <= 0 or size <= 0:
        return 0
    return math.ceil(page_count / size)


def get_usage() -> dict:
    with _lock:
        data = _load_unlocked()
    limit = settings.gemini_max_batches_per_day
    used = int(data["batches"])
    if limit <= 0:
        remaining = None
    else:
        remaining = max(0, limit - used)
    return {
        "date": data["date"],
        "batches_used": used,
        "batches_limit": limit,
        "batches_remaining": remaining,
        "batch_size": settings.batch_size,
    }


def assert_under_daily_limit() -> None:
    """Lève GeminiDailyQuotaError si le plafond journalier de lots est atteint."""
    limit = settings.gemini_max_batches_per_day
    if limit <= 0:
        return
    with _lock:
        data = _load_unlocked()
        if data["batches"] >= limit:
            raise GeminiDailyQuotaError(MSG_DAILY_QUOTA, source="server")


def reserve_batch() -> None:
    """Réserve un lot de façon atomique (vérifie + incrémente sous le même verrou)."""
    limit = settings.gemini_max_batches_per_day
    with _lock:
        data = _load_unlocked()
        if limit > 0 and data["batches"] >= limit:
            raise GeminiDailyQuotaError(MSG_DAILY_QUOTA, source="server")
        data["batches"] = int(data["batches"]) + 1
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save_unlocked(data)


def record_batch() -> None:
    """Incrémente le compteur journalier d'un lot Gemini consommé (sans vérifier le plafond)."""
    with _lock:
        data = _load_unlocked()
        data["batches"] = int(data["batches"]) + 1
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save_unlocked(data)


def needs_over_quota_confirmation(page_count: int) -> dict:
    """Indique si le PDF dépasse le reste de lots journaliers."""
    usage = get_usage()
    needed = batches_needed(page_count)
    remaining = usage["batches_remaining"]
    limit = usage["batches_limit"]
    requires = (
        limit > 0
        and remaining is not None
        and needed > remaining
    )
    return {
        "requires_confirmation": requires,
        "batches_needed": needed,
        "batches_remaining": remaining,
        "batches_limit": limit,
        "batches_used": usage["batches_used"],
        "batch_size": usage["batch_size"],
        "page_count": page_count,
        "message": (
            (
                f"Ce PDF nécessite {needed} lot(s), mais il ne reste que "
                f"{remaining} lot(s) sur votre quota journalier ({limit}). "
                "L'extraction ne pourra pas se finir aujourd'hui. "
                "Continuer quand même ?"
            )
            if requires
            else None
        ),
    }
