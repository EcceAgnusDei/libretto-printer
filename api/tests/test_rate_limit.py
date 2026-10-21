from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.services import rate_limit  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class RateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit.reset_for_tests()
        self._old_window = settings.rate_limit_window_seconds
        self._old_max = settings.rate_limit_upload_max
        self._old_trust = settings.trust_proxy_headers
        settings.rate_limit_window_seconds = 60.0
        settings.rate_limit_upload_max = 3
        settings.trust_proxy_headers = False

    def tearDown(self) -> None:
        settings.rate_limit_window_seconds = self._old_window
        settings.rate_limit_upload_max = self._old_max
        settings.trust_proxy_headers = self._old_trust
        rate_limit.reset_for_tests()

    def _request(self, host: str = "1.2.3.4") -> MagicMock:
        req = MagicMock()
        req.client.host = host
        req.headers = {}
        return req

    def test_allows_under_limit(self) -> None:
        req = self._request()
        for _ in range(3):
            rate_limit.enforce(req, "upload", 3)

    def test_blocks_over_limit(self) -> None:
        req = self._request()
        for _ in range(3):
            rate_limit.enforce(req, "upload", 3)
        with self.assertRaises(HTTPException) as ctx:
            rate_limit.enforce(req, "upload", 3)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_disabled_when_max_non_positive(self) -> None:
        req = self._request()
        for _ in range(20):
            rate_limit.enforce(req, "upload", 0)

    def test_trust_proxy_headers(self) -> None:
        settings.trust_proxy_headers = True
        req = self._request(host="10.0.0.1")
        req.headers = {"x-forwarded-for": "9.9.9.9, 10.0.0.1"}
        self.assertEqual(rate_limit.client_ip(req), "9.9.9.9")


if __name__ == "__main__":
    unittest.main()
