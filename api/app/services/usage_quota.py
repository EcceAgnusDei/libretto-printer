from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings
from app.services.gemini_errors import (
    GeminiDailyQuotaError,
    MSG_DAILY_QUOTA,
    MSG_QUOTA_MISCONFIGURED,
)

_lock = threading.Lock()
logger = logging.getLogger(__name__)
_warned_unlimited = False
_warned_misconfigured = False

# Compteur local = garde-fou coût machine unique (trusted), pas un contrôle d'accès multi-tenant.
QUOTA_TIMEZONE = "UTC"


def _usage_path() -> Path:
    return settings.data_dir / "gemini_usage.json"


def _today() -> str:
    """Jour civil UTC — doit rester aligné avec next_reset_at()."""
    return datetime.now(timezone.utc).date().isoformat()


def next_reset_at() -> datetime:
    """Prochain minuit UTC (même référentiel que _today)."""
    today = datetime.now(timezone.utc).date()
    return datetime(
        today.year,
        today.month,
        today.day,
        tzinfo=timezone.utc,
    ) + timedelta(days=1)


def next_reset_iso() -> str:
    return next_reset_at().isoformat()


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
    """Écriture atomique (tmp + replace) pour éviter un reset à 0 si crash mid-write."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = _usage_path()
    payload = json.dumps(data, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(settings.data_dir),
        prefix=".gemini_usage.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def batches_needed(page_count: int, batch_size: int | None = None) -> int:
    """Lots d'extraction + 1 pour la relecture finale Gemini."""
    size = batch_size if batch_size is not None else settings.batch_size
    if page_count <= 0 or size <= 0:
        return 0
    return math.ceil(page_count / size) + 1


def unlimited_allowed() -> bool:
    return settings.gemini_max_batches_per_day <= 0 and (
        settings.allow_unlimited_gemini_quota
    )


def assert_quota_configured() -> None:
    """Refuse le mode illimité sauf si ALLOW_UNLIMITED_GEMINI_QUOTA=true."""
    global _warned_misconfigured
    if settings.gemini_max_batches_per_day > 0:
        return
    if settings.allow_unlimited_gemini_quota:
        return
    if not _warned_misconfigured:
        _warned_misconfigured = True
        logger.error(
            "gemini_max_batches_per_day=%s sans ALLOW_UNLIMITED_GEMINI_QUOTA "
            "— quota illimité interdit",
            settings.gemini_max_batches_per_day,
        )
    raise GeminiDailyQuotaError(MSG_QUOTA_MISCONFIGURED, source="server")


def _warn_if_unlimited(limit: int) -> None:
    global _warned_unlimited
    if limit > 0 or _warned_unlimited:
        return
    if not settings.allow_unlimited_gemini_quota:
        return
    _warned_unlimited = True
    logger.warning(
        "gemini_max_batches_per_day=%s : plafond serveur désactivé (illimité)",
        limit,
    )


def get_usage() -> dict:
    with _lock:
        data = _load_unlocked()
    limit = settings.gemini_max_batches_per_day
    used = int(data["batches"])
    if limit <= 0:
        if settings.allow_unlimited_gemini_quota:
            _warn_if_unlimited(limit)
            remaining = None
        else:
            remaining = 0
    else:
        remaining = max(0, limit - used)
    return {
        "date": data["date"],
        "batches_used": used,
        "batches_limit": limit,
        "batches_remaining": remaining,
        "batch_size": settings.batch_size,
        "next_reset_at": next_reset_iso(),
        "quota_timezone": QUOTA_TIMEZONE,
    }


def assert_under_daily_limit() -> None:
    """Lève GeminiDailyQuotaError si le plafond journalier de lots est atteint."""
    assert_quota_configured()
    limit = settings.gemini_max_batches_per_day
    if limit <= 0:
        return
    with _lock:
        data = _load_unlocked()
        if data["batches"] >= limit:
            raise GeminiDailyQuotaError(MSG_DAILY_QUOTA, source="server")


def reserve_batch() -> None:
    """Réserve un lot de façon atomique (vérifie + incrémente sous le même verrou)."""
    assert_quota_configured()
    limit = settings.gemini_max_batches_per_day
    with _lock:
        data = _load_unlocked()
        if limit > 0 and data["batches"] >= limit:
            raise GeminiDailyQuotaError(MSG_DAILY_QUOTA, source="server")
        data["batches"] = int(data["batches"]) + 1
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save_unlocked(data)


def record_batch() -> None:
    """Incrémente après un appel Gemini réussi (pas de pré-réservation)."""
    assert_quota_configured()
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
        "next_reset_at": usage["next_reset_at"],
        "quota_timezone": usage["quota_timezone"],
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
