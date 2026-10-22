from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

_lock = threading.Lock()
logger = logging.getLogger(__name__)


def _usage_path() -> Path:
    return settings.data_dir / "gemini_token_usage.jsonl"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def record_call(payload: dict[str, Any]) -> None:
    """Append one JSON line; never raises (metrics must not break extraction)."""
    now = _now_utc()
    row: dict[str, Any] = {
        "at": now.isoformat(),
        "date": now.date().isoformat(),
    }
    row.update(payload)
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    try:
        with _lock:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            path = _usage_path()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
    except OSError as exc:
        logger.warning(
            "gemini_token_usage append failed: %s: %s",
            type(exc).__name__,
            exc,
        )
