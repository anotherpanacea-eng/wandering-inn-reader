#!/usr/bin/env python3
"""Run check.sh with the current Python and all caches outside the worktree."""

from __future__ import annotations

from pathlib import Path

try:
    from tools.land_merge_train import LandingError, _run_gate
except ModuleNotFoundError:
    from land_merge_train import LandingError, _run_gate


def main() -> int:
    try:
        _run_gate(Path.cwd())
    except (LandingError, OSError, UnicodeError) as exc:
        print(f"local gate: REFUSED: {exc}")
        return 1
    print("local gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
