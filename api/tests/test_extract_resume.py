from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.services import extract as extract_service  # noqa: E402
from app.services import ingest as ingest_service  # noqa: E402
from app.services.gemini_errors import (  # noqa: E402
    GeminiDailyQuotaError,
    MSG_DAILY_QUOTA,
)


def _write_tiny_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=(200, 200, 200)).save(path, format="PNG")


class ExtractResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = settings.data_dir
        self._old_key = settings.gemini_api_key
        self._old_batch = settings.batch_size
        self._old_interval = settings.gemini_min_interval_seconds
        self._old_limit = settings.gemini_max_batches_per_day
        settings.data_dir = Path(self._tmp.name)
        settings.sessions_dir.mkdir(parents=True, exist_ok=True)
        settings.gemini_api_key = "test-key"
        settings.batch_size = 2
        settings.gemini_min_interval_seconds = 0
        settings.gemini_max_batches_per_day = 100
        extract_service._running_sessions.clear()
        extract_service._last_gemini_call_at = None

    def tearDown(self) -> None:
        extract_service._running_sessions.clear()
        extract_service._last_gemini_call_at = None
        settings.data_dir = self._old_data
        settings.gemini_api_key = self._old_key
        settings.batch_size = self._old_batch
        settings.gemini_min_interval_seconds = self._old_interval
        settings.gemini_max_batches_per_day = self._old_limit
        self._tmp.cleanup()

    def _make_session(self, page_count: int = 4) -> str:
        meta = ingest_service.create_session("fixture.pdf")
        session_id = meta.session_id
        session_dir = settings.sessions_dir / session_id
        (session_dir / "source.pdf").write_bytes(b"%PDF-1.4 fixture")
        pages_dir = session_dir / "pages"
        for index in range(page_count):
            _write_tiny_png(pages_dir / f"page_{index:04d}.png")
        meta.has_pdf = True
        meta.pdf_page_count = page_count
        meta.rendered_count = page_count
        meta.page_count = page_count
        ingest_service._save_meta(meta)
        return session_id

    def _write_partial_job(
        self,
        session_id: str,
        *,
        pages_done: int,
        status: str = "paused",
        source: str | None = "server",
        page_count: int = 4,
    ) -> dict:
        now = extract_service._now_iso()
        libretto = "## Acte I\n\nTexte partiel.\n"
        extract_service.write_libretto(session_id, libretto)
        job = {
            "session_id": session_id,
            "status": status,
            "source_name": "fixture.pdf",
            "pdf_page_count": page_count,
            "batch_size": settings.batch_size,
            "total_batches": extract_service._total_batches(
                page_count, settings.batch_size
            ),
            "pages_done": pages_done,
            "current_batch": max(1, pages_done // settings.batch_size),
            "error": MSG_DAILY_QUOTA if status in {"paused", "error"} else None,
            "wait": {
                "reason": "daily",
                "message": MSG_DAILY_QUOTA,
                "wait_seconds": None,
                "wait_until": None,
                "source": source,
            }
            if status in {"paused", "error"}
            else None,
            "created_at": now,
            "updated_at": now,
        }
        extract_service._save_job(job)
        return job

    def test_resume_preserves_libretto_and_pages_done(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2, source="server")
        before_md = extract_service.read_libretto(session_id)

        with patch.object(extract_service, "_spawn_worker") as spawn:
            job = extract_service.resume_job(session_id)

        spawn.assert_called_once_with(session_id)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["pages_done"], 2)
        self.assertIsNone(job["error"])
        self.assertIsNone(job["wait"])
        self.assertEqual(extract_service.read_libretto(session_id), before_md)

    def test_start_still_wipes_progress(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2)

        with patch.object(extract_service, "_spawn_worker"):
            job = extract_service.start_job(session_id)

        self.assertEqual(job["pages_done"], 0)
        self.assertEqual(extract_service.read_libretto(session_id), "")

    def test_resume_rejects_non_daily_error(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2, status="error")
        job = extract_service._load_job(session_id)
        assert job is not None
        job["wait"] = None
        job["error"] = "ValueError: boom"
        extract_service._save_job(job)

        with self.assertRaises(HTTPException) as ctx:
            extract_service.resume_job(session_id)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_resume_accepts_legacy_error_daily(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(
            session_id, pages_done=2, status="error", source="gemini"
        )

        with patch.object(extract_service, "_spawn_worker") as spawn:
            job = extract_service.resume_job(session_id)

        spawn.assert_called_once_with(session_id)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["pages_done"], 2)

    def test_resume_rejects_when_complete(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=4)

        with self.assertRaises(HTTPException) as ctx:
            extract_service.resume_job(session_id)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_pause_for_daily_quota_sets_source(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=0, status="running")
        job = extract_service._load_job(session_id)
        assert job is not None
        job["wait"] = None
        job["error"] = None
        extract_service._save_job(job)

        extract_service._pause_for_daily_quota(
            session_id,
            job,
            GeminiDailyQuotaError(MSG_DAILY_QUOTA, source="gemini"),
        )
        saved = extract_service._load_job(session_id)
        assert saved is not None
        self.assertEqual(saved["status"], "paused")
        self.assertEqual(saved["error"], MSG_DAILY_QUOTA)
        self.assertEqual(saved["wait"]["reason"], "daily")
        self.assertEqual(saved["wait"]["source"], "gemini")
        self.assertIsNone(saved["wait"]["wait_until"])

    def test_run_extraction_pauses_on_server_daily_and_keeps_checkpoint(
        self,
    ) -> None:
        session_id = self._make_session(4)
        now = extract_service._now_iso()
        extract_service.write_libretto(session_id, "")
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": "running",
                "source_name": "fixture.pdf",
                "pdf_page_count": 4,
                "batch_size": 2,
                "total_batches": 2,
                "pages_done": 0,
                "current_batch": 0,
                "error": None,
                "wait": None,
                "created_at": now,
                "updated_at": now,
            }
        )

        calls = {"n": 0}

        def fake_extract(image_paths, section, *, on_wait=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return "## Acte I\n\nLot 1.\n"
            raise GeminiDailyQuotaError(MSG_DAILY_QUOTA, source="server")

        with (
            patch.object(
                extract_service.gemini_client,
                "extract_libretto_markdown",
                side_effect=fake_extract,
            ),
            patch.object(extract_service.usage_quota, "reserve_batch"),
        ):
            extract_service.run_extraction(session_id)

        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "paused")
        self.assertEqual(job["pages_done"], 2)
        self.assertEqual(job["wait"]["source"], "server")
        self.assertIsNotNone(job["wait"]["wait_until"])
        self.assertIn("Lot 1", extract_service.read_libretto(session_id))

    def test_resume_server_blocked_while_local_quota_exhausted(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2, source="server")
        settings.gemini_max_batches_per_day = 1
        from app.services import usage_quota

        usage_quota.reserve_batch()

        with self.assertRaises(HTTPException) as ctx:
            extract_service.resume_job(session_id)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.detail, MSG_DAILY_QUOTA)
        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "paused")
        self.assertEqual(job["pages_done"], 2)
        self.assertEqual(job["wait"]["source"], "server")
        self.assertIsNotNone(job["wait"]["wait_until"])
        self.assertIn("Texte partiel", extract_service.read_libretto(session_id))

    def test_resume_gemini_source_ignores_local_quota(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2, source="gemini")
        settings.gemini_max_batches_per_day = 1
        from app.services import usage_quota

        usage_quota.reserve_batch()

        with patch.object(extract_service, "_spawn_worker") as spawn:
            job = extract_service.resume_job(session_id)

        spawn.assert_called_once_with(session_id)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["pages_done"], 2)

    def test_pause_server_sets_wait_until_utc_midnight(self) -> None:
        from app.services import usage_quota

        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=0, status="running")
        job = extract_service._load_job(session_id)
        assert job is not None
        job["wait"] = None
        job["error"] = None

        extract_service._pause_for_daily_quota(
            session_id,
            job,
            GeminiDailyQuotaError(MSG_DAILY_QUOTA, source="server"),
        )
        saved = extract_service._load_job(session_id)
        assert saved is not None
        self.assertEqual(saved["wait"]["wait_until"], usage_quota.next_reset_iso())
        self.assertEqual(saved["wait"]["source"], "server")

    def test_resume_then_run_continues_from_pages_done(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2, source="gemini")
        seen_starts: list[int] = []

        def fake_extract(image_paths, section, *, on_wait=None):
            start = int(image_paths[0].stem.split("_")[1])
            seen_starts.append(start)
            return f"{section}\nPage {start}.\n"

        with (
            patch.object(extract_service, "_spawn_worker"),
            patch.object(
                extract_service.gemini_client,
                "extract_libretto_markdown",
                side_effect=fake_extract,
            ),
            patch.object(extract_service.usage_quota, "reserve_batch"),
        ):
            extract_service.resume_job(session_id)
            extract_service.run_extraction(session_id)

        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["pages_done"], 4)
        self.assertEqual(seen_starts, [2])
        md = extract_service.read_libretto(session_id)
        self.assertIn("Texte partiel", md)
        self.assertIn("Page 2", md)

    def test_is_daily_resumable_helpers(self) -> None:
        self.assertTrue(
            extract_service._is_daily_resumable(
                {"status": "paused", "wait": {"reason": "daily"}}
            )
        )
        self.assertTrue(
            extract_service._is_daily_resumable(
                {"status": "error", "wait": {"reason": "daily"}}
            )
        )
        self.assertFalse(
            extract_service._is_daily_resumable(
                {"status": "error", "wait": None}
            )
        )
        self.assertFalse(
            extract_service._is_daily_resumable({"status": "done", "wait": None})
        )


class UsageQuotaSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = settings.data_dir
        self._old_limit = settings.gemini_max_batches_per_day
        settings.data_dir = Path(self._tmp.name)
        settings.gemini_max_batches_per_day = 1

    def tearDown(self) -> None:
        settings.data_dir = self._old_data
        settings.gemini_max_batches_per_day = self._old_limit
        self._tmp.cleanup()

    def test_reserve_batch_error_has_server_source(self) -> None:
        from app.services import usage_quota

        usage_quota.reserve_batch()
        with self.assertRaises(GeminiDailyQuotaError) as ctx:
            usage_quota.reserve_batch()
        self.assertEqual(ctx.exception.source, "server")


if __name__ == "__main__":
    unittest.main()
