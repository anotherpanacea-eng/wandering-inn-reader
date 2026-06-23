#!/bin/sh
# check.sh — the local "CI" gate. Runs every check AGENTS.md §"Verify before
# claiming green" requires, in one command. No network, no build step, no
# device — these are the checks a cloud session CAN run (the live render check
# still needs a browser; see AGENTS.md). Exit non-zero on the first failure.
#
#   ./check.sh
#
# There is no GitHub-Actions workflow (the repo token has no `workflow` scope and
# the repo is deliberately dependency-free); this script IS the gate. The IP guard
# and the safe-pattern lint also run from the pre-commit hook (.githooks/pre-commit).
set -e

root="$(git rev-parse --show-toplevel)"
cd "$root"

echo "→ IP-limit guard (≤20s voice / ≤500 words text, no bulk artifacts)"
python3 tools/check_ip_limits.py

echo "→ safe-pattern lint (no HTML-string DOM / parenthesised eval-mode call)"
python3 tools/check_safe_patterns.py

echo "→ byte-compile pipeline + tools + tests"
python3 -m py_compile pipeline/*.py tools/*.py tests/*.py

echo "→ align.py data-contract test"
python3 tests/test_align.py

echo "→ edit-aware aligner cut-detection / gap-emit test (synthetic, no GPU)"
python3 tests/test_editaware.py
echo "→ wps-gate threshold-logic test"
python3 tests/test_wps_check.py

# Player logic check: the paged-mode page-anchor function (index.html). Uses Node's
# built-in test runner only (no npm/deps). Skips with a notice if Node is absent so
# the gate stays runnable on a Python-only box, but runs in CI / on dev machines.
if command -v node >/dev/null 2>&1; then
  echo "→ paged-anchor regression test (index.html pagedAnchors; Codex P1 PR #27)"
  node --test tests/test_paged_anchor.mjs
else
  echo "→ paged-anchor regression test SKIPPED (node not found)"
fi

echo "✓ all local checks passed"
