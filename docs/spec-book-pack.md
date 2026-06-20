# Spec: the book-pack workflow (`process_book.py` + a per-book manifest)

Status: DRAFT / proposal. Owner: anotherpanacea-eng. Target repo:
`D:/Code-PC/wandering-inn-reader/` (pipeline lives in `pipeline/`); per-book scratch
stays in `D:/Code-PC/_inn_work/` (out of git — see § IP guard).

## 1. Problem & goal

Turning one TWI audiobook into a phone read-along is, today, a hand-run chain of 6–10
scripts. The chain forks three ways depending on the audiobook's *format tier*, the text
comes from one of two sources, and three of the steps (`probe_track_starts.py`,
`probe_m4b.py`, `find_chapter_boundaries.py`) emit a transcript a human must read and turn
into a hand-built map JSON. The stable half of the chain is committed in
`pipeline/`; the hardest tier (straddling tracks) only exists as Book-17-specific scratch
(`_inn_work/book17_*.py`). Codex's review asked for a **book-pack workflow**: a manifest
generator, m4b chapter import, EPUB/plain-text import, and **one command that produces
per-track sync files**.

**Goal.** A single driver — `pipeline/process_book.py` — reads one per-book manifest
(`book.json`) and runs the whole chain end-to-end:

```
range → text → format-detect → map → cut → align → recombine/package → verify → ship
```

…stopping only at the **two genuine human gates** (confirm the ASR-derived map; confirm
boundary timings). Every existing script stays runnable standalone; the driver orchestrates
them, it doesn't replace them. The manifest makes a run reproducible and resumable.

Non-goal (per `AGENTS.md`): do **not** bolt fleet vendor/lock/drift machinery onto this
repo. The driver is a thin, restraint-first orchestrator over scripts that already work.

## 2. Background: the three format tiers (what the driver must route between)

The audiobook→web-chapter mapping is **always read from the official TOC**
(`wanderinginn.com/table-of-contents/`, via `list_chapters.py`), never ASR-guessed. ASR is
used only for *audio timestamp boundaries*. The format tiers differ in how audio time maps
to chapters:

