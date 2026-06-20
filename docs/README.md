# Design docs

Forward-looking design proposals for the pipeline (not yet implemented as a whole).
Generated from a 2026-06-19 review of the Codex audit. Paths referencing
`D:/Code-PC/_inn_work/` are the operator's local scratch dir.

- **spec-book-pack.md** — a unified `process_book.py` driver + per-book manifest that
  routes the three audiobook format tiers through one cut→align→verify→package spine.
- **spec-qa-workbench.md** — `align_qa.py` (drift `profile`, no-GPU `wps-check`,
  declarative `correct`). Implemented in PR #19.
- **promotion-review.md** — the plan for promoting `_inn_work` scratch tools into
  `pipeline/` (PRs #17/#18 done; `--wayback` fetch follow-up pending).
