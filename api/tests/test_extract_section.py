# pyright: reportPrivateUsage=false

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.extract import (  # noqa: E402
    apply_section_update,
    split_current_section,
)


class SplitCurrentSectionTests(unittest.TestCase):
    def test_empty_returns_whole_as_section(self) -> None:
        prefix, section = split_current_section("")
        self.assertEqual(prefix, "")
        self.assertEqual(section, "")

    def test_no_heading_returns_whole_as_section(self) -> None:
        md = "Texte sans titre.\n"
        prefix, section = split_current_section(md)
        self.assertEqual(prefix, "")
        self.assertEqual(section, md)

    def test_splits_from_last_h3(self) -> None:
        md = (
            "# Carmen\n\n"
            "## Acte I\n\n"
            "Intro.\n\n"
            "### Scène 1\n\n"
            "Texte 1.\n\n"
            "### Scène 2\n\n"
            "Texte 2.\n"
        )
        prefix, section = split_current_section(md)
        self.assertTrue(section.startswith("### Scène 2"))
        self.assertIn("### Scène 1", prefix)
        self.assertNotIn("### Scène 2", prefix)
        self.assertIn("# Carmen", prefix)
        self.assertIn("## Acte I", prefix)

    def test_splits_from_last_h2_when_no_h3(self) -> None:
        md = "# Carmen\n\n## Acte I\n\nTexte.\n\n## Acte II\n\nSuite.\n"
        prefix, section = split_current_section(md)
        self.assertTrue(section.startswith("## Acte II"))
        self.assertIn("## Acte I", prefix)
        self.assertNotIn("## Acte II", prefix)

    def test_splits_from_h1_when_only_title(self) -> None:
        md = "# Carmen\n\nOuverture.\n"
        prefix, section = split_current_section(md)
        self.assertEqual(prefix, "")
        self.assertTrue(section.startswith("# Carmen"))

    def test_ignores_h4(self) -> None:
        md = "## Acte I\n\n#### Note\n\nTexte.\n"
        prefix, section = split_current_section(md)
        self.assertEqual(prefix, "")
        self.assertTrue(section.startswith("## Acte I"))
        self.assertIn("#### Note", section)

    def test_heading_requires_space(self) -> None:
        md = "##Acte\n\nTexte.\n"
        prefix, section = split_current_section(md)
        self.assertEqual(prefix, "")
        self.assertEqual(section, md)


class ApplySectionUpdateTests(unittest.TestCase):
    def test_replaces_from_last_heading(self) -> None:
        current = (
            "# Carmen\n\n"
            "## Acte I\n\n"
            "### Scène 1\n\n"
            "Ancien.\n\n"
            "### Scène 2\n\n"
            "Partiel.\n"
        )
        updated = "### Scène 2\n\nComplet.\n\n### Scène 3\n\nNouveau.\n"
        result = apply_section_update(current, updated)
        self.assertIn("### Scène 1", result)
        self.assertIn("Ancien.", result)
        self.assertIn("### Scène 2", result)
        self.assertIn("Complet.", result)
        self.assertIn("### Scène 3", result)
        self.assertNotIn("Partiel.", result)

    def test_empty_update_keeps_markdown(self) -> None:
        current = "## Acte I\n\nTexte.\n"
        self.assertEqual(apply_section_update(current, "  "), current)

    def test_new_heading_locks_previous_section(self) -> None:
        current = (
            "## Acte II\n\n"
            "### Scène 5\n\n"
            "Avant.\n\n"
            "### Scène 6\n\n"
            "*Zélidie, Zaïs.*\n\n"
            "**Zélidie.**\n"
            "Ciel est ce vous ! en croirai-je mes yeux ?\n\n"
            "**Zaïs.**\n"
            "Moi, vous fuir ! quel ordre funeste !\n\n"
            "---Zélidie---\n"
            "---(aucun)---\n"
        )
        updated = (
            "## Acte III\n\n"
            "### Scène 1\n\n"
            "**Cindor.**\n"
            "Vous estes obéï.\n"
        )
        result = apply_section_update(current, updated)
        self.assertIn("en croirai-je mes yeux", result)
        self.assertIn("Moi, vous fuir", result)
        self.assertIn("## Acte III", result)
        self.assertIn("Vous estes obéï", result)
        self.assertNotIn("---Zélidie---", result)
        prefix, section = split_current_section(result)
        self.assertIn("### Scène 6", prefix)
        self.assertIn("en croirai-je mes yeux", prefix)
        self.assertTrue(section.startswith("### Scène 1"))

    def test_same_heading_still_replaces(self) -> None:
        current = "### Scène 6\n\nAncien début.\n\n---Zaïs---\n"
        updated = "### Scène 6\n\nSuite complète.\n\n---Zaïs---\n"
        result = apply_section_update(current, updated)
        self.assertIn("Suite complète", result)
        self.assertNotIn("Ancien début", result)


if __name__ == "__main__":
    _ = unittest.main()
