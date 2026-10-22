"""Synthetic chapter selector and URL-list behavior; no TOC fetch or HTML parser."""

import contextlib
import importlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
chapters_cli = importlib.import_module("list_chapters")


def chapter(volume, slug, label):
    return {"volume": volume, "slug": slug, "label": label,
            "url": f"https://example.invalid/{slug}/"}


CHAPTERS = [
    chapter(6, "6-32", "Chapter 6.32"),
    chapter(6, "6-33-e", "Chapter 6.33 E"),
    chapter(6, "interlude-green", "Interlude Green"),
    chapter(6, "6-47-e", "Chapter 6.47 E"),
    chapter(7, "7-01", "Chapter 7.01"),
]


class SelectorTests(unittest.TestCase):
    def test_normalization_and_delimited_prefixes(self):
        self.assertEqual(chapters_cli.norm("  6.33 E  "), "6-33-e")
        self.assertEqual(chapters_cli.match_idx(CHAPTERS, "  6.33 E  "), 1)
        self.assertEqual(chapters_cli.match_idx(CHAPTERS, "6.33"), 1)

        candidates = [chapter(6, "6-330-e", "Different number"),
                      chapter(6, "6-33", "Exact number"),
                      chapter(6, "6-33-e", "Suffixed number")]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(chapters_cli.match_idx(candidates, "6.33"), 1)
        with self.assertRaisesRegex(SystemExit, "No chapter matched"):
            chapters_cli.match_idx(candidates, "6.3")

    def test_ambiguous_start_is_first_with_warning_and_end_is_last(self):
        candidates = [chapter(6, "6-33-e", "First match"),
                      chapter(6, "interlude-green", "Interlude"),
                      chapter(6, "6-33-t", "Last match")]
        warning = io.StringIO()
        with contextlib.redirect_stderr(warning):
            self.assertEqual(chapters_cli.match_idx(candidates, "6.33"), 0)
            self.assertEqual(chapters_cli.match_idx(candidates, "6.33", last=True), 2)
        self.assertIn("matched 2 chapters; using the first", warning.getvalue())
        self.assertIn("6-33-e", warning.getvalue())
        self.assertEqual(warning.getvalue().count("matched 2 chapters"), 1)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "urls.txt"

    def run_cli(self, args=(), chapters=CHAPTERS):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (patch.object(sys, "argv", ["list_chapters.py", *args]),
              patch.object(chapters_cli, "fetch", return_value="synthetic TOC") as fetch,
              patch.object(chapters_cli, "parse_toc", return_value=chapters) as parse,
              contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr)):
            try:
                chapters_cli.main()
                error = None
            except SystemExit as exc:
                error = str(exc)
        parse.assert_called_once_with("synthetic TOC")
        return stdout.getvalue(), stderr.getvalue(), error, fetch

    def test_inclusive_range_keeps_interlude_and_toc_order_in_url_file(self):
        stdout, stderr, error, fetch = self.run_cli(
            ["--from", "6.33", "--to", "6.47", "--out", str(self.out),
             "--toc-url", "https://example.invalid/invented-toc"])
        self.assertIsNone(error)
        fetch.assert_called_once_with("https://example.invalid/invented-toc")
        lines = self.out.read_text(encoding="utf-8").splitlines()
        self.assertIn("6.33", lines[0])
        self.assertIn("6.47", lines[0])
        self.assertIn("3 chapters", lines[1])
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3:], [c["url"] for c in CHAPTERS[1:4]])
        self.assertIn("Wrote", stdout)
        self.assertNotIn("Interlude Green", stdout)
        self.assertEqual(stderr, "")

    def test_cli_uses_first_ambiguous_start_and_last_ambiguous_end(self):
        fixture = [chapter(6, "6-33-e", "First start"),
                   chapter(6, "interlude-green", "Between starts"),
                   chapter(6, "6-33-t", "Last start"),
                   chapter(6, "interlude-blue", "Between endpoints"),
                   chapter(6, "6-47-e", "First end"),
                   chapter(6, "6-47-t", "Last end")]
        _, stderr, error, _ = self.run_cli(
            ["--from", "6.33", "--to", "6.47", "--out", str(self.out)], fixture)
        self.assertIsNone(error)
        self.assertIn("matched 2 chapters; using the first (6-33-e)", stderr)
        self.assertEqual(self.out.read_text(encoding="utf-8").splitlines()[3:],
                         [c["url"] for c in fixture])

    def test_volume_list_and_file_use_only_requested_volume_in_order(self):
        stdout, stderr, error, fetch = self.run_cli(
            ["--volume", "6", "--list", "--out", str(self.out)])
        self.assertIsNone(error)
        fetch.assert_called_once_with(chapters_cli.TOC_URL)
        self.assertEqual(stderr, "")
        self.assertIn("[V6]", stdout)
        self.assertIn("Interlude Green", stdout)
        self.assertNotIn("Chapter 7.01", stdout)
        self.assertLess(stdout.index("Chapter 6.32"), stdout.index("Interlude Green"))
        self.assertLess(stdout.index("Interlude Green"), stdout.index("Chapter 6.47 E"))
        lines = self.out.read_text(encoding="utf-8").splitlines()
        self.assertIn("Volume 6", lines[0])
        self.assertEqual(lines[3:], [c["url"] for c in CHAPTERS[:4]])

    def test_no_output_path_lists_chapters_and_overview_without_writing(self):
        stdout, _, error, _ = self.run_cli(["--volume", "7"])
        self.assertIsNone(error)
        self.assertIn("Chapter 7.01", stdout)
        self.assertIn("1 chapters.", stdout)
        self.assertFalse(self.out.exists())

        stdout, _, error, _ = self.run_cli()
        self.assertIsNone(error)
        self.assertIn("5 chapters across 2 volumes", stdout)
        self.assertIn("Volume 6: 4 chapters", stdout)
        self.assertIn("Volume 7: 1 chapters", stdout)
        self.assertFalse(self.out.exists())

    def test_invalid_selection_refuses_before_writing(self):
        cases = [
            ([], "No chapters parsed", []),
            (["--volume", "9"], "No chapters found for Volume 9", CHAPTERS),
            (["--from", "6.99", "--to", "7.01"], "No chapter matched", CHAPTERS),
            (["--from", "6.33", "--to", "6.99"], "No chapter matched", CHAPTERS),
            (["--from", "7.01", "--to", "6.33"], "comes after", CHAPTERS),
        ]
        for args, message, fixture in cases:
            with self.subTest(args=args, message=message):
                stdout, _, error, _ = self.run_cli(
                    [*args, "--out", str(self.out)], fixture)
                self.assertIsNotNone(error)
                self.assertIn(message, error)
                self.assertEqual(stdout, "")
                self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
