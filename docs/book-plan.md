# Tier-1 book plan

`python pipeline/process_book.py book.json --plan` describes the existing m4b
pipeline as JSON on stdout. There is no execution mode. It reads the manifest
only, creates no book artifacts, and does not check whether inputs or tools exist.
This is the bounded plan-only increment of issue54; the full book-pack workflow
remains a proposal.

```json
{
  "book": 1,
  "title": "Invented example",
  "tier": "m4b",
  "audio": {"m4b": "audio/book.m4b"},
  "text": {"source": "live", "toc_from": "1.00", "toc_to": "1.02"}
}
```

Paths are local and resolved lexically against the manifest's directory, without
filesystem probing or environment interpolation. Native absolute and UNC paths
are supported; URL-shaped and NUL-containing paths are rejected. Chapter labels
are preserved; the planner does not infer ordering or fetch the official TOC.

Only the fields shown above and optional `map` and `ship` objects are supported.
Unknown fields, duplicate JSON keys, nonfinite constants and unsupported routes
fail with exit2 and a named diagnostic. Book is a positive integer, not a boolean;
titles and labels are nonblank strings.

`map` optionally contains `m4b_starts`, `m4b_end`, and `confirmed` (default false).
Starts and end must be supplied together; starts are nonempty, strictly increasing
nonnegative integers and end exceeds the last start. Booleans are not indices.
Confirmation requires a supplied map and remains an unverified user assertion.
`ship` may contain a local `dropbox_dir`; naming it grants no transfer authority.

The plan lists range/text acquisition, probing, Gate A, units, WAV cuts,
chapter alignment, playback cuts, packaging, verification, Gate B and delivery.
Missing map parameters remain explicit. These descriptions are not runnable
commands. Gate B stays pending regardless of map confirmation: any verification
FLAG at exit0 requires boundary review; nonzero exit, missing/malformed summaries,
zero sampled points or inconsistent counts block shipping. The planner performs
no verification and accepts no boundary-approval latch.

No private audiobook is needed to inspect a plan. Processed-book acceptance,
execution/resume, other format/text routes, boundary adjudication and delivery
require separate reviewed increments and their existing authorizations.
