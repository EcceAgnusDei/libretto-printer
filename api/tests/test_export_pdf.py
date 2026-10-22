from __future__ import annotations

import json
import sys
import tempfile
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
from app.services import export_pdf as export_pdf_service  # noqa: E402
from app.services import extract as extract_service  # noqa: E402
from app.services import rate_limit  # noqa: E402
from app.services.gemini_errors import MSG_PDF_FAILED  # noqa: E402


class ExportPdfTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old = {
            "data_dir": settings.data_dir,
            "gemini_api_key": settings.gemini_api_key,
            "session_ttl_hours": settings.session_ttl_hours,
            "extract_supervisor_interval_seconds": (
                settings.extract_supervisor_interval_seconds
            ),
            "rate_limit_upload_max": settings.rate_limit_upload_max,
            "rate_limit_extract_start_max": settings.rate_limit_extract_start_max,
        }
        settings.data_dir = Path(self._tmp.name)
        settings.gemini_api_key = "test-key"
        settings.session_ttl_hours = 0
        settings.extract_supervisor_interval_seconds = 3600.0
        settings.rate_limit_upload_max = 50
        settings.rate_limit_extract_start_max = 50
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

    def _save_job(
        self,
        session_id: str,
        *,
        status: str,
        review_done: bool,
        pdf_ready: bool = False,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": status,
                "phase": "review",
                "source_name": "Carmen.pdf",
                "pdf_page_count": 2,
                "batch_size": 2,
                "total_batches": 1,
                "pages_done": 2,
                "current_batch": 1,
                "review_done": review_done,
                "pdf_ready": pdf_ready,
                "error": None,
                "wait": None,
                "created_at": now,
                "updated_at": now,
            }
        )

    def test_markdown_helpers(self) -> None:
        md = "# Carmen\n\n## Acte I\n\nTexte *italique*.\n"
        self.assertEqual(export_pdf_service.extract_title(md, "x"), "Carmen")
        html = export_pdf_service.markdown_to_body_html(md)
        self.assertIn("<h1>", html)
        self.assertIn("<h2>", html)
        self.assertNotIn("<script>", html)
        unsafe = export_pdf_service.markdown_to_body_html(
            '# T\n\n<script>alert(1)</script>\n\n<p onclick="x">ok</p>\n'
        )
        self.assertNotIn("<script>", unsafe)
        self.assertNotIn("onclick", unsafe)
        pdf = export_pdf_service.render_pdf_bytes(md, "Carmen")
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_pdf_not_ready_until_generated(self) -> None:
        session_id = self._make_ready_session()
        extract_service.write_libretto(
            session_id,
            "# Titre\n\n## Acte I\n\nTexte.\n",
        )
        self._save_job(session_id, status="done", review_done=True, pdf_ready=False)
        denied = self.client.get(f"/extract/sessions/{session_id}/pdf")
        self.assertEqual(denied.status_code, 409)

        self._save_job(session_id, status="running", review_done=False)
        denied_running = self.client.get(f"/extract/sessions/{session_id}/pdf")
        self.assertEqual(denied_running.status_code, 409)

    def test_generate_after_review_keeps_file_until_cleared(self) -> None:
        session_id = self._make_ready_session()
        md = "# Carmen\n\n## Acte I\n\n### Scène 1\n\nDon José.\n"
        extract_service.write_libretto(session_id, md)
        self._save_job(session_id, status="running", review_done=False)
        job = extract_service._load_job(session_id)
        assert job is not None
        extract_service._mark_review_done(session_id, job)

        status = self.client.get(f"/extract/sessions/{session_id}/status")
        self.assertEqual(status.status_code, 200)
        body = status.json()
        self.assertEqual(body["status"], "done")
        self.assertTrue(body["review_done"])
        self.assertTrue(body["pdf_ready"])

        pdf_path = settings.sessions_dir / session_id / "libretto.pdf"
        self.assertTrue(pdf_path.is_file())
        mtime_before = pdf_path.stat().st_mtime
        first_bytes = pdf_path.read_bytes()

        first = self.client.get(f"/extract/sessions/{session_id}/pdf")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["content-type"], "application/pdf")
        self.assertEqual(first.content, first_bytes)
        self.assertIn("Carmen.pdf", first.headers.get("content-disposition", ""))

        extract_service.write_libretto(
            session_id,
            "# Carmen\n\n## Acte I\n\n### Scène 1\n\nVersion différente.\n",
        )
        second = self.client.get(f"/extract/sessions/{session_id}/pdf")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.content, first_bytes)
        self.assertEqual(pdf_path.stat().st_mtime, mtime_before)

        export_pdf_service.clear_libretto_pdf(session_id)
        self.assertFalse(pdf_path.exists())

    def test_pdf_failure_sets_flag(self) -> None:
        session_id = self._make_ready_session()
        extract_service.write_libretto(session_id, "# Titre\n\nTexte.\n")
        self._save_job(session_id, status="running", review_done=False)
        job = extract_service._load_job(session_id)
        assert job is not None
        with patch(
            "app.services.export_pdf.write_libretto_pdf",
            side_effect=RuntimeError("boom"),
        ):
            extract_service._mark_review_done(session_id, job)
        status = self.client.get(f"/extract/sessions/{session_id}/status").json()
        self.assertEqual(status["status"], "done")
        self.assertTrue(status["review_done"])
        self.assertFalse(status["pdf_ready"])
        self.assertEqual(status["error"], MSG_PDF_FAILED)


if __name__ == "__main__":
    unittest.main()
