from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import settings

_lock = threading.Lock()
_hits: dict[str, deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        real_ip = request.headers.get("x-real-ip")
        if real_ip and real_ip.strip():
            return real_ip.strip()
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def enforce(request: Request, bucket: str, max_hits: int) -> None:
    """Sliding window ; max_hits <= 0 désactive la limite pour ce bucket."""
    if max_hits <= 0:
        return
    window = max(settings.rate_limit_window_seconds, 1.0)
    key = f"{bucket}:{client_ip(request)}"
    now = time.monotonic()
    with _lock:
        queue = _hits[key]
        while queue and queue[0] <= now - window:
            queue.popleft()
        if len(queue) >= max_hits:
            raise HTTPException(
                status_code=429,
                detail="trop de requêtes, réessayez plus tard",
            )
        queue.append(now)


def reset_for_tests() -> None:
    with _lock:
        _hits.clear()
