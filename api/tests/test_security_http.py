from __future__ import annotations

import json
import os
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

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import extract as extract_service  # noqa: E402
from app.services import rate_limit  # noqa: E402
from app.services.gemini_errors import (  # noqa: E402
    MSG_EXTRACT_FAILED,
    MSG_QUOTA_MISCONFIGURED,
)


class SecurityHttpTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old = {
            "data_dir": settings.data_dir,
            "gemini_api_key": settings.gemini_api_key,
            "gemini_max_batches_per_day": settings.gemini_max_batches_per_day,
            "allow_unlimited_gemini_quota": settings.allow_unlimited_gemini_quota,
            "rate_limit_window_seconds": settings.rate_limit_window_seconds,
            "rate_limit_upload_max": settings.rate_limit_upload_max,
            "rate_limit_extract_start_max": settings.rate_limit_extract_start_max,
            "force_https": settings.force_https,
            "trust_proxy_headers": settings.trust_proxy_headers,
            "session_ttl_hours": settings.session_ttl_hours,
            "cors_origins": settings.cors_origins,
            "extract_supervisor_interval_seconds": (
                settings.extract_supervisor_interval_seconds
            ),
        }
        settings.data_dir = Path(self._tmp.name)
        settings.gemini_api_key = "test-key"
        settings.gemini_max_batches_per_day = 100
        settings.allow_unlimited_gemini_quota = False
        settings.rate_limit_window_seconds = 60.0
        settings.rate_limit_upload_max = 3
        settings.rate_limit_extract_start_max = 2
        settings.force_https = False
        settings.trust_proxy_headers = False
        settings.session_ttl_hours = 0
        settings.cors_origins = "http://localhost:3000"
        settings.extract_supervisor_interval_seconds = 3600.0
        rate_limit.reset_for_tests()
        extract_service.stop_supervisor()
        self.client = TestClient(app, raise_server_exceptions=True)

    @override
    def tearDown(self) -> None:
        self.client.close()
        extract_service.stop_supervisor()
        rate_limit.reset_for_tests()
        for key, value in self._old.items():
            setattr(settings, key, value)
        self._tmp.cleanup()

    def _make_ready_session(self, page_count: int = 2) -> str:
        create = self.client.post(
            "/ingest/sessions",
            data={"source_name": "fixture.pdf"},
        )
        self.assertEqual(create.status_code, 200)
        session_id = create.json()["session_id"]
        session_dir = settings.sessions_dir / session_id
        (session_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        pages = session_dir / "pages"
        pages.mkdir(exist_ok=True)
        for i in range(page_count):
            (pages / f"page_{i:04d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        meta_path = session_dir / "meta.json"
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        data.update(
            {
                "has_pdf": True,
                "pdf_page_count": page_count,
                "page_count": page_count,
                "rendered_count": page_count,
            }
        )
        meta_path.write_text(json.dumps(data), encoding="utf-8")
        return session_id

    def test_health_and_config_and_quota(self) -> None:
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["gemini_configured"])

        config = self.client.get("/config")
        self.assertEqual(config.status_code, 200)
        body = config.json()
        self.assertEqual(body["gemini_max_batches_per_day"], 100)
        self.assertEqual(body["batches_used_today"], 0)

        quota = self.client.get("/quota", params={"page_count": 20})
        self.assertEqual(quota.status_code, 200)
        q = quota.json()
        self.assertEqual(q["batches_needed"], 4)
        self.assertFalse(q["requires_confirmation"])

    def test_cors_allows_configured_origin(self) -> None:
        res = self.client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            res.headers.get("access-control-allow-origin"),
            "http://localhost:3000",
        )

    def test_cors_rejects_unknown_origin(self) -> None:
        res = self.client.get(
            "/health",
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertNotEqual(
            res.headers.get("access-control-allow-origin"),
            "https://evil.example",
        )

    def test_force_https_blocks_http(self) -> None:
        settings.force_https = True
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "HTTPS requis")

    def test_force_https_allows_forwarded_proto(self) -> None:
        settings.force_https = True
        res = self.client.get(
            "/health",
            headers={"X-Forwarded-Proto": "https"},
        )
        self.assertEqual(res.status_code, 200)

    def test_upload_rate_limit_returns_429(self) -> None:
        settings.rate_limit_upload_max = 2
        rate_limit.reset_for_tests()
        for _ in range(2):
            res = self.client.post(
                "/ingest/sessions",
                data={"source_name": "a.pdf"},
            )
            self.assertEqual(res.status_code, 200)
        blocked = self.client.post(
            "/ingest/sessions",
            data={"source_name": "b.pdf"},
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("trop de requêtes", blocked.json()["detail"])

    def test_extract_start_rate_limit_returns_429(self) -> None:
        settings.rate_limit_upload_max = 50
        settings.rate_limit_extract_start_max = 1
        rate_limit.reset_for_tests()
        session_a = self._make_ready_session()
        session_b = self._make_ready_session()

        with patch.object(extract_service, "_spawn_worker"):
            first = self.client.post(f"/extract/sessions/{session_a}/start")
            self.assertEqual(first.status_code, 200)
            second = self.client.post(f"/extract/sessions/{session_b}/start")
        self.assertEqual(second.status_code, 429)

    def test_start_rejects_unlimited_quota_misconfig(self) -> None:
        settings.rate_limit_upload_max = 50
        settings.rate_limit_extract_start_max = 50
        rate_limit.reset_for_tests()
        session_id = self._make_ready_session()
        settings.gemini_max_batches_per_day = 0
        settings.allow_unlimited_gemini_quota = False
        res = self.client.post(f"/extract/sessions/{session_id}/start")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["detail"], MSG_QUOTA_MISCONFIGURED)

    def test_status_redacts_technical_error(self) -> None:
        settings.rate_limit_upload_max = 50
        rate_limit.reset_for_tests()
        session_id = self._make_ready_session()
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
                "error": "OSError: /home/secret/key.json",
                "wait": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        res = self.client.get(f"/extract/sessions/{session_id}/status")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["error"], MSG_EXTRACT_FAILED)

    def test_purge_expired_via_service_after_http_create(self) -> None:
        settings.rate_limit_upload_max = 50
        rate_limit.reset_for_tests()
        create = self.client.post(
            "/ingest/sessions",
            data={"source_name": "old.pdf"},
        )
        session_id = create.json()["session_id"]
        session_dir = settings.sessions_dir / session_id
        old = time.time() - (5 * 3600)
        os.utime(session_dir, (old, old))
        for child in session_dir.rglob("*"):
            os.utime(child, (old, old))
        settings.session_ttl_hours = 1.0
        deleted = extract_service.purge_expired_sessions()
        self.assertEqual(deleted, 1)
        self.assertFalse(session_dir.exists())
        missing = self.client.get(f"/ingest/sessions/{session_id}")
        self.assertEqual(missing.status_code, 404)

    def test_failed_gemini_does_not_increment_usage_over_http_worker(self) -> None:
        settings.rate_limit_upload_max = 50
        settings.rate_limit_extract_start_max = 50
        rate_limit.reset_for_tests()
        session_id = self._make_ready_session(page_count=2)
        before = self.client.get("/quota").json()["batches_used"]

        with patch(
            "app.services.extract.gemini_client.extract_libretto_markdown",
            side_effect=RuntimeError("simulated failure"),
        ):
            extract_service.write_libretto(session_id, "")
            now = datetime.now(timezone.utc).isoformat()
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
            extract_service.run_extraction(session_id)

        after = self.client.get("/quota").json()["batches_used"]
        self.assertEqual(after, before)
        status = self.client.get(f"/extract/sessions/{session_id}/status")
        self.assertEqual(status.json()["error"], MSG_EXTRACT_FAILED)

    def test_successful_gemini_increments_usage(self) -> None:
        settings.rate_limit_upload_max = 50
        settings.rate_limit_extract_start_max = 50
        rate_limit.reset_for_tests()
        session_id = self._make_ready_session(page_count=2)
        before = self.client.get("/quota").json()["batches_used"]

        with patch(
            "app.services.extract.gemini_client.extract_libretto_markdown",
            return_value="# Titre\n\nTexte.\n",
        ):
            extract_service.write_libretto(session_id, "")
            now = datetime.now(timezone.utc).isoformat()
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
            extract_service.run_extraction(session_id)

        after = self.client.get("/quota").json()["batches_used"]
        self.assertEqual(after, before + 1)
        status = self.client.get(f"/extract/sessions/{session_id}/status")
        self.assertEqual(status.json()["status"], "done")


if __name__ == "__main__":
    unittest.main()
