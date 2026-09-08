#!/usr/bin/env python3
"""Run Node tests with TAP output and fail if any test is skipped."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence


SKIP_RE = re.compile(r"(?im)^\s*(?:ok|not ok)\b[^\r\n]*#\s*SKIP\b")


def run_tests(files: Sequence[Path], *, node: str = "node", emit: bool = True) -> int:
    command = [node, "--test", "--test-reporter=tap", *(str(path) for path in files)]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="strict",
            check=False,
        )
    except (OSError, UnicodeError) as exc:
        if emit:
            print(f"node-gate: REFUSED: {exc}", file=sys.stderr)
        return 1
    if emit:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
    if result.returncode:
        return result.returncode
    if SKIP_RE.search(result.stdout):
        if emit:
            print("node-gate: REFUSED: skipped test detected", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--node", default="node")
    args = parser.parse_args(argv)
    return run_tests(args.files, node=args.node)


if __name__ == "__main__":
    raise SystemExit(main())
