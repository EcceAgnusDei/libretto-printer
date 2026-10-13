from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

MSG_RATE_LIMIT = "forte affluence : extraction ralentie"
MSG_DAILY_QUOTA = "dépassement des quota d'utilisation, réessayez demain"

QuotaKind = Literal["rate_limit", "daily"]

_RETRY_DELAY_RE = re.compile(
    r"(?:retry(?:\s+in)?|Please retry in)\s*([0-9]+(?:\.[0-9]+)?)\s*s",
    re.IGNORECASE,
)
_DELAY_TOKEN_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)s$", re.IGNORECASE)


@dataclass(frozen=True)
class QuotaSignal:
    kind: QuotaKind
    wait_seconds: float | None
    message: str
    raw_quota_id: str | None = None


class GeminiDailyQuotaError(Exception):
    """Quota journalier Gemini ou serveur — ne pas retry aujourd'hui."""

    def __init__(self, message: str = MSG_DAILY_QUOTA):
        super().__init__(message)
        self.user_message = message


class GeminiRateLimitError(Exception):
    """RPM/TPM / affluence — retry après wait_seconds."""

    def __init__(self, wait_seconds: float, message: str = MSG_RATE_LIMIT):
        super().__init__(message)
        self.wait_seconds = max(0.0, wait_seconds)
        self.user_message = message


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _error_payload(exc: BaseException) -> dict[str, Any]:
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        if "error" in details and isinstance(details["error"], dict):
            return details
        return {"error": details}
    # Parfois details est déjà le bloc error
    msg = getattr(exc, "message", None) or str(exc)
    return {"error": {"message": msg, "code": getattr(exc, "code", None)}}


def _detail_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    error = _as_dict(payload.get("error"))
    details = error.get("details")
    if isinstance(details, list):
        return [d for d in details if isinstance(d, dict)]
    return []


def parse_retry_delay_seconds(payload: dict[str, Any], fallback: float) -> float:
    for detail in _detail_list(payload):
        type_name = str(detail.get("@type") or "")
        if type_name.endswith("RetryInfo") or "RetryInfo" in type_name:
            raw = detail.get("retryDelay")
            if isinstance(raw, str):
                match = _DELAY_TOKEN_RE.match(raw.strip())
                if match:
                    return max(0.0, float(match.group(1)))
            if isinstance(raw, (int, float)):
                return max(0.0, float(raw))

    error = _as_dict(payload.get("error"))
    message = str(error.get("message") or "")
    match = _RETRY_DELAY_RE.search(message)
    if match:
        return max(0.0, float(match.group(1)))
    return max(0.0, fallback)


def _quota_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for detail in _detail_list(payload):
        type_name = str(detail.get("@type") or "")
        if "QuotaFailure" not in type_name:
            continue
        violations = detail.get("violations")
        if not isinstance(violations, list):
            continue
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            for key in ("quotaId", "quotaMetric"):
                value = violation.get(key)
                if isinstance(value, str) and value:
                    ids.append(value)
    return ids


def is_daily_quota_id(quota_id: str) -> bool:
    lowered = quota_id.lower()
    if any(
        token in lowered
        for token in ("perminute", "per_minute", "permin", "per_min")
    ):
        return False
    return any(
        token in lowered
        for token in ("perday", "per_day", "daily", "requestsperday")
    )


def _is_high_demand(text: str, code: int | None) -> bool:
    """503 UNAVAILABLE / pics de demande Gemini (affluence)."""
    demand_tokens = (
        "high demand",
        "experiencing high demand",
        "spikes in demand",
        "try again later",
        "overloaded",
        "unavailable",
    )
    if any(token in text for token in demand_tokens):
        return True
    return code in {503, 529}


def classify_gemini_error(
    exc: BaseException,
    *,
    fallback_wait_seconds: float,
) -> QuotaSignal | None:
    """Classe une erreur Gemini en rate_limit / daily, ou None si autre."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    text = f"{getattr(exc, 'message', '')} {exc}".lower()
    payload = _error_payload(exc)

    looks_rate_or_quota = (
        code == 429
        or _is_high_demand(text, code)
        or "resourceexhausted" in text
        or "resource_exhausted" in text
        or "quota" in text
        or "rate limit" in text
    )
    if not looks_rate_or_quota:
        return None

    quota_ids = _quota_ids(payload)
    daily_id = next((qid for qid in quota_ids if is_daily_quota_id(qid)), None)

    # Heuristique message si pas de QuotaFailure structuré
    if daily_id is None and any(
        token in text
        for token in (
            "per day",
            "perday",
            "daily quota",
            "quotaid\": \"generaterequestsperday",
        )
    ):
        daily_id = "inferred-daily"

    if daily_id is not None:
        return QuotaSignal(
            kind="daily",
            wait_seconds=None,
            message=MSG_DAILY_QUOTA,
            raw_quota_id=daily_id,
        )

    wait = parse_retry_delay_seconds(payload, fallback_wait_seconds)
    # 503 high demand n'a souvent pas de retryDelay → attendre un peu plus
    if _is_high_demand(text, code) and wait <= fallback_wait_seconds:
        wait = max(fallback_wait_seconds, 15.0)
    if wait < 1.0:
        wait = max(fallback_wait_seconds, 1.0)

    return QuotaSignal(
        kind="rate_limit",
        wait_seconds=wait,
        message=MSG_RATE_LIMIT,
        raw_quota_id=quota_ids[0] if quota_ids else None,
    )


def wait_until_iso(wait_seconds: float) -> str:
    when = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
    return when.isoformat()
