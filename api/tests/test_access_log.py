from __future__ import annotations

import logging
import unittest

from app.access_log import QuietStatusAccessFilter, configure_access_log_filters


class QuietStatusAccessFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.filter = QuietStatusAccessFilter()

    def _record(
        self,
        method: str,
        path: str,
        status_code: int,
    ) -> logging.LogRecord:
        return logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:1234", method, path, "1.1", status_code),
            exc_info=None,
        )

    def test_suppresses_successful_status_poll(self) -> None:
        record = self._record(
            "GET",
            "/extract/sessions/abc/status",
            200,
        )
        self.assertFalse(self.filter.filter(record))

    def test_keeps_status_errors(self) -> None:
        record = self._record(
            "GET",
            "/extract/sessions/abc/status",
            404,
        )
        self.assertTrue(self.filter.filter(record))

    def test_keeps_other_gets(self) -> None:
        record = self._record(
            "GET",
            "/extract/sessions/abc/libretto",
            200,
        )
        self.assertTrue(self.filter.filter(record))

    def test_configure_is_idempotent(self) -> None:
        access_logger = logging.getLogger("uvicorn.access")
        before = len(access_logger.filters)
        configure_access_log_filters()
        configure_access_log_filters()
        filters = [
            item
            for item in access_logger.filters
            if isinstance(item, QuietStatusAccessFilter)
        ]
        self.assertEqual(len(filters), 1)
        self.assertGreaterEqual(len(access_logger.filters), before)


if __name__ == "__main__":
    unittest.main()
