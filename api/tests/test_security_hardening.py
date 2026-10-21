from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import override
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.services import extract as extract_service  # noqa: E402
from app.services import ingest as ingest_service  # noqa: E402
from app.services import usage_quota  # noqa: E402
from app.services.gemini_errors import (  # noqa: E402
    MSG_DAILY_QUOTA,
    MSG_EXTRACT_FAILED,
    MSG_QUOTA_MISCONFIGURED,
    client_safe_error,
)
from fastapi import HTTPException  # noqa: E402


class ClientSafeErrorTests(unittest.TestCase):
    def test_keeps_known_messages(self) -> None:
        self.assertEqual(client_safe_error(MSG_DAILY_QUOTA), MSG_DAILY_QUOTA)

    def test_redacts_unknown(self) -> None:
        self.assertEqual(
            client_safe_error("ValueError: /secret/path boom"),
            MSG_EXTRACT_FAILED,
        )


class QuotaMisconfigTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = settings.data_dir
        self._old_limit = settings.gemini_max_batches_per_day
        self._old_allow = settings.allow_unlimited_gemini_quota
        settings.data_dir = Path(self._tmp.name)
        settings.gemini_max_batches_per_day = 0
        settings.allow_unlimited_gemini_quota = False

    @override
    def tearDown(self) -> None:
        settings.data_dir = self._old_data
        settings.gemini_max_batches_per_day = self._old_limit
        settings.allow_unlimited_gemini_quota = self._old_allow
        self._tmp.cleanup()

    def test_assert_quota_configured_blocks(self) -> None:
        with self.assertRaises(Exception) as ctx:
            usage_quota.assert_quota_configured()
        self.assertEqual(str(ctx.exception), MSG_QUOTA_MISCONFIGURED)

    def test_get_usage_reports_zero_remaining(self) -> None:
        usage = usage_quota.get_usage()
        self.assertEqual(usage["batches_remaining"], 0)
        self.assertEqual(usage["batches_limit"], 0)

    def test_unlimited_allowed_when_flag_set(self) -> None:
        settings.allow_unlimited_gemini_quota = True
        usage_quota.assert_quota_configured()
        usage = usage_quota.get_usage()
        self.assertIsNone(usage["batches_remaining"])


class PurgeSessionsTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = settings.data_dir
        self._old_ttl = settings.session_ttl_hours
        self._old_key = settings.gemini_api_key
        settings.data_dir = Path(self._tmp.name)
        settings.session_ttl_hours = 1.0
        settings.gemini_api_key = "test-key"

    @override
    def tearDown(self) -> None:
        settings.data_dir = self._old_data
        settings.session_ttl_hours = self._old_ttl
        settings.gemini_api_key = self._old_key
        self._tmp.cleanup()

    def test_purge_expired_session(self) -> None:
        meta = ingest_service.create_session(source_name="old.pdf")
        session_dir = settings.sessions_dir / meta.session_id
        old = time.time() - (3 * 3600)
        import os

        os.utime(session_dir, (old, old))
        for child in session_dir.rglob("*"):
            os.utime(child, (old, old))

        deleted = extract_service.purge_expired_sessions()
        self.assertEqual(deleted, 1)
        self.assertFalse(session_dir.exists())

    def test_purge_disabled_when_ttl_zero(self) -> None:
        settings.session_ttl_hours = 0
        _ = ingest_service.create_session(source_name="keep.pdf")
        self.assertEqual(extract_service.purge_expired_sessions(), 0)


class ExtractErrorRedactionTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = settings.data_dir
        self._old_key = settings.gemini_api_key
        self._old_limit = settings.gemini_max_batches_per_day
        self._old_allow = settings.allow_unlimited_gemini_quota
        settings.data_dir = Path(self._tmp.name)
        settings.gemini_api_key = "test-key"
        settings.gemini_max_batches_per_day = 10
        settings.allow_unlimited_gemini_quota = False

    @override
    def tearDown(self) -> None:
        settings.data_dir = self._old_data
        settings.gemini_api_key = self._old_key
        settings.gemini_max_batches_per_day = self._old_limit
        settings.allow_unlimited_gemini_quota = self._old_allow
        self._tmp.cleanup()

    def _make_session(self) -> str:
        meta = ingest_service.create_session(source_name="fixture.pdf")
        session_id = meta.session_id
        session_dir = settings.sessions_dir / session_id
        (session_dir / "source.pdf").write_bytes(b"%PDF-1.4")
        pages = session_dir / "pages"
        pages.mkdir(exist_ok=True)
        for i in range(2):
            (pages / f"page_{i:04d}.png").write_bytes(b"\x89PNG\r\n")
        meta_path = session_dir / "meta.json"
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        data["has_pdf"] = True
        data["pdf_page_count"] = 2
        data["page_count"] = 2
        data["rendered_count"] = 2
        meta_path.write_text(json.dumps(data), encoding="utf-8")
        return session_id

    def test_get_job_redacts_technical_error(self) -> None:
        session_id = self._make_session()
        now = datetime.now(timezone.utc).isoformat()
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": "error",
                "source_name": "fixture.pdf",
                "pdf_page_count": 2,
                "batch_size": 2,
                "total_batches": 1,
                "pages_done": 0,
                "current_batch": 0,
                "error": "RuntimeError: /tmp/secret leaked",
                "wait": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        job = extract_service.get_job(session_id)
        self.assertEqual(job["error"], MSG_EXTRACT_FAILED)

    def test_record_batch_after_successful_extract(self) -> None:
        session_id = self._make_session()
        now = datetime.now(timezone.utc).isoformat()
        extract_service.write_libretto(session_id, "")
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": "running",
                "source_name": "fixture.pdf",
                "pdf_page_count": 2,
                "batch_size": 2,
                "total_batches": 1,
                "pages_done": 0,
                "current_batch": 0,
                "error": None,
                "wait": None,
                "created_at": now,
                "updated_at": now,
            }
        )

        with (
            patch(
                "app.services.extract.gemini_client.extract_libretto_markdown",
                return_value="# Titre\n\nTexte.\n",
            ),
            patch.object(usage_quota, "assert_under_daily_limit") as assert_limit,
            patch.object(usage_quota, "record_batch") as record,
        ):
            extract_service.run_extraction(session_id)

        assert_limit.assert_called()
        record.assert_called_once()
        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "done")

    def test_failed_extract_does_not_record_batch(self) -> None:
        session_id = self._make_session()
        now = datetime.now(timezone.utc).isoformat()
        extract_service.write_libretto(session_id, "")
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": "running",
                "source_name": "fixture.pdf",
                "pdf_page_count": 2,
                "batch_size": 2,
                "total_batches": 1,
                "pages_done": 0,
                "current_batch": 0,
                "error": None,
                "wait": None,
                "created_at": now,
                "updated_at": now,
            }
        )

        with (
            patch(
                "app.services.extract.gemini_client.extract_libretto_markdown",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(usage_quota, "assert_under_daily_limit"),
            patch.object(usage_quota, "record_batch") as record,
        ):
            extract_service.run_extraction(session_id)

        record.assert_not_called()
        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "error")
        self.assertEqual(job["error"], MSG_EXTRACT_FAILED)

    def test_start_rejects_misconfigured_quota(self) -> None:
        settings.gemini_max_batches_per_day = 0
        settings.allow_unlimited_gemini_quota = False
        session_id = self._make_session()
        with self.assertRaises(HTTPException) as ctx:
            extract_service.start_job(session_id)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail, MSG_QUOTA_MISCONFIGURED)


if __name__ == "__main__":
    unittest.main()
