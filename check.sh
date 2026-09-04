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
PYTHON="${PYTHON:-python3}"

echo "→ IP-limit guard (≤20s voice / ≤500 words text, no bulk artifacts)"
"$PYTHON" tools/check_ip_limits.py

echo "→ safe-pattern lint (no HTML-string DOM / parenthesised eval-mode call)"
"$PYTHON" tools/check_safe_patterns.py

echo "→ byte-compile pipeline + tools + tests"
"$PYTHON" -m py_compile pipeline/*.py tools/*.py tests/*.py

echo "→ align.py data-contract test"
"$PYTHON" tests/test_align.py

echo "→ edit-aware aligner cut-detection / gap-emit test (synthetic, no GPU)"
"$PYTHON" tests/test_editaware.py
echo "→ opt-in fuzzy ASR overlap boundary/default-off test"
"$PYTHON" tests/test_fuzzy_overlap.py
echo "→ wps-gate threshold-logic test"
"$PYTHON" tests/test_wps_check.py
echo "→ mandatory Node no-skip gate regression"
"$PYTHON" tests/test_node_gate.py
echo "→ draft-first merge-train policy test"
"$PYTHON" tests/test_merge_train.py

# Player behavior is a JavaScript/security boundary, so the dependency-free Node
# runner is mandatory and neither suite may silently skip.
if ! command -v node >/dev/null 2>&1; then
  echo "✗ player behavioral gates require node" >&2
  exit 1
fi
echo "→ paged-anchor regression test (index.html pagedAnchors; Codex P1 PR #27)"
"$PYTHON" tools/run_node_tests.py tests/test_paged_anchor.mjs
echo "→ generic-ingest behavioral/security test (mandatory, no skips)"
"$PYTHON" tools/run_node_tests.py tests/test_generic_ingest.mjs

echo "✓ all local checks passed"
