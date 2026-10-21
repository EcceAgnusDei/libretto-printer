from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.services import usage_quota  # noqa: E402
from app.services.gemini_errors import GeminiDailyQuotaError, MSG_DAILY_QUOTA  # noqa: E402


class UsageQuotaBatchesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = settings.data_dir
        self._old_limit = settings.gemini_max_batches_per_day
        self._old_batch = settings.batch_size
        settings.data_dir = Path(self._tmp.name)
        settings.gemini_max_batches_per_day = 3
        settings.batch_size = 5

    def tearDown(self) -> None:
        settings.data_dir = self._old_data
        settings.gemini_max_batches_per_day = self._old_limit
        settings.batch_size = self._old_batch
        self._tmp.cleanup()

    def test_batches_needed(self) -> None:
        self.assertEqual(usage_quota.batches_needed(187), 39)
        self.assertEqual(usage_quota.batches_needed(10), 3)
        self.assertEqual(usage_quota.batches_needed(5), 2)

    def test_record_and_remaining(self) -> None:
        usage_quota.record_batch()
        usage_quota.record_batch()
        usage = usage_quota.get_usage()
        self.assertEqual(usage["batches_used"], 2)
        self.assertEqual(usage["batches_remaining"], 1)

    def test_daily_limit(self) -> None:
        usage_quota.record_batch()
        usage_quota.record_batch()
        usage_quota.record_batch()
        with self.assertRaises(GeminiDailyQuotaError) as ctx:
            usage_quota.assert_under_daily_limit()
        self.assertEqual(str(ctx.exception), MSG_DAILY_QUOTA)

    def test_reserve_batch_respects_limit(self) -> None:
        usage_quota.reserve_batch()
        usage_quota.reserve_batch()
        usage_quota.reserve_batch()
        with self.assertRaises(GeminiDailyQuotaError) as ctx:
            usage_quota.reserve_batch()
        self.assertEqual(str(ctx.exception), MSG_DAILY_QUOTA)
        usage = usage_quota.get_usage()
        self.assertEqual(usage["batches_used"], 3)
        self.assertEqual(usage["batches_remaining"], 0)

    def test_reserve_batch_concurrent_does_not_exceed_limit(self) -> None:
        settings.gemini_max_batches_per_day = 10
        workers = 40
        barrier = threading.Barrier(workers)
        successes: list[bool] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            try:
                usage_quota.reserve_batch()
                with lock:
                    successes.append(True)
            except GeminiDailyQuotaError:
                with lock:
                    successes.append(False)
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(sum(1 for ok in successes if ok), 10)
        self.assertEqual(sum(1 for ok in successes if not ok), 30)
        usage = usage_quota.get_usage()
        self.assertEqual(usage["batches_used"], 10)
        self.assertEqual(usage["batches_remaining"], 0)

    def test_needs_confirmation(self) -> None:
        usage_quota.record_batch()  # 2 remaining
        check = usage_quota.needs_over_quota_confirmation(20)  # 4 extract + 1 review
        self.assertTrue(check["requires_confirmation"])
        self.assertEqual(check["batches_needed"], 5)
        self.assertEqual(check["batches_remaining"], 2)
        self.assertIn("Continuer", check["message"] or "")

    def test_no_confirmation_when_enough(self) -> None:
        check = usage_quota.needs_over_quota_confirmation(5)  # 1 extract + 1 review, limit 3
        self.assertFalse(check["requires_confirmation"])

    def test_next_reset_at_is_next_utc_midnight(self) -> None:
        reset = usage_quota.next_reset_at()
        now = datetime.now(timezone.utc)
        self.assertEqual(reset.tzinfo, timezone.utc)
        self.assertEqual(reset.hour, 0)
        self.assertEqual(reset.minute, 0)
        self.assertEqual(reset.second, 0)
        self.assertGreater(reset, now)
        self.assertEqual(reset.date(), now.date() + timedelta(days=1))

    def test_get_usage_exposes_reset_metadata(self) -> None:
        usage = usage_quota.get_usage()
        self.assertEqual(usage["quota_timezone"], "UTC")
        self.assertEqual(usage["next_reset_at"], usage_quota.next_reset_iso())
        self.assertEqual(usage["date"], usage_quota._today())

    def test_atomic_save_replaces_usage_file(self) -> None:
        usage_quota.reserve_batch()
        path = usage_quota._usage_path()
        self.assertTrue(path.is_file())
        raw = path.read_text(encoding="utf-8")
        self.assertIn('"batches": 1', raw)
        leftovers = list(settings.data_dir.glob(".gemini_usage.*.tmp"))
        self.assertEqual(leftovers, [])

    def test_record_batch_increments_after_success_path(self) -> None:
        usage_quota.assert_under_daily_limit()
        usage_quota.record_batch()
        usage = usage_quota.get_usage()
        self.assertEqual(usage["batches_used"], 1)


if __name__ == "__main__":
    unittest.main()
