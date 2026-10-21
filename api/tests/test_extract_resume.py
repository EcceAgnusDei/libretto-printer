# pyright: reportPrivateUsage=false, reportUnusedCallResult=false, reportUnknownParameterType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportMissingParameterType=false, reportUnusedParameter=false, reportUninitializedInstanceVariable=false, reportAny=false, reportUnnecessaryTypeIgnoreComment=false

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import override
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.services import extract as extract_service  # noqa: E402
from app.services import gemini_client, usage_quota  # noqa: E402
from app.services import ingest as ingest_service  # noqa: E402
from app.services.extract import JobDict  # noqa: E402
from app.services.gemini_errors import (  # noqa: E402
    GeminiCancelledError,
    GeminiDailyQuotaError,
    MSG_DAILY_QUOTA,
    MSG_RATE_LIMIT,
    MSG_RESUME_COOLDOWN,
    next_gemini_reset_iso,
)


def _write_tiny_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color=(200, 200, 200)).save(path, format="PNG")


def _wait_dict(job: JobDict) -> JobDict:
    wait = job["wait"]
    assert isinstance(wait, dict)
    return {str(key): value for key, value in wait.items()}


class ExtractResumeTests(unittest.TestCase):
    _tmp: tempfile.TemporaryDirectory[str] | None = None
    _old_data: Path | None = None
    _old_key: str | None = None
    _old_batch: int | None = None
    _old_interval: float | None = None
    _old_limit: int | None = None
    _old_cooldown: float | None = None
    _old_workers: int | None = None

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = settings.data_dir
        self._old_key = settings.gemini_api_key
        self._old_batch = settings.batch_size
        self._old_interval = settings.gemini_min_interval_seconds
        self._old_limit = settings.gemini_max_batches_per_day
        self._old_cooldown = settings.extract_resume_cooldown_seconds
        self._old_workers = settings.extract_max_concurrent_workers
        settings.data_dir = Path(self._tmp.name)
        settings.sessions_dir.mkdir(parents=True, exist_ok=True)
        settings.gemini_api_key = "test-key"
        settings.batch_size = 2
        settings.gemini_min_interval_seconds = 0
        settings.gemini_max_batches_per_day = 100
        settings.extract_resume_cooldown_seconds = 0
        settings.extract_max_concurrent_workers = 2
        extract_service.stop_worker_pool()
        extract_service._running_sessions.clear()
        extract_service._cancelled_sessions.clear()
        extract_service._last_gemini_call_at = None
        extract_service._resume_last_at.clear()

    @override
    def tearDown(self) -> None:
        extract_service.stop_worker_pool()
        extract_service._running_sessions.clear()
        extract_service._cancelled_sessions.clear()
        extract_service._last_gemini_call_at = None
        extract_service._resume_last_at.clear()
        assert self._old_data is not None
        assert self._old_key is not None
        assert self._old_batch is not None
        assert self._old_interval is not None
        assert self._old_limit is not None
        assert self._old_cooldown is not None
        assert self._old_workers is not None
        assert self._tmp is not None
        settings.data_dir = self._old_data
        settings.gemini_api_key = self._old_key
        settings.batch_size = self._old_batch
        settings.gemini_min_interval_seconds = self._old_interval
        settings.gemini_max_batches_per_day = self._old_limit
        settings.extract_resume_cooldown_seconds = self._old_cooldown
        settings.extract_max_concurrent_workers = self._old_workers
        self._tmp.cleanup()

    def _make_session(self, page_count: int = 4) -> str:
        meta = ingest_service.create_session("fixture.pdf")
        session_id = meta.session_id
        session_dir = settings.sessions_dir / session_id
        _ = (session_dir / "source.pdf").write_bytes(b"%PDF-1.4 fixture")
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
        wait_until: str | None = None,
    ) -> JobDict:
        now = extract_service._now_iso()
        libretto = "## Acte I\n\nTexte partiel.\n"
        extract_service.write_libretto(session_id, libretto)
        if status in {"paused", "error"} and wait_until is None and source == "gemini":
            wait_until = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat()
        job: JobDict = {
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
                "wait_until": wait_until,
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

    def test_start_on_paused_routes_to_resume(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2, source="server")
        before_md = extract_service.read_libretto(session_id)

        with patch.object(extract_service, "_spawn_worker") as spawn:
            job = extract_service.start_job(session_id)

        spawn.assert_called_once_with(session_id)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["pages_done"], 2)
        self.assertEqual(extract_service.read_libretto(session_id), before_md)

    def test_start_on_done_still_wipes_progress(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=4, status="done")
        job = extract_service._load_job(session_id)
        assert job is not None
        job["wait"] = None
        job["error"] = None
        extract_service._save_job(job)

        with patch.object(extract_service, "_spawn_worker"):
            started = extract_service.start_job(session_id)

        self.assertEqual(started["pages_done"], 0)
        self.assertEqual(extract_service.read_libretto(session_id), "")

    def test_save_job_atomic_roundtrip(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2, source="server")
        loaded = extract_service._load_job(session_id)
        assert loaded is not None
        loaded["pages_done"] = 3
        extract_service._save_job(loaded)
        again = extract_service._load_job(session_id)
        assert again is not None
        self.assertEqual(again["pages_done"], 3)
        leftovers = list(
            (settings.sessions_dir / session_id).glob(".extract_job.*.tmp")
        )
        self.assertEqual(leftovers, [])

    def test_spawn_uses_bounded_worker_pool(self) -> None:
        settings.extract_max_concurrent_workers = 3
        extract_service.stop_worker_pool()
        session_id = self._make_session(2)
        fake_pool = MagicMock()
        with patch(
            "app.services.extract.ThreadPoolExecutor",
            return_value=fake_pool,
        ) as pool_cls:
            extract_service._spawn_worker(session_id)
            pool_cls.assert_called_once()
            self.assertEqual(pool_cls.call_args.kwargs["max_workers"], 3)
            fake_pool.submit.assert_called_once()
            args = fake_pool.submit.call_args.args
            self.assertEqual(args[0], extract_service._worker_entry)
            self.assertEqual(args[1], session_id)
        self.assertIn(session_id, extract_service._running_sessions)
        extract_service._running_sessions.discard(session_id)
        extract_service.stop_worker_pool()

    def test_worker_pool_limits_concurrency(self) -> None:
        settings.extract_max_concurrent_workers = 1
        extract_service.stop_worker_pool()
        extract_service.start_worker_pool()

        session_a = self._make_session(2)
        session_b = self._make_session(2)
        for session_id in (session_a, session_b):
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
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )

        active = 0
        max_seen = 0
        lock = threading.Lock()
        release = threading.Event()
        entered = threading.Event()

        def fake_extract(
            _paths: object,
            _section: str,
            on_wait: object = None,
            should_abort: object = None,
        ) -> str:
            nonlocal active, max_seen
            with lock:
                active += 1
                max_seen = max(max_seen, active)
                if active == 1:
                    entered.set()
            self.assertTrue(release.wait(timeout=3.0))
            with lock:
                active -= 1
            return "# Pool\n\nTexte.\n"

        with (
            patch.object(
                gemini_client,
                "extract_libretto_markdown",
                side_effect=fake_extract,
            ),
            patch.object(usage_quota, "assert_under_daily_limit"),
            patch.object(usage_quota, "record_batch"),
        ):
            extract_service._spawn_worker(session_a)
            extract_service._spawn_worker(session_b)
            self.assertTrue(entered.wait(timeout=3.0))
            time.sleep(0.2)
            self.assertEqual(max_seen, 1)
            release.set()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not extract_service._running_sessions:
                    break
                time.sleep(0.05)

        self.assertEqual(max_seen, 1)
        self.assertEqual(extract_service._running_sessions, set())

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
            GeminiDailyQuotaError(
                MSG_DAILY_QUOTA,
                source="gemini",
                wait_until=next_gemini_reset_iso(),
                raw_quota_id="GenerateRequestsPerDay",
            ),
        )
        saved = extract_service._load_job(session_id)
        assert saved is not None
        self.assertEqual(saved["status"], "paused")
        self.assertEqual(saved["error"], MSG_DAILY_QUOTA)
        wait = _wait_dict(saved)
        self.assertEqual(wait["reason"], "daily")
        self.assertEqual(wait["source"], "gemini")
        self.assertEqual(wait["wait_until"], next_gemini_reset_iso())

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

        def fake_extract(image_paths, section, *, on_wait=None, should_abort=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return "## Acte I\n\nLot 1.\n"
            raise GeminiDailyQuotaError(MSG_DAILY_QUOTA, source="server")

        with (
            patch.object(
                gemini_client,
                "extract_libretto_markdown",
                side_effect=fake_extract,
            ),
            patch.object(usage_quota, "assert_under_daily_limit"),
            patch.object(usage_quota, "record_batch"),
            patch.object(
                extract_service,
                "_await_daily_quota_reset",
                return_value=False,
            ),
        ):
            extract_service.run_extraction(session_id)

        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "paused")
        self.assertEqual(job["pages_done"], 2)
        wait = job["wait"]
        assert isinstance(wait, dict)
        self.assertEqual(wait["source"], "server")
        self.assertIsNotNone(wait["wait_until"])
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
        wait = _wait_dict(job)
        self.assertEqual(wait["source"], "server")
        self.assertIsNotNone(wait["wait_until"])
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
        wait = _wait_dict(saved)
        self.assertEqual(wait["wait_until"], usage_quota.next_reset_iso())
        self.assertEqual(wait["source"], "server")

    def test_resume_then_run_continues_from_pages_done(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2, source="gemini")
        seen_starts: list[int] = []

        def fake_extract(image_paths, section, *, on_wait=None, should_abort=None):
            start = int(image_paths[0].stem.split("_")[1])
            seen_starts.append(start)
            return f"{section}\nPage {start}.\n"

        with (
            patch.object(extract_service, "_spawn_worker"),
            patch.object(
                gemini_client,
                "extract_libretto_markdown",
                side_effect=fake_extract,
            ),
            patch.object(usage_quota, "assert_under_daily_limit"),
            patch.object(usage_quota, "record_batch"),
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

    def test_resume_gemini_blocked_before_wait_until(self) -> None:
        session_id = self._make_session(4)
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self._write_partial_job(
            session_id,
            pages_done=2,
            source="gemini",
            wait_until=future,
        )

        with self.assertRaises(HTTPException) as ctx:
            extract_service.resume_job(session_id)
        self.assertEqual(ctx.exception.status_code, 429)
        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "paused")
        self.assertEqual(job["pages_done"], 2)
        wait = _wait_dict(job)
        self.assertEqual(wait["source"], "gemini")
        self.assertIn("Texte partiel", extract_service.read_libretto(session_id))

    def test_resume_cooldown_blocks_rapid_retries(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2, source="gemini")
        settings.extract_resume_cooldown_seconds = 30.0

        with patch.object(extract_service, "_spawn_worker"):
            extract_service.resume_job(session_id)

        self._write_partial_job(session_id, pages_done=2, source="gemini")
        with self.assertRaises(HTTPException) as ctx:
            extract_service.resume_job(session_id)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.detail, MSG_RESUME_COOLDOWN)

    def test_get_job_marks_orphan_running_as_stale(self) -> None:
        session_id = self._make_session(4)
        now = extract_service._now_iso()
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": "running",
                "source_name": "fixture.pdf",
                "pdf_page_count": 4,
                "batch_size": 2,
                "total_batches": 2,
                "pages_done": 2,
                "current_batch": 1,
                "error": None,
                "wait": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        job = extract_service.get_job(session_id)
        self.assertTrue(job["stale"])

        extract_service._running_sessions.add(session_id)
        try:
            job_alive = extract_service.get_job(session_id)
            self.assertFalse(job_alive["stale"])
        finally:
            extract_service._running_sessions.discard(session_id)

    def test_get_job_marks_orphan_daily_pause_as_stale(self) -> None:
        session_id = self._make_session(4)
        self._write_partial_job(session_id, pages_done=2, source="server")
        job = extract_service.get_job(session_id)
        self.assertTrue(job["stale"])

        extract_service._running_sessions.add(session_id)
        try:
            job_alive = extract_service.get_job(session_id)
            self.assertFalse(job_alive["stale"])
        finally:
            extract_service._running_sessions.discard(session_id)

    def test_resume_reattaches_orphan_running_worker(self) -> None:
        session_id = self._make_session(4)
        now = extract_service._now_iso()
        extract_service.write_libretto(session_id, "## Acte I\n\nTexte.\n")
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": "running",
                "source_name": "fixture.pdf",
                "pdf_page_count": 4,
                "batch_size": 2,
                "total_batches": 2,
                "pages_done": 2,
                "current_batch": 1,
                "error": None,
                "wait": None,
                "created_at": now,
                "updated_at": now,
            }
        )

        with patch.object(extract_service, "_spawn_worker") as spawn:
            job = extract_service.resume_job(session_id)

        spawn.assert_called_once_with(session_id)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["pages_done"], 2)
        self.assertIn("Texte", extract_service.read_libretto(session_id))

    def test_resume_orphan_blocked_while_rate_limit_wait_until_future(
        self,
    ) -> None:
        session_id = self._make_session(4)
        now = extract_service._now_iso()
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        extract_service.write_libretto(session_id, "## Acte I\n\nTexte.\n")
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": "running",
                "source_name": "fixture.pdf",
                "pdf_page_count": 4,
                "batch_size": 2,
                "total_batches": 2,
                "pages_done": 2,
                "current_batch": 1,
                "error": None,
                "wait": {
                    "reason": "rate_limit",
                    "message": MSG_RATE_LIMIT,
                    "wait_seconds": 300,
                    "wait_until": future,
                    "source": "gemini",
                },
                "created_at": now,
                "updated_at": now,
            }
        )

        with patch.object(extract_service, "_spawn_worker") as spawn:
            with self.assertRaises(HTTPException) as ctx:
                extract_service.resume_job(session_id)
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(ctx.exception.detail, MSG_RATE_LIMIT)
        spawn.assert_not_called()
        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["pages_done"], 2)
        wait = job.get("wait")
        assert isinstance(wait, dict)
        self.assertEqual(wait.get("reason"), "rate_limit")

    def test_resume_orphan_allowed_after_rate_limit_wait_until_past(
        self,
    ) -> None:
        session_id = self._make_session(4)
        now = extract_service._now_iso()
        past = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        extract_service.write_libretto(session_id, "## Acte I\n\nTexte.\n")
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": "running",
                "source_name": "fixture.pdf",
                "pdf_page_count": 4,
                "batch_size": 2,
                "total_batches": 2,
                "pages_done": 2,
                "current_batch": 1,
                "error": None,
                "wait": {
                    "reason": "rate_limit",
                    "message": MSG_RATE_LIMIT,
                    "wait_seconds": 12,
                    "wait_until": past,
                    "source": "gemini",
                },
                "created_at": now,
                "updated_at": now,
            }
        )

        with patch.object(extract_service, "_spawn_worker") as spawn:
            job = extract_service.resume_job(session_id)

        spawn.assert_called_once_with(session_id)
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["pages_done"], 2)

    def test_resume_rejects_when_worker_already_alive(self) -> None:
        session_id = self._make_session(4)
        now = extract_service._now_iso()
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": "running",
                "source_name": "fixture.pdf",
                "pdf_page_count": 4,
                "batch_size": 2,
                "total_batches": 2,
                "pages_done": 1,
                "current_batch": 1,
                "error": None,
                "wait": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        extract_service._running_sessions.add(session_id)
        try:
            with self.assertRaises(HTTPException) as ctx:
                extract_service.resume_job(session_id)
            self.assertEqual(ctx.exception.status_code, 409)
        finally:
            extract_service._running_sessions.discard(session_id)

    def test_run_extraction_pauses_on_gemini_daily_with_wait_until(self) -> None:
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
        reset = next_gemini_reset_iso()

        def fake_extract(image_paths, section, *, on_wait=None, should_abort=None):
            raise GeminiDailyQuotaError(
                MSG_DAILY_QUOTA,
                source="gemini",
                wait_until=reset,
                raw_quota_id="GenerateRequestsPerDay",
            )

        with (
            patch.object(
                gemini_client,
                "extract_libretto_markdown",
                side_effect=fake_extract,
            ),
            patch.object(usage_quota, "assert_under_daily_limit"),
            patch.object(usage_quota, "record_batch"),
            patch.object(
                extract_service,
                "_await_daily_quota_reset",
                return_value=False,
            ),
        ):
            extract_service.run_extraction(session_id)

        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "paused")
        self.assertEqual(job["pages_done"], 0)
        wait = job["wait"]
        assert isinstance(wait, dict)
        self.assertEqual(wait["source"], "gemini")
        self.assertEqual(wait["wait_until"], reset)
        self.assertEqual(job["error"], MSG_DAILY_QUOTA)

    def test_run_extraction_auto_resumes_after_server_daily(self) -> None:
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

        def fake_extract(image_paths, section, *, on_wait=None, should_abort=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return "## Acte I\n\nLot 1.\n"
            if calls["n"] == 2:
                raise GeminiDailyQuotaError(MSG_DAILY_QUOTA, source="server")
            return "## Acte I\n\nLot 1.\n\nLot 2.\n"

        with (
            patch.object(
                gemini_client,
                "extract_libretto_markdown",
                side_effect=fake_extract,
            ),
            patch.object(usage_quota, "assert_under_daily_limit"),
            patch.object(usage_quota, "record_batch"),
            patch.object(
                extract_service,
                "_await_daily_quota_reset",
                return_value=True,
            ) as await_reset,
        ):
            extract_service.run_extraction(session_id)

        await_reset.assert_called_once_with(session_id)
        self.assertEqual(calls["n"], 3)
        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["pages_done"], 4)
        self.assertIsNone(job["wait"])
        self.assertIn("Lot 2", extract_service.read_libretto(session_id))

    def test_run_extraction_auto_resumes_after_gemini_daily(self) -> None:
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
        reset = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()

        calls = {"n": 0}

        def fake_extract(image_paths, section, *, on_wait=None, should_abort=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise GeminiDailyQuotaError(
                    MSG_DAILY_QUOTA,
                    source="gemini",
                    wait_until=reset,
                    raw_quota_id="GenerateRequestsPerDay",
                )
            return f"## Acte I\n\nLot {calls['n']}.\n"

        with (
            patch.object(
                gemini_client,
                "extract_libretto_markdown",
                side_effect=fake_extract,
            ),
            patch.object(usage_quota, "assert_under_daily_limit"),
            patch.object(usage_quota, "record_batch"),
            patch.object(
                extract_service,
                "_await_daily_quota_reset",
                return_value=True,
            ) as await_reset,
        ):
            extract_service.run_extraction(session_id)

        await_reset.assert_called_once_with(session_id)
        self.assertEqual(calls["n"], 3)
        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["pages_done"], 4)

    def test_await_daily_quota_reset_returns_when_wait_until_past(self) -> None:
        session_id = self._make_session(4)
        past = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        self._write_partial_job(
            session_id,
            pages_done=2,
            source="gemini",
            wait_until=past,
        )

        with patch.object(extract_service.time, "sleep") as sleep:
            ok = extract_service._await_daily_quota_reset(session_id)

        self.assertTrue(ok)
        sleep.assert_not_called()

    def test_try_auto_resume_reattaches_orphan_running(self) -> None:
        session_id = self._make_session(4)
        now = extract_service._now_iso()
        extract_service._save_job(
            {
                "session_id": session_id,
                "status": "running",
                "source_name": "fixture.pdf",
                "pdf_page_count": 4,
                "batch_size": 2,
                "total_batches": 2,
                "pages_done": 2,
                "current_batch": 1,
                "error": None,
                "wait": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        with patch.object(extract_service, "_spawn_worker") as spawn:
            ok = extract_service.try_auto_resume(session_id)
        self.assertTrue(ok)
        spawn.assert_called_once_with(session_id)

    def test_try_auto_resume_daily_when_wait_until_past(self) -> None:
        session_id = self._make_session(4)
        past = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        self._write_partial_job(
            session_id,
            pages_done=2,
            source="gemini",
            wait_until=past,
        )
        with patch.object(extract_service, "_spawn_worker") as spawn:
            ok = extract_service.try_auto_resume(session_id)
        self.assertTrue(ok)
        spawn.assert_called_once_with(session_id)
        job = extract_service._load_job(session_id)
        assert job is not None
        self.assertEqual(job["status"], "running")
        self.assertIsNone(job["wait"])

    def test_try_auto_resume_skips_daily_before_wait_until(self) -> None:
        session_id = self._make_session(4)
        future = (
            datetime.now(timezone.utc) + timedelta(hours=2)
        ).isoformat()
        self._write_partial_job(
            session_id,
            pages_done=2,
            source="gemini",
            wait_until=future,
        )
        with patch.object(extract_service, "_spawn_worker") as spawn:
            ok = extract_service.try_auto_resume(session_id)
        self.assertFalse(ok)
        spawn.assert_not_called()

    def test_reconcile_orphaned_jobs_resumes_stale_daily(self) -> None:
        session_id = self._make_session(4)
        past = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        self._write_partial_job(
            session_id,
            pages_done=2,
            source="server",
            wait_until=past,
        )
        with patch.object(extract_service, "_spawn_worker") as spawn:
            count = extract_service.reconcile_orphaned_jobs()
        self.assertEqual(count, 1)
        spawn.assert_called_once_with(session_id)

    def test_await_daily_quota_reset_sleeps_then_resumes(self) -> None:
        session_id = self._make_session(4)
        future = (
            datetime.now(timezone.utc) + timedelta(seconds=45)
        ).isoformat()
        self._write_partial_job(
            session_id,
            pages_done=2,
            source="server",
            wait_until=future,
        )

        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            job = extract_service._load_job(session_id)
            assert job is not None
            wait = _wait_dict(job)
            wait["wait_until"] = (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat()
            job["wait"] = wait
            extract_service._save_job(job)

        with (
            patch.object(extract_service.time, "sleep", side_effect=fake_sleep),
            patch.object(usage_quota, "assert_under_daily_limit"),
        ):
            ok = extract_service._await_daily_quota_reset(session_id)

        self.assertTrue(ok)
        self.assertEqual(len(sleeps), 1)
        self.assertLessEqual(sleeps[0], extract_service._DAILY_WAIT_CHUNK_SECONDS)

    def test_request_cancel_stops_worker_before_gemini(self) -> None:
        session_id = self._make_session(page_count=4)
        calls = {"n": 0}

        def fake_extract(
            _paths: object,
            _section: str,
            on_wait: object = None,
            should_abort: object = None,
        ) -> str:
            calls["n"] += 1
            return "# Cancelled\n"

        with patch.object(extract_service, "_spawn_worker"):
            job = extract_service.start_job(session_id)
        self.assertEqual(job["status"], "running")
        extract_service.request_cancel(session_id)
        ingest_service.delete_session(session_id)

        with patch.object(
            gemini_client,
            "extract_libretto_markdown",
            side_effect=fake_extract,
        ):
            extract_service.run_extraction(session_id)

        self.assertEqual(calls["n"], 0)
        self.assertTrue(extract_service.is_cancelled(session_id))
        self.assertFalse((settings.sessions_dir / session_id).exists())
        self.assertFalse(extract_service.try_auto_resume(session_id))

    def test_gemini_rate_limit_wait_aborts_when_cancelled(self) -> None:
        aborted = {"n": 0}

        def should_abort() -> bool:
            aborted["n"] += 1
            return aborted["n"] >= 2

        with self.assertRaises(GeminiCancelledError):
            gemini_client._interruptible_sleep(30.0, should_abort)
        self.assertGreaterEqual(aborted["n"], 2)


class UsageQuotaSourceTests(unittest.TestCase):
    _tmp: tempfile.TemporaryDirectory[str] | None = None
    _old_data: Path | None = None
    _old_limit: int | None = None

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = settings.data_dir
        self._old_limit = settings.gemini_max_batches_per_day
        settings.data_dir = Path(self._tmp.name)
        settings.gemini_max_batches_per_day = 1

    @override
    def tearDown(self) -> None:
        assert self._old_data is not None
        assert self._old_limit is not None
        assert self._tmp is not None
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
    _ = unittest.main()
