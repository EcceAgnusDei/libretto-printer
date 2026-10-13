from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.services.gemini_errors import (  # noqa: E402
    MSG_DAILY_QUOTA,
    MSG_RATE_LIMIT,
    classify_gemini_error,
    gemini_daily_wait_until_iso,
    next_gemini_reset_at,
    next_gemini_reset_iso,
)


class _FakeExc(Exception):
    def __init__(self, message: str, *, code: int | None = None, details=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details


class GeminiQuotaHelpersTests(unittest.TestCase):
    def test_next_gemini_reset_is_next_pacific_midnight_as_utc(self) -> None:
        tz = ZoneInfo(settings.gemini_quota_timezone)
        reset_local = next_gemini_reset_at()
        self.assertEqual(str(reset_local.tzinfo), str(tz))
        self.assertEqual(reset_local.hour, 0)
        self.assertEqual(reset_local.minute, 0)
        self.assertEqual(reset_local.second, 0)
        now_local = datetime.now(tz)
        self.assertGreater(reset_local, now_local)
        self.assertLessEqual(reset_local - now_local, timedelta(days=1, minutes=1))

        reset_utc = datetime.fromisoformat(next_gemini_reset_iso())
        self.assertEqual(reset_utc.tzinfo, timezone.utc)
        self.assertEqual(reset_utc, reset_local.astimezone(timezone.utc))

    def test_daily_wait_until_prefers_structured_retry_info(self) -> None:
        payload = {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "120s",
                    }
                ]
            }
        }
        until = datetime.fromisoformat(gemini_daily_wait_until_iso(payload))
        delta = until - datetime.now(timezone.utc)
        self.assertGreaterEqual(delta.total_seconds(), 110)
        self.assertLessEqual(delta.total_seconds(), 130)

    def test_classify_structured_daily_quota_id(self) -> None:
        exc = _FakeExc(
            "quota",
            code=429,
            details={
                "error": {
                    "message": "Resource exhausted",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {"quotaId": "GenerateRequestsPerDay-FreeTier"}
                            ],
                        }
                    ],
                }
            },
        )
        signal = classify_gemini_error(exc, fallback_wait_seconds=2.0)
        assert signal is not None
        self.assertEqual(signal.kind, "daily")
        self.assertEqual(signal.source, "gemini")
        self.assertEqual(signal.message, MSG_DAILY_QUOTA)
        self.assertIn("PerDay", signal.raw_quota_id or "")

    def test_classify_minute_quota_id_is_rate_limit_not_daily(self) -> None:
        exc = _FakeExc(
            "quota",
            code=429,
            details={
                "error": {
                    "message": "Resource exhausted per day wording noise",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {"quotaId": "GenerateRequestsPerMinute-FreeTier"}
                            ],
                        }
                    ],
                }
            },
        )
        signal = classify_gemini_error(exc, fallback_wait_seconds=2.0)
        assert signal is not None
        self.assertEqual(signal.kind, "rate_limit")
        self.assertEqual(signal.message, MSG_RATE_LIMIT)

    def test_classify_message_heuristic_daily_only_on_429(self) -> None:
        ok = classify_gemini_error(
            _FakeExc("exceeded daily quota per day", code=429),
            fallback_wait_seconds=2.0,
        )
        assert ok is not None
        self.assertEqual(ok.kind, "daily")
        self.assertEqual(ok.raw_quota_id, "inferred-daily")

        # Même texte hors 429 : pas de daily inféré (évite faux positifs).
        other = classify_gemini_error(
            _FakeExc("exceeded daily quota per day", code=500),
            fallback_wait_seconds=2.0,
        )
        assert other is not None
        self.assertEqual(other.kind, "rate_limit")


if __name__ == "__main__":
    unittest.main()
