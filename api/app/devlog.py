from __future__ import annotations

import sys
from typing import Any

from app.config import settings


def devlog(message: str, *args: Any) -> None:
    if not settings.devlog:
        return
    text = message % args if args else message
    print(f"[devlog] {text}", file=sys.stderr, flush=True)
