from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.services import gemini_client  # noqa: E402
from app.services import gemini_token_usage  # noqa: E402


class GeminiTokenUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_data = settings.data_dir
        settings.data_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        settings.data_dir = self._old_data
        self._tmp.cleanup()

    def _read_lines(self) -> list[dict]:
        path = settings.data_dir / "gemini_token_usage.jsonl"
        self.assertTrue(path.is_file())
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def test_record_call_appends_json_line(self) -> None:
        gemini_token_usage.record_call(
            {
                "kind": "extract",
                "ok": True,
                "prompt_token_count": 10,
            }
        )
        gemini_token_usage.record_call(
            {
                "kind": "review",
                "ok": True,
                "prompt_token_count": 20,
            }
        )
        rows = self._read_lines()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["kind"], "extract")
        self.assertEqual(rows[0]["prompt_token_count"], 10)
        self.assertIn("at", rows[0])
        self.assertRegex(rows[0]["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(rows[1]["kind"], "review")

    def test_record_call_concurrent_two_lines(self) -> None:
        barrier = threading.Barrier(2)

        def worker(kind: str) -> None:
            barrier.wait()
            gemini_token_usage.record_call({"kind": kind, "ok": True})

        threads = [
            threading.Thread(target=worker, args=("extract",)),
            threading.Thread(target=worker, args=("review",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        rows = self._read_lines()
        self.assertEqual(len(rows), 2)
        kinds = {row["kind"] for row in rows}
        self.assertEqual(kinds, {"extract", "review"})

    def test_record_call_io_error_does_not_raise(self) -> None:
        with patch("pathlib.Path.open", side_effect=OSError("disk full")):
            gemini_token_usage.record_call({"kind": "extract", "ok": True})

    def test_generate_markdown_records_usage_metadata(self) -> None:
        usage = SimpleNamespace(
            prompt_token_count=100,
            candidates_token_count=40,
            thoughts_token_count=5,
            cached_content_token_count=None,
            tool_use_prompt_token_count=None,
            total_token_count=145,
            prompt_tokens_details=[
                SimpleNamespace(modality=SimpleNamespace(value="IMAGE"), token_count=80),
                SimpleNamespace(modality="TEXT", token_count=20),
            ],
        )
        response = SimpleNamespace(text="# Titre\n", usage_metadata=usage)
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = response

        with patch.object(gemini_client, "_get_client", return_value=mock_client):
            text = gemini_client._generate_markdown(
                [],
                log_label="extract",
                session_id="sess-1",
                source_name="carmen.pdf",
                batch_index=2,
                images=3,
                image_bytes=999,
                section_chars=12,
            )

        self.assertEqual(text, "# Titre")
        rows = self._read_lines()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["kind"], "extract")
        self.assertEqual(row["session_id"], "sess-1")
        self.assertEqual(row["source_name"], "carmen.pdf")
        self.assertEqual(row["batch_index"], 2)
        self.assertEqual(row["images"], 3)
        self.assertEqual(row["image_bytes"], 999)
        self.assertEqual(row["section_chars"], 12)
        self.assertTrue(row["ok"])
        self.assertEqual(row["prompt_token_count"], 100)
        self.assertEqual(row["candidates_token_count"], 40)
        self.assertEqual(row["thoughts_token_count"], 5)
        self.assertEqual(row["total_token_count"], 145)
        self.assertEqual(
            row["prompt_tokens_details"],
            [
                {"modality": "IMAGE", "token_count": 80},
                {"modality": "TEXT", "token_count": 20},
            ],
        )

    def test_generate_markdown_records_failure_without_raising_from_logger(
        self,
    ) -> None:
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("boom")
        old_retries = settings.extract_max_retries
        settings.extract_max_retries = 0
        try:
            with patch.object(gemini_client, "_get_client", return_value=mock_client):
                with self.assertRaises(RuntimeError):
                    gemini_client._generate_markdown(
                        [],
                        log_label="review",
                        session_id="sess-err",
                    )
        finally:
            settings.extract_max_retries = old_retries

        rows = self._read_lines()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["ok"])
        self.assertEqual(rows[0]["error_type"], "RuntimeError")
        self.assertEqual(rows[0]["kind"], "review")

    def test_low_thinking_for_piece_info_and_review_only(self) -> None:
        seen: list[types.ThinkingLevel | None] = []

        def fake_generate(_parts: list[object], **kwargs: object) -> str:
            level = kwargs.get("thinking_level")
            if level is None or isinstance(level, types.ThinkingLevel):
                seen.append(level)
            return "ok"

        image = Path(self._tmp.name) / "page.png"
        image.write_bytes(b"\x89PNG\r\n")
        with patch.object(
            gemini_client,
            "_generate_markdown",
            side_effect=fake_generate,
        ):
            self.assertEqual(gemini_client.extract_piece_info([image]), "ok")
            self.assertEqual(gemini_client.review_libretto_markdown("# T\n"), "ok")
            self.assertEqual(
                gemini_client.extract_libretto_markdown([image], ""),
                "ok",
            )

        self.assertEqual(
            seen,
            [
                types.ThinkingLevel.LOW,
                types.ThinkingLevel.LOW,
                None,
            ],
        )

    def test_generate_markdown_sends_low_thinking_config(self) -> None:
        response = SimpleNamespace(text="ok", usage_metadata=None)
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = response
        with patch.object(gemini_client, "_get_client", return_value=mock_client):
            gemini_client._generate_markdown(
                [],
                log_label="piece_info",
                thinking_level=types.ThinkingLevel.LOW,
            )
            gemini_client._generate_markdown([], log_label="extract")

        configs = [
            call.kwargs["config"]
            for call in mock_client.models.generate_content.call_args_list
        ]
        self.assertEqual(
            configs[0].thinking_config.thinking_level,
            types.ThinkingLevel.LOW,
        )
        self.assertFalse(configs[0].thinking_config.include_thoughts)
        self.assertIsNone(configs[1].thinking_config)


if __name__ == "__main__":
    unittest.main()
