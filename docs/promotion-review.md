# Promotion review — `_inn_work/` scratch tools → `wandering-inn-reader/pipeline/`

Review only. No code was modified and no GPU/alignment job was run (a long align is in flight).
Scope: assess the battle-tested scratch tooling in `D:\Code-PC\_inn_work\` for promotion into the
stable repo as one or more focused PRs, per the Codex review's "book-pack workflow" expansion.

Reference conventions (from `D:\Code-PC\wandering-inn-reader\AGENTS.md`, `pipeline\schema.py`,
`tools\check_ip_limits.py`, `tools\check_safe_patterns.py`):
- `argparse` CLI, no hard-coded book paths, fail-loud (`sys.exit` on bad input).
- producers/consumers validate through `from schema import validate_doc`.
- torch scripts must use `model.train(False)`, **never** the parenthesised eval-mode call
  (`check_safe_patterns.py` blocks it; a reviewed line may carry a `safe-pattern-ok` comment).
- ffmpeg/ffprobe should resolve via `m4b_common.py` (env `FFMPEG`/`FFPROBE` or PATH), not a
  hard-coded `C:\Program Files\Shotcut\ffmpeg.exe`.
- IP guard: never track bulk audio/prose; the new mp3 cutter and wayback fetcher must not cause
  any `book*.txt` / `*.chapters.json` / `*.wav` / `*.m4a` / `*.mp3` to become tracked.
- Already MERGED (PR #14): `m4b_common.py`, `m4b_cut.py`, `m4b_make_units.py`, `m4b_package.py`,
  `probe_m4b.py`. Open: #15 (align_chapters staleness), #16 (browser-UA fetch).

## Inventory

| Script | Purpose | Generalized? | Repo-convention issues | Duplicate? | Recommendation |
|---|---|---|---|---|---|
| `find_chapter_boundaries.py` | For a book whose mp3 tracks are uniform time-slices that **straddle** chapters (Book 17: 10 tracks / 14 chapters), ASR-anchor each track opening → seg, piecewise-interpolate each web chapter's start time, then ASR-refine a ± window → `boundaries.json`. Reduces a "hard" multi-mp3 book to the per-chapter m4b flow. Complements `probe_track_starts.py` (which only handles tracks already split *at* chapter boundaries). | **Yes** — full argparse (`--audio-glob/--text/--chapters/--out/--refine-window/--chunk/--anchor-sec`), no hard-coded paths. | Clean. Uses `model.train(False)` + `torch.inference_mode()`. Fail-loud on empty glob. Does **not** import schema (it emits a boundaries file, not a player doc) — correct. Minor: no `#!/usr/bin/env` GPU-interpreter note beyond docstring (fine). | No — fills a real gap (no repo tool finds straddling-track boundaries). | **Promote as-is.** Highest-value new capability. |
| `book17_cut.py` | Cut per-chapter audio from a continuous multi-mp3 timeline at discovered boundaries; builds an ffmpeg `ffconcat` list per chapter to stitch cross-track spans; emits `align-out/NN.wav` (16k) + `play-out/NN.mp3` (stream-copy). Mp3 analogue of the merged `m4b_cut.py`. | **Mostly** — argparse (`--audio-glob/--boundaries/--align-out/--play-out/--force`), staleness check, resumable. Only the **name** is book-specific; the body is generic. | ffmpeg hard-coded (`FFMPEG = ... or r"C:\Program Files\Shotcut\ffmpeg.exe"`) — should route through `m4b_common.py` (rename to a neutral `ffmpeg`/`media` helper). | No (complements, not duplicates, `m4b_cut.py` — that one cuts a single m4b by `-ss/-t`; this stitches multi-mp3 cross-track spans). | **Generalize → promote** as `mp3_cut.py` (or `tracks_cut.py`): rename off `book17_`, resolve ffmpeg via the shared helper. |
| `book17_make_units.py` | Build Book-17 unit defs from `find_chapter_boundaries` output, **merging** the 2-part Strategists interlude into one alignment unit; emits `*_units.json` + `*_track_map.json` + `*_cut_bounds.json`. Mp3 analogue of `m4b_make_units.py`. | **No** — hard-coded filenames (`book17_boundaries.json` etc.), the merge group `[[0,1]] + ...` is literally Book-17's interlude, no argparse at all. | No CLI; hard-coded paths; merge rule is data, not a parameter. | Conceptually parallels `m4b_make_units.py` but for the boundaries-file input. | **Generalize first.** Recast as `mp3_make_units.py --boundaries … --merge "0,1" --out-units … --out-trackmap …` (optional `--merge` groups for continuous-narration interludes). Lower priority than the cutter. |
| `book17_package.py` | Package a multi-mp3 book into player per-track files where **one alignment unit may hold >1 web-chapter marker** (merged Strategists). Assigns each web chapter to its containing unit and places the marker at the *aligned* segment time; emits `alignNN.json` + `manifest.json`. Imports `validate_doc`. | **Mostly** — full argparse (`--units/--chapters/--per-chapter/--out/--book-title`). | `sys.path.insert(0, r"D:\Code-PC\wandering-inn-reader\pipeline")` then `from schema import validate_doc` — the absolute-path shim is wrong once it lives **inside** `pipeline/` (a plain `from schema import validate_doc` works there; the shim is how scratch reaches the repo). | Superset of `m4b_package.py`: that does 1 marker/track; this does the multi-marker-per-unit case. | **Generalize → promote** as `mp3_package.py`: drop the `sys.path` shim, plain `from schema import`. Pairs with the cutter + make_units. |
| `book17_split_strat.py` | Split a too-long merged unit (drifted under one auto-wps over 7.86h) at an internal boundary into Pt1/Pt2 sub-units for separate alignment; reuses the already-cut `01.wav`. Boundary = segment **5132** start of the merged align (or `--b-min`). | **Partly** — has `--b-min`, but everything else is hard-coded: `book17_per_track/align01.json`, `book17_audio/01.wav`, `book17.txt`, `lines[:9369]`, segment `5132`, fixed output names. | No general CLI; magic indices `5132`/`9369`; fixed paths. Uses no torch (safe). | No repo equivalent (a generic "split a long unit, realign, recombine" tool would be new). | **Generalize first** (medium value). Recast `book17_split_strat.py` + `book17_recombine_strat.py` together as one `split_unit.py` taking the unit's wav/text/align + a split seg or time → two sub-units; plus a recombine that offsets Pt2 by Pt1 duration. |
| `book17_recombine_strat.py` | Stitch the separately-aligned Pt1+Pt2 back into one `align01.json` (offsets Pt2 segment times by Pt1 wav duration, re-ids, two markers), overwrites the drifted file, patches `manifest.json`. Imports `validate_doc`. | **No** — no argparse; hard-coded `book17_per_track`, glob `strat_per_chapter/chap0{0,1}_*.json`, `strat_audio/01.wav`, fixed titles. | Hard-coded paths + the same `sys.path.insert` schema shim; fixed marker titles. | Pairs with split_strat; no repo equivalent. | **Generalize first**, fold into the same `split_unit.py`/`recombine_unit.py` pair as above. |
| `wayback_resolve.py` | Rewrite live chapter URLs → Wayback raw-HTML (`id_`) snapshot URLs near a timestamp, so early TWI volumes (live site 403/404 + post-2019 redesign drops prose) can be fetched from the archive. | **Yes** — argparse (`--in/--out/--timestamp`), no hard-coded book. Tiny/pure-stdlib. | Clean, fail-loud-ish. | No — fetch_text has no archive fallback today. | **Generalize → promote** as part of a wayback companion to `fetch_text.py` (see PR plan). |
| `wb_cache.py` | Politely fetch + **cache** each chapter's Wayback raw-HTML snapshot; resumable (skips cached), long backoff through Wayback's throttle. | **Yes** — argparse (`--in/--cache/--timestamp/--sleep`), stdlib only. | Clean, resumable, fail-loud on nothing. | No. | **Promote** with `wayback_resolve` + `wb_parse` as the wayback companion. |
| `wb_parse.py` | Build `OUT.txt` + `OUT.chapters.json` from cached Wayback HTML, **reusing `fetch_text.extract` / `split_sentences`** so segment indices match the rest of the pipeline; fails loud on any missing cache file. | **Yes** — argparse (`--in/--cache/--out`). | `sys.path.insert(0, r"D:\Code-PC\wandering-inn-reader\pipeline")` then `import fetch_text` — absolute shim, wrong inside the repo (use a package-relative import once co-located). | No — it's the archive-input twin of `fetch_text.py`. | **Promote** (de-shim) as the wayback companion. Best done by adding a `--wayback`/`--cache` mode to `fetch_text.py` so the extraction logic isn't forked. |
| `m4b_cut.py` (scratch) | Single-m4b cutter by `-ss/-t`, `--ext wav|m4a`. | n/a | Hard-codes Shotcut ffmpeg; **no** `m4b_common` resolution. | **Yes — superseded** by the merged `pipeline/m4b_cut.py` (PR #14), which routes ffmpeg via `m4b_common.py`. | **Leave as scratch** (older copy). |
| `m4b_make_units.py` (scratch) | Marks→units/trackmap. | n/a | Hard-codes `FFPROBE`; local `ffprobe_starts`; weaker range checks. | **Yes — superseded** by `pipeline/m4b_make_units.py` (uses `m4b_common.ffprobe_chapters`, adds non-positive-duration + range guards). | **Leave as scratch.** |
| `m4b_package.py` (scratch) | Per-chapter→player per-track (1 marker/track). | n/a | `sys.path` schema shim. | **Yes — superseded** by `pipeline/m4b_package.py`. | **Leave as scratch.** (Its multi-marker sibling is `book17_package.py`, handled above.) |
| `probe_track_starts.py` (scratch) | ASR each track opening, print. | n/a | **`model = bundle.get_model().to(dev).eval()`** — parenthesised eval call, would FAIL `check_safe_patterns.py`; hard-coded `AUDIO_DIR`. | **Yes — superseded** by `pipeline/probe_track_starts.py` (argparse `--audio-glob/--out`, safe-pattern-clean). | **Leave as scratch.** Do **not** promote this copy. |
| `split_m4b_to_m4a.py` | Book-13 m4b → 13 per-chapter `.m4a`, hard-coded `UNITS` table + chpl atom parse. | No | Hard-coded M4B/FFMPEG/OUTDIR + literal units. | Superseded by `m4b_cut.py --ext m4a`. | **Leave as scratch** (one-off). |
| `split_6_49.py` | One-off: sub-anchor Book-13 chapter 6.49 at internal mark, realign halves. | No | Hard-coded paths, magic segs `4800/3186/6568`. | Special case of the generic split-unit tool. | **Leave as scratch** (informs `split_unit.py` design). |
| `probe_orphans.py`, `probe_chpl.py`, `probe_m4b_v2.py`, `probe_m4b_chapters.py`, `decode_m4b_units.py`, `verify_pt2.py`, `book13_package.py`, `m4b_atoms.py`, `gpu_probe.py` | Ad-hoc probes / per-book one-offs / binary-atom inspectors / a GPU smoke test. `probe_orphans.py` is safe-pattern-clean (`model.train(False)`) but hard-codes two file paths. | No | Hard-coded paths; several are throwaway. | Various overlap with promoted probes. | **Leave as scratch.** Not promotion-worthy (per task scope). |
| `align_book.py`, `run_book_chunked.py`, `align_chapters.py`, `recombine_chapters.py`, `split_tracks.py`, `verify_tracks.py` (scratch copies) | Core aligner / chunked driver / per-chapter align / recombine / split / verify. | n/a | — | **Yes — already in `pipeline/`** (these are working copies of merged scripts; #15 touches `align_chapters`). | **Leave as scratch**; treat `pipeline/` as source of truth. |

## IP-guard & safe-pattern risk flags

- **Safe-pattern hard fail:** scratch `probe_track_starts.py` uses the parenthesised eval-mode call —
  it would be rejected by `check_safe_patterns.py`. Promote the repo copy, never this one. All other
  promotion candidates that load torch (`find_chapter_boundaries.py`, `probe_orphans.py`) already use
  `model.train(False)` + `torch.inference_mode()` — clean.
- **schema path shims:** `book17_package.py`, `book17_recombine_strat.py`, `wb_parse.py` all do
  `sys.path.insert(0, r"D:\Code-PC\wandering-inn-reader\pipeline")`. That absolute hack must be removed
  on promotion (plain `from schema import …` / `import fetch_text` once co-located in `pipeline/`).
- **ffmpeg/ffprobe hard-coding:** `book17_cut.py` (and the scratch m4b/split copies) hard-code the
  Shotcut path. On promotion, route through `m4b_common.py` so it honors `FFMPEG`/`FFPROBE`/PATH.
- **IP guard:** none of the promotion candidates ship audio/prose — they generate git-ignored
  `*.txt`/`*.wav`/`*.m4a`/`*.mp3`/`boundaries.json`/`_units.json` working files. No new tracked-IP risk,
  provided the wayback cache dir and any sample outputs stay git-ignored (add `*_wb_cache/` /
  `_wb_cache/` to `.gitignore` if not already covered). `wb_parse` output is a `*.chapters.json` /
  `book*.txt`, both already FORBIDDEN-by-pattern in the IP guard — confirm they land outside the repo
  tree or under existing ignores.

## Grouped PR plan

Respecting the repo workflow (focused PRs, merge commits not squash, Codex review gate; docs-only can
skip Codex). Ordered by value and independence.

### PR 1 — `pipeline: mp3 straddling-track boundary finder` (highest value, lands first)
- **Files:** add `pipeline/find_chapter_boundaries.py` (as-is).
- **Cleanup:** none required — argparse-complete, safe-pattern-clean, no schema dependency. Optionally
  add a one-line mention to the AGENTS.md "mp3 multitrack" flow that this handles the *straddling*-track
  case (tracks not pre-split at chapter boundaries). Keep that doc tweak in this PR.
- **Why first:** it's the new capability that unlocks the whole mp3 book-pack flow, and it's the
  cleanest candidate (no generalization needed). Codex-reviewable in isolation.

### PR 2 — `pipeline: mp3 book-pack cut/units/package` (the Book-17 family, generalized)
- **Files:** `book17_cut.py` → `pipeline/mp3_cut.py`; `book17_make_units.py` → `pipeline/mp3_make_units.py`;
  `book17_package.py` → `pipeline/mp3_package.py`.
- **Cleanup:**
  - rename all three off `book17_`;
  - `mp3_cut.py`: resolve ffmpeg via `m4b_common.py` (or a shared media helper) instead of the
    hard-coded Shotcut path;
  - `mp3_make_units.py`: add argparse (`--boundaries --out-units --out-trackmap [--merge "i,j"]…`),
    replace the literal Strategists merge with optional `--merge` groups, fail-loud;
  - `mp3_package.py`: drop the `sys.path.insert` shim, use plain `from schema import validate_doc`.
- **Depends on:** consumes PR 1's `boundaries.json`. Land after PR 1. This is the mp3 mirror of the
  already-merged m4b trio, so the review surface is familiar to Codex.

### PR 3 — `pipeline: split-and-recombine a too-long alignment unit`
- **Files:** fold `book17_split_strat.py` + `book17_recombine_strat.py` (and the lessons from
  `split_6_49.py`) into `pipeline/split_unit.py` + `pipeline/recombine_unit.py` (or one file with two
  subcommands).
- **Cleanup:** full argparse (unit wav/text/align + split seg-or-time + output names); remove magic
  `5132`/`9369`/`4800` and all hard-coded paths; drop the schema shim; parameterize marker titles.
- **Why separate:** it's an *escape-hatch* tool (used when a long auto-wps unit drifts), not part of the
  happy path — smaller, independently reviewable, and benefits from a real second test case before
  generalizing. Lowest priority of the three code PRs.

### PR 4 — `pipeline: wayback fallback for fetch_text` (can land independent of 1–3)
- **Files:** the cleanest shape is a `--wayback` / `--cache` mode **on `fetch_text.py`** plus a small
  resolver, rather than three new top-level scripts — so the extraction logic isn't forked. Concretely:
  fold `wayback_resolve.py` (URL→`id_` snapshot) and `wb_cache.py` (resumable polite cache) in as a
  `--wayback --timestamp --cache` path, and replace `wb_parse.py` by having `fetch_text` read from the
  cache dir when `--cache` is set (it already owns `extract`/`split_sentences`). If a single-file
  refactor is too big for one PR, promote `wayback_resolve.py` + `wb_cache.py` as-is and de-shim
  `wb_parse.py` to a package-relative `import fetch_text`.
- **Cleanup:** de-shim `wb_parse` import; ensure the cache dir + outputs stay git-ignored (the IP guard
  already FORBIDs `book*.txt`/`*.chapters.json` — keep them out of the tree); note the ≤2019 timestamp
  caveat in the docstring (already there).
- **Why independent:** touches the *text* path, not the audio path; complements open PR #16's UA fix
  (live-site 403 handling) — same problem space (early volumes), so coordinate but keep separate.

### Leave as scratch (no PR)
`m4b_cut.py`, `m4b_make_units.py`, `m4b_package.py`, `probe_track_starts.py` (all superseded by PR #14 /
repo copies), `split_m4b_to_m4a.py`, `split_6_49.py`, `probe_orphans.py`, `probe_chpl.py`,
`probe_m4b_v2.py`, `probe_m4b_chapters.py`, `decode_m4b_units.py`, `verify_pt2.py`, `book13_package.py`,
`m4b_atoms.py`, `gpu_probe.py`, and the scratch copies of `align_book/run_book_chunked/align_chapters/
recombine_chapters/split_tracks/verify_tracks` (already in `pipeline/`).
