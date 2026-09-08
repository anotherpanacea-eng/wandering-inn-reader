#!/usr/bin/env python3
"""Regression: mandatory Node suites may not pass by skipping cases."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.run_node_tests import run_tests


class NodeGateTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "the repository gate requires Node")
    def test_passes_complete_suite_and_refuses_skip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            passing = root / "passing.mjs"
            skipped = root / "skipped.mjs"
            passing.write_text(
                "import {test} from 'node:test';test('runs',()=>{});\n", encoding="utf-8",
            )
            skipped.write_text(
                "import {test} from 'node:test';test.skip('must run',()=>{});\n", encoding="utf-8",
            )
            self.assertEqual(run_tests([passing], emit=False), 0)
            self.assertNotEqual(run_tests([skipped], emit=False), 0)


if __name__ == "__main__":
    unittest.main()