| Tier | Audio shape | Map step (the human gate) | Cut | Assemble | Books |
|---|---|---|---|---|---|
| **1 — m4b marks** | one `.m4b`, embedded chapter marks (marks map ~1:1 or one web chapter split across 2–3 marks) | `probe_m4b.py` ASRs each mark → you read off the RAW mark index each web chapter STARTS at; `m4b_make_units.py --starts … --end …` | `m4b_cut.py --ext wav` then `--ext m4a` | `m4b_package.py` (1 unit = 1 track; no recombine/split) | 4, 7, 13, 14, 15 |
| **2 — mp3 multitrack, boundaries on chapters** | many `NN - *.mp3`, tracks ≥ chapters, every track boundary falls on a chapter boundary | `probe_track_starts.py` ASRs each track opening → hand-build `*_track_map.json` (chapter→tracks[]+seg) | (none — align tracks in place) | `align_chapters.py --auto-wps` → `recombine_chapters.py` → `split_tracks.py` | 8, 10, 11, 12 |
| **3 — straddling (hard)** | few-track / single-file mp3, tracks **<<** chapters, tracks straddle chapter boundaries | `find_chapter_boundaries.py` (ASR-anchor each track→seg, then refine each chapter's start in the continuous timeline) → `*_boundaries.json` | `book17_cut.py` (ffmpeg `concat` across track edges) | align per cut chapter → `book17_package.py` | 3, 6, 9, 16, 17 |

Key structural facts the driver relies on:

- **Alignment drift.** A single greedy pass over a whole edited audiobook accumulates
  edit-divergence drift into a large end-of-book lag (`align_chapters.py` docstring). The
  fix is to align **each chapter independently** against its own contiguous audio so lag
  resets at every chapter boundary — which is why **all three tiers reduce to "cut into
  per-chapter audio units, then per-chapter anchored align."** Tier 1 and Tier 3 cut
  physical files; Tier 2 aligns in place because its tracks already are the units.
- **Pace varies per chapter**, so alignment uses `align_chapters.py --auto-wps`
  (per-chapter `wps = alignable_words / audio_seconds`); a fixed `--wps` drifts within a
  chapter narrated faster/slower than the guess.
- **Two cut artifacts from the same boundaries**: a 16 kHz mono **wav/PCM** for the aligner
  and ASR-verify (libsndfile can't read AAC), and a lossless/stream-copy **m4a/mp3** for
  phone playback. `m4b_cut.py --ext {wav,m4a}` and `book17_cut.py --align-out/--play-out`
  already produce both.
- **One contract.** Every producer and consumer validates through
  `pipeline/schema.py::validate_doc` (segments[], optional words[], optional chapters[]).
  The driver writes nothing that bypasses it.

## 3. The per-book manifest (`book.json`)

A single human-authored (mostly) file per book, living in that book's scratch dir
(`_inn_work/bookNN/book.json`) — **out of git**. It is the run's source of truth: it drives
routing, makes the run resumable, and records the human-gate decisions so a re-run is
non-interactive. Proposed schema (validated by a new `pipeline/book_manifest.py`):

```jsonc
{
  "book": 14,                                  // audiobook Book number
  "title": "Hell's Wardens",                   // player/display title
  "tier": "m4b",                               // "m4b" | "mp3_multi" | "straddle" | "auto"
  "audio": {                                   // exactly one of m4b / glob is set
    "m4b": "D:/.../Book 14.m4b",               // tier 1
    "glob": "D:/.../Reading/Book 8/*.mp3",     // tier 2 & 3 (numbered NN - *.mp3)
    "skip_first": 1, "skip_last": 0            // drop credits/outro tracks (cf. run_book_chunked)
  },
  "text": {
    "source": "live",                          // "live" | "wayback" | "epub" | "plain"
    "toc_from": "6.48 T", "toc_to": "6.59",    // list_chapters.py --from/--to (TOC range)
    "wayback_timestamp": "20180601",           // when source=wayback (<=2019)
    "import_path": "D:/.../book2.epub"          // when source=epub/plain
  },
  "map": {                                     // FILLED IN AT THE HUMAN GATE (see §5)
    "m4b_starts": [0,1,3,5, ...], "m4b_end": 41,        // tier 1 (m4b_make_units --starts/--end)
    "track_map": "book8_track_map.json",                // tier 2 (chapter→tracks[]+seg)
    "boundaries": "book17_boundaries.json",             // tier 3 (find_chapter_boundaries out)
    "merge_units": [[0,1]],                             // tier 3: web-chapter indices to merge
    "split_units": [{"unit": 0, "at_seg": 5132}],       // tier 3: long unit to split+recombine
    "confirmed": true                                    // gate passed → run is non-interactive
  },
  "ship": {
    "dropbox_dir": "D:/Dropbox/Apps/InnReader/book14"   // final per-track + manifest target
  },
  "thermal": { "cooldown_every": 1800, "cooldown": 30,   // align_chapters thermal knobs
               "smartctl": "C:/Program Files/smartmontools/bin/smartctl.exe",
               "smartctl_dev": "/dev/sda" }              // P5=C: after the 2026-06-15 drive swap
}
```

Rationale for fields:

- `tier: "auto"` → the driver detects (see §4) and rewrites the field in place so the
  decision is recorded.
- The `map` block is the only part a human edits mid-run; `confirmed: true` is the latch
  that makes the *next* `process_book.py` invocation run straight through (resumable).
- `thermal` mirrors the real `align_chapters.py` / `align_book.py` flags; defaults match
  the post-cooling-fix lighter cadence noted in `CLAUDE.md` (`--cooldown-every 1800
  --cooldown 30`). `smartctl_dev` default `/dev/sda` reflects the **post-swap** mapping
  (P5=C:=`/dev/sda`); re-verify with `smartctl --scan` after any hardware change.

## 4. Format-tier auto-detection

`process_book.py --detect` (also run implicitly when `tier == "auto"`):

1. If `audio.m4b` is set and `ffprobe_chapters()` (from `m4b_common.py`) returns ≥ 2
   marks → **tier 1 (m4b)**.
2. Else glob the mp3s. Count numbered tracks `T` (via the `track_no()` regex shared by the
   scripts) and web chapters `C` (from the TOC slice / `*.chapters.json`):
   - `T >= C` **and** a cheap opening-ASR spot check on a few tracks lands each sampled
     track's opening on a *chapter* opening → **tier 2 (mp3_multi)**.
   - `T < C` (notably `T` of single digits vs `C` in the teens, e.g. Book 17 = 10 tracks /
     14 chapters) → **tier 3 (straddle)**.
3. The `T >= C` vs `T < C` split is the load-bearing test; the ASR spot check only
   disambiguates the boundary case (a multitrack book whose tracks happen to equal the
   chapter count but are *time-sliced*, not chapter-cut). On ambiguity, **print the counts
   and stop** — never silently route; tier choice changes the whole downstream chain.

Detection writes the resolved `tier` back into `book.json` and exits (it does not auto-run
the GPU chain) so the human can eyeball it before the long job.

## 5. The two — and only two — human gates

Everything else is automated. The driver makes the gates loud and explicit:

**Gate A — confirm the audio→chapter map.** After the tier's probe step runs, the driver
prints the probe output and **halts** with: "edit `book.json` `map.*` and set
`map.confirmed: true`, then re-run." What the human fills in per tier:
- tier 1: `m4b_starts` (RAW mark index each web chapter starts at, from `probe_m4b.py`) +
  `m4b_end`.
- tier 2: the `*_track_map.json` (chapter→`tracks[]`+`seg`), hand-built from
  `probe_track_starts.py` openings joined with the TOC chapter list + `*.chapters.json`
  seg offsets.
- tier 3: accept/justify the `find_chapter_boundaries.py` `*_boundaries.json` (it already
  flags `LOW CONFIDENCE` rows and a non-monotonic-starts warning — those are exactly what
  the human checks), plus any `merge_units` / `split_units`.

**Gate B — confirm boundary timings (tier 3 only, optional elsewhere).** After cut + a
first align, the driver runs `verify_tracks.py` and, if it FLAGs points or `split_units`
is requested, halts for the human to adjust a boundary or request a unit split before the
final package. (Tiers 1–2 usually pass straight through; `verify_tracks.py` is still the
ship gate, just not an interactive stop unless it fails.)

Both gates are **state in the manifest**, not interactive prompts — so a resumed run is
fully non-interactive and a CI/cron-style re-run behaves deterministically (belt-and-
suspenders: divisible + resumable, per `CLAUDE.md`).

## 6. End-to-end stages (what `process_book.py` actually calls)

Each stage is idempotent and skip-if-done (mirroring the resume logic already in
`align_chapters.py`, `m4b_cut.py`, `book17_cut.py`). `--from-stage` / `--only` let you
re-enter at any stage. The driver shells out to the existing scripts with `sys.executable`
(so children inherit the ROCm 3.12 interpreter — **launch the driver with `py -3.12`**;
the bare-`py` shebang trap lands children on Store-3.13 CPU torch — `CLAUDE.md`).

0. **resolve range/text** —
   - `live`: `list_chapters.py --from --to --out bookNN_chapters.txt` → `fetch_text.py
     --url-file … --out bookNN`. (UA header already fixed in `fetch_text.py`.)
   - `wayback` (Books 1–2, genuine 404): `wayback_resolve.py` → `wb_cache.py` (resumable
     cache; runs in background through Wayback throttling) → `wb_parse.py` (reuses
     `fetch_text.extract` / `split_sentences`, so seg indices match). Driver waits for the
     cache to be complete (fail-loud on any missing chapter, as `wb_parse.py` does).
   - `epub` / `plain` (**NEW importers**, see §7): produce the same `bookNN.txt` +
     `bookNN.chapters.json` pair. This is Codex's "EPUB/plain-text import" item.
   - Output of stage 0 is always the contract pair: sentence-per-line `.txt` +
     `.chapters.json` whose `seg` = running non-blank-line index.
1. **detect** tier (§4) if `auto`.
2. **probe/map** — run the tier's probe; **Gate A** if `map.confirmed != true`.
3. **make units** —
   - tier 1: `m4b_make_units.py --starts --end` → `*_units.json` + `*_track_map.json`.
   - tier 2: the confirmed `*_track_map.json` is the unit definition (no cut).
   - tier 3: a promoted `make_units` step (generalize `book17_make_units.py`) applies
     `merge_units` → `*_units.json` + `*_track_map.json` + `*_cut_bounds.json`.
4. **cut** (tiers 1 & 3 only) —
   - tier 1: `m4b_cut.py --ext wav` (aligner input).
   - tier 3: `book17_cut.py` → `align-out/NN.wav` + `play-out/NN.mp3` (ffmpeg `concat`
     across track edges).
5. **align** — `align_chapters.py --auto-wps --track-map … --text … --outdir per_chapter`,
   passing the `thermal` knobs through to per-chapter `align_book.py` subprocesses
   (resumable per chapter: a present + schema-valid `chapNN_*.json` = done).
6. **assemble** —
   - tier 1: `m4b_cut.py --ext m4a` (playback files) then `m4b_package.py`.
   - tier 2: `recombine_chapters.py` → `split_tracks.py`.
   - tier 3: `book17_package.py` (a unit may hold >1 web-chapter marker — multi-part
     interludes — placed at aligned segment times). If `split_units` is set, run the
     promoted split→align→recombine sub-flow (generalize `book17_split_strat.py` +
     `book17_recombine_strat.py`) before packaging.
   - All paths emit `alignNN.json` + `manifest.json`, every file through
     `schema.validate_doc`.
7. **verify** — `verify_tracks.py --dir … --audio-glob …` (ASR-vs-alignment word overlap;
   exits nonzero past `--max-fail-frac`). This is the **ship gate**. **Gate B** here for
   tier 3 / on FLAG.
8. **ship** — copy the per-track `alignNN.json` + `manifest.json` and the playback audio
   (`NN.m4a` / `NN.mp3`) to `ship.dropbox_dir`. **Driver-side IP assertion before copy**
   (§9): refuse to copy any per-track JSON whose prose-word count or any embedded audio
   would violate the repo limits *if it were ever tracked* — the Dropbox app folder is the
   ship target, but the assertion keeps the habit honest and catches a mis-pathed write
   into the repo tree.

A `process_book.py --plan` prints the resolved stage list (like `align_chapters.py
--dry-run`) without touching the GPU.

## 7. New importers (Codex's EPUB / plain-text item)

Two small scripts under `pipeline/`, each emitting the **exact** contract pair so the rest
of the chain is unchanged:

- `import_epub.py --epub X.epub --out bookNN [--chapter-css SELECTOR]` — unzip, walk spine
  documents in order, extract paragraphs, and reuse `fetch_text.split_sentences` /
  `clean` so sentence segmentation is identical to the web path. Each spine doc (or a
  configured heading split) becomes one chapter marker with `seg` = running non-blank
  index. Dependency: stdlib `zipfile` + the existing `beautifulsoup4` (lxml optional;
  3.13 Store python has the acquisition tier — `CLAUDE.md`).
- `import_plain.py --in X.txt --out bookNN [--chapter-marker REGEX]` — for a hand-pasted
  manuscript: split on a marker regex (default a line like `## Chapter`), else treat the
  whole file as one chapter. Same sentence splitter, same output pair.

Both must produce `seg` indices consistent with how `fetch_text.py` counts (non-blank
lines, blank line between paragraphs), because every downstream `seg` (track maps, units,
chapter markers) is defined against that counting. A shared
`pipeline/text_emit.py::write_text_pair(lines, markers, out)` should own that invariant so
all four sources (live/wayback/epub/plain) go through one writer.

## 8. Promotion plan: scratch → `pipeline/`

Promote the battle-tested Book-17 hard-tier tools, generalized to be book-agnostic
(parameterized by the manifest, no `book17`-hardcoded filenames):

| Scratch tool | Promote to | Changes for promotion |
|---|---|---|
| `find_chapter_boundaries.py` | `pipeline/find_chapter_boundaries.py` | already generic (takes `--audio-glob/--text/--chapters/--out`); move as-is, import `schema`/helpers from repo. |
| `book17_make_units.py` | `pipeline/make_units.py` | replace the hardcoded `groups = [[0,1]] + …` with manifest `merge_units`; read `*_boundaries.json` + `*.chapters.json` by flag. |
| `book17_cut.py` | `pipeline/concat_cut.py` | drop `book17` names; keep ffmpeg-`concat` cross-track logic + the stale-output (mtime) resume guard. |
| `book17_package.py` | `pipeline/package_tracks.py` | generalize the "unit may hold >1 web-chapter marker" logic; this also subsumes `m4b_package.py`'s 1-unit-1-marker case as a special case (consider merging the two packagers). |
| `book17_split_strat.py` / `book17_recombine_strat.py` | `pipeline/split_unit.py` / `pipeline/recombine_unit.py` | parameterize the Pt1/Pt2 boundary (`split_units[].at_seg`) and the audio file; keep the offset-by-Pt1-duration recombine. |
| `wayback_resolve.py`, `wb_cache.py`, `wb_parse.py` | `pipeline/wayback_*.py` | already import repo `fetch_text`; move so the live/wayback split lives in one place. |

Keep `align.py` / `align_torch.py` (aeneas / single-pass torch) as-is — they are the
fallback aligners; the driver uses the `align_book.py`→`align_chapters.py` anchored path.
**Every promoted script keeps its standalone CLI** (the driver just calls it); this is the
`AGENTS.md` "each script usable standalone" requirement and the restraint principle (don't
hide a working tool behind only the driver).

After promotion the three tiers share one packager and one cut/align/verify spine; the only
tier-specific code is detect + probe + make-units.

## 9. Staying inside the IP guard (`tools/check_ip_limits.py`)

Hard constraint from `AGENTS.md` and `check_ip_limits.py`: the repo never ships > 20 s of
any audio asset or > 500 words of prose in any tracked file, and the bulk audio/fetched
text (`Reading/`, `*.wav/*.m4a/*.m4b`, `bookNN.txt`, `*.chapters.json`) is never tracked.
The book-pack workflow respects this by construction:

- **All per-book artifacts live in scratch** (`_inn_work/bookNN/…` or `D:/`), never under
  the repo tree. `book.json`, `bookNN.txt`, `*.chapters.json`, units, boundaries, cut
  audio, `per_chapter/`, `per_track/` are all scratch. The promoted *code* goes in
  `pipeline/`; the *data* never does. `book.json` paths point at absolute scratch/Dropbox
  locations, so the driver has no reason to write into the repo.
- **Caches outside Dropbox.** Per `CLAUDE.md`, Dropbox sync-lock corrupts atomic cache
  flushes; `wb_cache.py` and any align checkpoints write to a LOCAL non-Dropbox dir, and
  durable checkpoints go to the COOL drive (C:/P5 post-swap).
- **Driver-side ship assertion (§6 step 8).** Before copying, the driver runs the same
  measurement logic `check_ip_limits.py` uses (reuse `prose_words` / `measure_seconds`) as
  a guard: it asserts the destination is the configured Dropbox app folder (not a repo
  path) and that no artifact would violate limits if tracked. Belt-and-suspenders with the
  pre-commit hook, which still guards the repo itself.
- **Demo unchanged.** The repo's only shipped sample stays the existing Book-1 demo
  (≤ 9 s / within both limits); the book-pack workflow never adds tracked samples.

## 10. Open questions & edge cases

Open questions (ranked):

1. **Tier-2 vs tier-3 auto-detection reliability.** The `T >= C` / `T < C` count test is
   cheap but the boundary case (tracks == chapters but time-sliced) needs the ASR spot
   check; how many tracks must be sampled, and at what overlap threshold, before we trust
   the route vs stop for the human? Mis-routing wastes a multi-hour GPU run. (Leaning:
   sample 3 spread tracks; any opening NOT landing on a chapter start → tier 3.)
2. **One packager or two?** `book17_package.py` (multi-marker units) is a strict superset
   of `m4b_package.py` (1 marker/unit). Merge into `package_tracks.py`, or keep two for
   clarity? Merging reduces the tier-specific surface to detect+probe+make-units (§8 goal),
   but the m4b path's `audio = NN.m4a` vs mp3 `audio = NN.mp3` hint differs.
3. **Where does `split_units` get decided?** Tier-3's "a long merged unit drifts under one
   auto-wps" (the Strategists 7.86 h case) was only discovered *after* a first align flagged
   in `verify_tracks.py`. Should `process_book.py` auto-propose a split when a unit exceeds
   a duration/segment threshold, or always leave it to Gate B? (Leaning: auto-*propose* at
   Gate B, never auto-apply.)

Edge cases the spec must handle:

- **Split chapters (tier 1).** One web chapter across 2–3 marks: handled — `m4b_make_units
  --starts` lists only the FIRST mark of each web chapter; the unit spans to the next
  chapter's first mark.
- **Cross-track cuts (tier 3).** A chapter span crossing mp3 boundaries: handled by
  `book17_cut.py`'s ffmpeg `concat` list (inpoint/outpoint per track portion).
- **Multi-part interludes (e.g. Strategists Pt1/Pt2).** Two web-chapter markers inside one
  audio unit: `book17_package.py` already places each marker at its aligned segment time
  inside the unit. If continuous narration drifts under a single auto-wps over a very long
  unit, fall back to `split_unit` → align each part → `recombine_unit` (offset Pt2 by Pt1
  audio duration).
- **Credits/outro tracks.** `audio.skip_first` / `skip_last` drop them (cf.
  `run_book_chunked.py --skip-first 1`).
- **Coverage gap = fetched text short a chapter.** `align_book.py` exits 3 on a >2 % audio
  gap and `align_chapters.py` deletes the gapped chapter's output so a re-run retries;
  surface this as a driver error pointing at "fetch the missing chapter," not a silent pass.
- **Non-monotonic / low-confidence boundaries (tier 3).** `find_chapter_boundaries.py`
  already flags both; these are exactly the rows Gate A asks the human to inspect.
- **Thermal/reboot mid-run.** Every long stage is resumable (per-chapter outputs, durable
  fsync'd checkpoints on the cool drive); a resumed `process_book.py` skips finished
  stages. This is the `CLAUDE.md` belt-and-suspenders rule for long GPU jobs.

## 11. Acceptance criteria

- `py -3.12 pipeline/process_book.py --manifest book14/book.json --plan` prints the full
  resolved stage list for a tier-1 book with no GPU use.
- A fresh tier-1 run halts exactly once (Gate A) until `map.confirmed`, then runs to a
  validated `per_track/` + `manifest.json` and passes `verify_tracks.py`.
- A tier-3 run reproduces the Book-17 result (13 units, merged Strategists, Pt2 marker
  placed inside unit 1) from a manifest, using only promoted `pipeline/` scripts.
- `import_epub.py` on a sample EPUB and `import_plain.py` on a pasted chapter each produce a
  `.txt`/`.chapters.json` pair whose `seg` indices match what `fetch_text.py` would assign.
- `./check.sh` stays green; no per-book data file is tracked; `check_ip_limits.py` passes.
