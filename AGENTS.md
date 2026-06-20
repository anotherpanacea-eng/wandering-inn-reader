# Agent workflow

This repo is single-author (`anotherpanacea-eng`) but multi-agent: Claude sessions
and Codex sessions both contribute. This document is the durable, tool-agnostic
record of how they should work here. `CLAUDE.md` is a thin pointer to this file.

**Not part of the SETEC / APODICTIC fleet.** This is a standalone personal tool —
a read-along audiobook player plus a forced-alignment pipeline. There is no
cross-repo dependency, no vendored contract, no weekly-sync bot. Do **not** bolt
the fleet's vendor/lock/drift-gate machinery onto it; the right move here is
restraint (the same instinct that says "don't build a shared library for two
consumers"). The operator's local `Cowork/repo-fleet/` hub documents the fleet,
not this repo, and is invisible to cloud containers.

## The flow

```
spec  →  review  →  write  →  review  →  fix  →  merge
            ▲                    ▲
         codex                 codex
         claude                claude
```

- **Codex 5.5 is the PR review step.** It catches P1/P2 issues Opus misses. Make
  the obviously-needed fixes, then let Codex review. Don't spawn heavy Opus review
  fleets (duplicates the gate) and **don't merge out from under Codex** unless told
  to land. Be explicit about which model produced a finding.
- **Docs-only PRs skip Codex** (README/AGENTS prose, no code/behavior change) —
  merge directly. Codex is a correctness gate; reserve it for code.
- **Merge commits, never squash** — preserves the spec→review→fix trail.
- **Private repo:** GitHub branch protection needs Pro for private repos, so this
  relies on CI-equivalent local checks + discipline, not enforced gates.
- Work from a written contract (chat brief for trivia, a GitHub Issue once it's
  non-trivial), not an unscoped "improve the player."

## Repo layout

- **`index.html`** — the player. One self-contained file: no build step, no
  framework, no dependencies, no network. Keep it that way. Everything runs
  locally on the device; nothing uploads. `manifest.webmanifest` + `icon.svg`
  make it installable full-screen via "Add to Home Screen" (no native build).
- **`pipeline/`** — one-time jobs run on the operator's Mac / GPU PC:
  - `list_chapters.py` — read the live Table of Contents → a chapter-URL list for any
    Volume or audiobook Book (slices the ordered TOC, so interludes come along).
    - **Key resource: the TOC at <https://wanderinginn.com/table-of-contents/> is the
      authoritative audiobook→web-chapter MAPPING** (columns: Web Serial | Audiobook
      chapter | Ebook). It tells you exactly which web chapters each audiobook Book
      covers (e.g. Book 12 = `6.33 E … 6.47 E`, Book 13 = `6.48 T … 6.59`). **Read it
      to get a book's range/mapping — do NOT ASR-guess it.** ASR is only ever needed
      for AUDIO timestamp boundaries (single-file / few-track mp3 with no chapter marks).
  - `fetch_text.py` — pull chapter prose from wanderinginn.com → sentence-per-line
    `.txt` + `.chapters.json` markers.
  - `align.py` — convert an **aeneas** sync map → player JSON (sentence-level; word
    spans if you pass `--words-json`).
  - `align_torch.py` — **alternative** aligner on torchaudio `MMS_FA`: word-level
    timings, no aeneas/espeak install. Same output schema.
  - **Three assembly flows for a whole audiobook Book, all ending in the same per-track
    player JSON + `manifest.json` (then `verify_tracks.py` gates the ship):**
    - **mp3 multitrack** (Book 12 — many `NN - *.mp3` tracks): `probe_track_starts.py`
      (ASR each track opening → which mp3 tracks a chapter spans → a `*_track_map.json`)
      → `align_chapters.py --auto-wps` (per-chapter *anchored* `MMS_FA` align so drift
      resets each chapter; drives `align_book.py`, thermal-chunked + resumable) →
      `recombine_chapters.py` → `split_tracks.py`.
    - **single `.m4b`** (Books 13–15 — one AAC file with embedded chapter marks):
      `probe_m4b.py` (ASR each chapter MARK → which web chapter it starts; an edited
      audiobook splits one long web chapter across 2–3 marks, so mark count ≠ chapter
      count) → `m4b_make_units.py` (the hand-verified mark indices → `*_units.json` +
      `*_track_map.json`) → `m4b_cut.py --ext wav` (per-chapter 16 kHz wavs — libsndfile
      can't read AAC) → `align_chapters.py --auto-wps` → `m4b_cut.py --ext m4a` (lossless
      stream-copy playback files) → `m4b_package.py` (each per-chapter JSON is already one
      track → `alignNN.json` + manifest; no recombine/split). `m4b_common.py` resolves
      ffmpeg/ffprobe (`FFMPEG`/`FFPROBE` env or PATH; Shotcut bundles them on Windows).
    - **straddling mp3 / single-file** (Book 17, and few-track books where tracks ≪
      chapters so a chapter boundary falls *mid-track* — the per-chapter-anchoring
      precondition fails): `find_chapter_boundaries.py` locates each web chapter's START
      time in the continuous (concatenated) audio — ANCHOR (ASR each track opening →
      fuzzy-locate it in the text → keep only **trusted** `(global_time, seg)` pairs that
      clear `--conf-min`, plus the `(0,0)`/`(end)` bookends → piecewise-linear interp;
      dropped/low-confidence openings fall back to the proportional estimate) then REFINE
      (ASR a window around each chapter's estimated start → best-overlap sub-chunk). The
      output carries a `reliable` flag; too few trusted anchors (`--min-anchors`) or
      non-monotonic starts mark it `reliable=false` and exit nonzero. Those boundaries
      then cut per-chapter audio across track edges → `align_chapters.py --auto-wps` →
      package. (A very long continuous unit drifts under one greedy pass; split it into
      ~200-min sub-units and recombine — see the Book-17 notes.)
    - `verify_tracks.py` — ASR-vs-alignment word-overlap GATE (exits nonzero past a fail
      fraction); `schema.py` is the shared player-JSON validator all flows write through.
- **`demo/`** — offline sample: `demo-align.json` (source of truth) + `demo-data.js`
  (the embedded bundle the player's "Try the demo" loads — the audio is a base64
  `data:` URI inside it, no standalone audio file). A short Book 1 excerpt (the opening
  line + ~9s of narration), deliberately within the IP limits below.
- **`tools/`** — `check_ip_limits.py`, the pre-commit IP guard (see § IP limits).

## The data contract (the load-bearing thing)

The player and **both** aligners agree on one JSON schema. Change it in one place,
change it in all three:

```jsonc
{
  "title":  "string",                 // doc title + lock-screen metadata
  "audio":  "string",                 // filename hint only; the player loads the file the user picks
  "chapters": [                        // optional
    { "title": "string", "start": 12.5, "seg": 3 }   // seg = index into segments[]
  ],
  "segments": [
    { "id": 0, "start": 0.0, "end": 4.0, "text": "string",
      "words": [ { "w": "string", "s": 0.0, "e": 0.27 } ]   // optional → per-word glow
    }
  ]
}
```

Chapter `seg` is the index of the chapter's first sentence among the **non-blank**
lines — which is exactly the segment index, because `fetch_text.py` and `align*.py`
both skip blanks in input order. `align.py` warns loudly and drops a chapter whose
`seg` is out of range (a sentence-count vs audio-fragment-count disagreement);
trust that warning over a clean-looking output.

## Gotchas (this repo)

- **Don't hand-edit `demo/demo-data.js`** (it's a ~119 KB bundle of the align JSON
  plus base64 audio). Edit `demo/demo-align.json`, then re-splice — read
  `demo-data.js`, replace its `align` key with the JSON file, keep `audioDataUri`,
  re-emit `window.DEMO = …;`. (A throwaway Python script does this; the audio never
  needs regenerating.)
- **The pre-commit hook blocks two unsafe patterns** — enforced by
  `tools/check_safe_patterns.py` (which also runs in `check.sh`), not just convention.
  In the player, build the bottom-sheet / word-span DOM with the `el()` / `clear()`
  helpers, never by assigning an HTML string to an element (`.innerHTML` and friends).
  In the torch scripts (`align_torch.py`, `probe_track_starts.py`, `verify_tracks.py`),
  put the model in inference mode with `model.train(False)`, not the parenthesised
  eval-mode call. A reviewed exception can carry a `safe-pattern-ok` comment on the line.
- **`.gitignore` keeps the audiobook out** — `Reading/`, `*.m4a`/`*.m4b`, and the
  locally-generated `sync*.json` / `align*.json` / `volume*.mp3` working files. The
  web text stays the author's; this is personal read-along, not redistribution.
- **License flags only matter if you ever sell it:** aeneas is AGPL-3.0, and the
  torchaudio `MMS_FA` model is CC-BY-NC. Both fine for personal use.

## IP limits — what we ship (a hard commitment)

To protect the author's text and the narrator's voice, the repo **never ships more
than a small sample of either**:

- **Voice:** ≤ 20 seconds of any single audio asset.
- **Text:** ≤ 500 words (~one page) of narrative prose in any single file.
- **Bulk:** the full audio (`Reading/`, `samples/`, `*.wav`/`*.m4b`) and the fetched
  text (`book12.txt`, `*.chapters.json`) are git-ignored and never tracked.

This is enforced, not just promised. `tools/check_ip_limits.py` scans every tracked
file — **including audio embedded as base64 `data:` URIs** inside .js/.json/.html, so
a long clip can't be smuggled past the file rule — and fails loud on any violation.
"Couldn't measure the duration" counts as a failure, not a pass. Enable the
pre-commit hook once per clone:

```
git config core.hooksPath .githooks
```

Run it anytime: `python3 tools/check_ip_limits.py`. Any alignment sample you make
must stay within the limits or be git-ignored (that's why `samples/` is ignored).
The demo is a short real excerpt (Book 1's opening line + ~9s of narration),
deliberately within both limits — it uses the sample allowance, it isn't exempt from
it. If you raise a limit, change it in one place — `MAX_VOICE_SECONDS` /
`MAX_PROSE_WORDS` — and say why in the PR.

## Verify before claiming green

There is no GitHub-Actions CI (the repo token has no `workflow` scope and we keep the
repo dependency-free), so **`./check.sh` is the gate** — it runs every check below in
one command, no network or device needed. Run it before opening a PR. What it covers:

- `python3 tools/check_ip_limits.py` — the IP-limits guard passes (also runs as the
  pre-commit hook); confirm it still *fails* on an over-limit fixture if you touch it.
- `python3 tools/check_safe_patterns.py` — no HTML-string DOM build, no parenthesised
  eval-mode call (also a pre-commit hook); confirm it still *fails* if you plant one.
- `python3 -m py_compile pipeline/*.py tools/*.py tests/*.py` — everything compiles.
- `python3 tests/test_align.py` — the `align.py` data-contract check against a
  synthetic aeneas sync + chapter markers (chapters map to the right segment starts;
  an out-of-range marker is dropped with a warning; a malformed doc is rejected).

Beyond `check.sh` (can't be automated here):

- The **demo is the live render check**: open `index.html`, tap **Try the demo**,
  confirm the highlight tracks, the chapter menu lists chapters, and the settings
  sheet (text size / keep-awake / sleep timer) works. A browser can't be driven
  from a cloud container, so a cloud session should say the render path is
  logic-checked, not that it confirmed the UI on a device.
- Forced alignment against real audio needs the operator's machine (aeneas or torch
  installed) — by definition untested in-session; say so.
</content>
