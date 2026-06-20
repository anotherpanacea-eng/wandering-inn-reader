# SPEC — Alignment QA Workbench (`align_qa.py`)

Status: DRAFT (design only; no code written, no GPU jobs run — a long alignment is in flight and
must not be disturbed). Author target: `wandering-inn-reader/pipeline/`.
Last updated: 2026-06-19.

## 1. Problem

Forced alignment (`align_book.py` MMS_FA sliding window, anchored per chapter by `align_chapters.py`)
maps the GIVEN web prose onto the audiobook audio. It **never checks the audio actually SAYS that
text.** On an EDITED audiobook (cuts, reorders, merged units) the alignment can stay monotonic and
schema-valid while being content-wrong. Two real failure modes from this project:

1. **Gross drift (wrong boundary).** The merged ~7.86h "Interlude — Strategists at Sea" unit aligned
   as ONE chapter under a single `--auto-wps`. Pt1 (first half) scored 0.5–0.9 overlap (fine); Pt2
   (second half) collapsed to **0.04–0.19** (garbage). It was a single auto-wps trying to hold two
   sub-units with different effective pace. Caught only because someone manually re-ran
   `verify_tracks.py` with extra `--points` on that one track. The fix (`book17_split_strat.py` +
   `book17_recombine_strat.py`) split the unit at boundary `B` — the Pt1/Pt2 split point — aligned each
   half separately with its own auto-wps, then offset+stitched Pt2 back onto one timeline.

2. **Within-chapter wander.** A heavily-edited chapter (audiobook cut ~1000 words) aligns fine at the
   chapter *ends* but wanders for a minute or two in the *middle*. `verify_tracks.py`'s sparse 5
   points/track sampling steps right over it and the gate passes.

The existing gate `verify_tracks.py` is good at "ship / don't ship" CI signal but is **too sparse to
localize** drift and offers **no correction path**. This spec extends it into a workbench:
**dense drift profiling → flagged-segment report → wps-sanity pre-screen → manual correction → re-emit.**

## 2. Existing pieces we reuse (do not reinvent)

From `verify_tracks.py` (the contract for "does audio say the aligned text"):
- `norm(w)` / `words_of(text)` — lowercase, strip to `[a-z']`, split. **Reuse verbatim** so QA scoring
  matches the gate exactly.
- `asr(path, t0)` — reads `--win` seconds (default 8.0s) from `t0`, mono-downmixes, resamples to the
  bundle SR, runs `WAV2VEC2_ASR_BASE_960H` greedy CTC, returns a lowercase word string. **One model
  load per process** (GPU-aware, §7).
- `aligned_window(segs, t0, t1)` — the aligned text covering `[t0,t1]` (segments whose
  `end>=t0 and start<=t1`; nearest segment if none).
- `overlap = |aligned∩heard| / |aligned|` — set-overlap, aligned-word denominator. **This is the drift
  scalar.** Keep its meaning identical; only the *sampling density* changes.

From the aligners (why drift happens, and the correction levers):
- `align_book.py` `--wps` / `--overprovide`: text provisioned per window `= window*wps*overprovide`.
  Over-provisioning DRIFTS (forced_align compresses excess text, commits too early). `--auto-wps` in
  `align_chapters.py` sets `wps = alignable_words / audio_seconds` **per chapter** — exact average pace.
- `align_chapters.n_alignable(sentences)` — counts the letters/apostrophe tokens `align_book` would
  align; the numerator of auto-wps. **Reuse for the wps-sanity check (§5).**
- Track map shape `{title, seg, tracks[]}` (see `strat_track_map.json`); boundaries are `seg` indices.
- `recombine_chapters.py` — per-chapter local timelines → one book via cumulative track offsets.
- `book17_split_strat.py` (split a merged unit at boundary `B`, re-emit per-part wav + track map) and
  `book17_recombine_strat.py` (offset Pt2 by Pt1 audio duration, restitch one `alignNN.json` + patch
  `manifest.json`). **These are the template for §6 manual correction** — generalize them, don't fork.

Schema (`schema.py`): output is `{title, audio, segments:[{id,start,end,text,words?}],
chapters?:[{title,start,seg}]}`; `validate_doc(doc, source=...)` is the producer+consumer gate. Any
re-emitted file MUST pass it (fail-loud), with **sequential `id`**, monotonic `start` (non-monotonic is
WARN not ERROR but we treat it as a flag), and `chapter.seg ∈ 0..len(segments)-1`.

## 3. Scope

In: dense ASR drift profiling, a visual drift profile, a flagged-segment report, a pre-ASR wps-sanity
screen, and a manual-correction → re-emit workflow. Out: changing the aligner's algorithm; auto-fixing
drift without an operator decision; any network/IP-exposing output.

`align_qa.py` has **subcommands** (one process, one model load when ASR is involved):

```
py -3.12 align_qa.py profile  ...   # dense ASR drift profile + flagged segments + drift viz
py -3.12 align_qa.py wps-check ...   # cheap, NO ASR/GPU: flag auto-wps outliers (boundary tell)
py -3.12 align_qa.py correct  ...    # apply an operator correction file -> re-emit per-track JSON
```

## 4. `profile` — dense drift profiling + flagged segments + visualization

**Goal:** ASR-sample a track at fine, regular intervals (not 5 points), score per-window overlap, and
render a DRIFT PROFILE over time so a bad half-chapter like Strategists Pt2 is obvious at a glance.

### Sampling
- `--dir`, `--audio-glob`, `--tracks`, `--win` (default 8.0s) — same semantics as `verify_tracks.py`.
- Replace `--points N` with **`--interval SEC`** (default 90s) → probe points at
  `t0 = 0, interval, 2*interval, …` clamped to `dur - win`. Optionally `--max-points` cap per track for
  GPU budget. A 471-min Strategists track at 90s = ~314 windows; at 8s each that is a real but bounded
  GPU cost (§7, open Q). Coarse default for sweeps; `--interval 20` for a forensic pass on one suspect
  track (this is the manual "extra points" move, now first-class).
- Reuse `asr()`, `aligned_window()`, `words_of()`, `overlap` unchanged. Emit one record per window:
  `{t0, t1, overlap, n_aligned, n_heard, align_text, heard_text}`.

### Smoothing / run detection
Single-window overlap is noisy (a quiet beat, a name the ASR mangles). To distinguish **drift** (a
sustained low run, e.g. Pt2's 0.04–0.19 across the whole half) from **noise** (one isolated dip):
- compute a rolling median over `--smooth K` windows (default 3);
- a point is FLAGGED if smoothed overlap `< --min-overlap` (default 0.4, matching the gate);
- contiguous flagged points merge into a **flagged RUN** `{t_start, t_end, min_overlap, mean_overlap,
  n_windows}`. A run spanning a large fraction of a track = the gross-drift / wrong-boundary signature.

### Drift profile visualization (no framework)
Two renderers, `--viz {ascii,html}` (default `ascii`):

**ASCII** (default; cp1252-safe, ASCII glyphs only per the console gotcha) — one row per probe, a bar
scaled to overlap, threshold marker, `<` flag on sub-threshold rows. Example shape:

```
track 01  Strategists at Sea  (471.9min)   interval 90s  thr 0.40
  t=  0.0min  0.82 ########################------  PASS
  t=  1.5min  0.77 ######################---------  PASS
  ...
  t=235.0min  0.71 #####################----------  PASS   <- Pt1 end, still good
  t=236.5min  0.14 ####--------------------------  FLAG <  <- Pt2 begins: cliff
  t=238.0min  0.09 ##----------------------------  FLAG <
  ... (sustained FLAG run to end) ...
=== track 01: 156/314 PASS; FLAGGED RUN 236.5-471.9min (mean 0.11, 158 win) ===
```

The **cliff** at the Pt1→Pt2 boundary is visible at a glance — exactly what was missed before.

**HTML/SVG** (`--viz html`, `--out report.html`) — a single static self-contained file, **no JS
framework, no CDN**: inline `<svg>` polyline of overlap vs time, a horizontal threshold line, flagged
runs shaded, hover `<title>` per point. Multiple tracks stack vertically. **No prose in the HTML beyond
the §4.5 snippet caps** (IP guard, §7).

### Flagged-segment report
For each flagged RUN, list the time range and, per window inside it, the **aligned text vs ASR heard
text side by side** (the `verify_tracks` `ALIGN:`/`AUDIO:` lines, retained) so the operator can tell
drift from noise:
- *drift* = `ALIGN` text is coherent prose unrelated to `AUDIO` (alignment is elsewhere in the book);
- *noise* = `ALIGN` and `AUDIO` are obviously the same content, just low set-overlap (names, numbers).

`--report report.txt` writes machine-greppable records:
```
FLAG track=01 t=236.5-238.0min overlap=0.14 n_aligned=22 n_heard=24
  ALIGN: <<= 150-char snippet of aligned text>>
  AUDIO: <<= 150-char snippet of ASR heard text>>
```
Snippets capped (default 150 chars, `--snippet`) to respect the prose limit. A machine `--json
profile.json` (per-window records + runs) lets `correct` and future tooling consume it without re-ASR.

### Gate compatibility
`profile` keeps `verify_tracks`'s exit contract: `--max-fail-frac` (per track AND book-wide) → exit
nonzero so it can still gate CI. Density makes the gate *stricter and localizing*, not just pass/fail.

## 5. `wps-check` — cheap pre-ASR boundary screen (NO GPU)

**Goal:** a fast, ASR-free first pass that flags the wrong-boundary signature *before* spending GPU on a
dense profile — the "squished-unit tell."

The mechanism (the Strategists diagnosis): a merged/mis-bounded unit's `--auto-wps` comes out **above
the book's normal narrator pace** because its true audio is longer than the boundary implies — the text
is squished into too little audio, so `words/sec` reads high. Conversely a unit given too *much* audio
reads low.

- Inputs: `--track-map`, `--text`, `--audio-glob` (same as `align_chapters.py`). For each map entry
  compute `wps_i = n_alignable(slice_i) / audio_seconds_i` using `align_chapters.n_alignable` and
  `soundfile.info().duration` — **identical to what `--auto-wps` already computes**, so no new math.
- Compute the book's **median** wps and MAD. Flag any chapter whose `wps_i` deviates beyond
  `--wps-tol` (default ±15%, or `--wps-mad N` MADs). Print a sorted table; nonzero exit on any flag with
  `--strict`.

```
=== wps-sanity (median 2.48 w/s over 24 chapters) ===
  [00] Strategists at Sea       wps 3.19  +29%   <-- FLAG (squished -> boundary too early / merged unit)
  [07] Interlude - Wistram Days  wps 2.51   +1%
  ...
```

This is **~free** (no model load) and points the operator at which track to run `profile --interval 20`
on, and which to consider splitting (§6). It is advisory, not a fix — narrator pace genuinely varies
(interludes, songs), so a flag means "inspect," confirmed by the dense profile.

## 6. `correct` — manual correction → re-emit per-track JSON

**Goal:** once the operator has localized drift, let them supply a corrected boundary (a split point
and/or a time offset) and re-emit the per-track JSON, conforming to schema, without touching the
aligner internals. This generalizes `book17_split_strat.py` + `book17_recombine_strat.py` into a
reusable, declarative step.

### Correction file format (`--correction corr.json`)
A small JSON the operator hand-writes (or `profile` can scaffold from a flagged run). One file lists
operations against ONE target track's `alignNN.json`:

```jsonc
{
  "target": "book17_per_track/align01.json",   // per-track file to re-emit (overwritten; .bak kept)
  "audio":  "book17_audio/01.wav",             // the continuous track's audio (for durations/offsets)
  "manifest": "book17_per_track/manifest.json",// optional: patch this track's manifest entry too
  "ops": [
    // -- SPLIT a drifted merged unit and RE-ALIGN each part (the Strategists fix) --
    {
      "op": "split_realign",
      "at_seg": 5132,                 // boundary segment: Pt2's first segment (the cliff from profile)
      // boundary time B defaults to segments[at_seg].start (Pt1 aligned well -> that time is accurate);
      // override with "at_min": 236.5 from the drift profile if Pt1's own tail is suspect.
      "parts": [
        {"title": "Interlude - Strategists at Sea (Pt. 1)"},
        {"title": "Interlude - Strategists at Sea (Pt. 2)"}
      ]
      // emits strat_audio/{01,02}.wav + strat.txt + a 2-entry track map, runs align_chapters
      // --auto-wps on them (SEPARATE wps per part), then offsets Pt2 by Pt1 audio duration and
      // stitches back -- exactly book17_split_strat + book17_recombine_strat, parameterized.
    },

    // -- SHIFT a within-chapter wander by a constant offset (cheap, no re-ASR) --
    {
      "op": "offset",
      "seg_range": [1840, 1990],      // segments to nudge (from the flagged run)
      "delta_sec": -2.4               // add to start/end (and words s/e) of each segment in range
    },

    // -- RESPLIT only (no realign): just move a chapter marker / boundary seg --
    {"op": "set_boundary", "chapter": 1, "seg": 5132}
  ]
}
```

### Behavior
- `split_realign` is the heavy op: it **shells out to `align_chapters.py --auto-wps`** on the sub-parts
  (so the boundary cliff disappears because each part gets its own pace), then performs the
  offset-and-stitch from `book17_recombine_strat.py` (Pt2 `start/end` and every word `s/e` shifted by
  Pt1 audio duration; ids renumbered sequentially; two chapter markers; manifest `sentences`/`chapters`
  patched). It is GPU work → obeys §7 (preflight, one model load, cooldowns). Because the sub-parts run
  through the normal aligner, it inherits checkpoint/resume — re-running resumes (belt-and-suspenders).
- `offset` and `set_boundary` are **pure JSON transforms — no GPU**: load, mutate the listed
  segments/words/markers, renumber ids, re-validate, write. Fast, reversible, ideal for a within-chapter
  wander where re-aligning the whole track is overkill.
- **Every re-emit:** demote the prior file to `.bak` (mirrors `align_book.save_ckpt`), run
  `validate_doc(doc, source=...)` BEFORE writing (fail loud on a malformed result — bad `seg` range,
  non-monotonic starts, NaN), and after writing, **auto-run `profile --interval 20` on just the
  corrected track** to confirm the flagged run cleared (close the loop; never declare fixed unverified).
- Optionally, after a `correct`, run `recombine_chapters.py` to refresh the whole-book JSON if one is
  maintained — but per-track files are the playback unit, so a single-track re-emit is usually enough.

### Auto-suggesting a boundary (open Q, §8)
`profile` can *propose* `at_seg` for a `split_realign`: the segment at the START of the longest flagged
run (the cliff). Whether to also auto-suggest a corrected `delta_sec` for an `offset` op — e.g. by
cross-correlating ASR-heard words against aligned text within the run to estimate the lag — is an open
question; v1 leaves `delta_sec` operator-supplied (read off the profile), proposing only the boundary.

## 7. GPU, thermal, and IP guards (hard constraints)

- **One model load at a time.** `profile` and `split_realign` load an ASR/aligner model. Run the
  `align_chapters.preflight()` check (refuse a 2nd `python*` process; abort if child torch is CPU-only)
  before any GPU op. **Never run `profile` while an alignment is in flight** (the current situation).
- **GPU interpreter:** `py -3.12` (ROCm). The shebang trap routes a bare `py script.py` to Store-3.13
  CPU torch — `profile` must print its torch build/`cuda.is_available()` and **fail loud if CPU-only**
  (a CPU ASR sweep of 300+ windows is unusably slow), mirroring `verify_tracks`'s `dev` print.
- **Thermal:** dense ASR is sustained GPU load on a box that thermal-reboots. Plumb the same knobs as
  `align_book`: `--cooldown-every`/`--cooldown`, and optional `--smartctl`/`--smartctl-dev`
  drive-temp watchdog (note: smartctl device IDs are now `/dev/sda`=P5=C:, `/dev/sdb`=SN850X=D: after
  the 2026-06-15 slot swap — make the device a flag, don't hardcode). Write any checkpoint/`--json`
  output to **D: (cool drive), never C:**, and never inside Dropbox (sync-lock corrupts atomic flushes).
- **IP guard:** the workbench prints/stores aligned prose snippets. Cap every snippet (`--snippet`,
  default 150 chars) and keep HTML/JSON reports OUT of git (they are QA scratch under `_inn_work\`, not
  shipped). The shippable per-track JSON from `correct` still passes `check_ip_limits.py` because it is
  the same player schema the pipeline already ships. No bulk text/audio is ever written to a tracked path.
- **Schema:** any file `correct` writes goes through `validate_doc(..., strict=True)` first; sequential
  ids, in-range `chapter.seg`, finite numbers.

## 8. Open questions

1. **Density vs GPU cost.** What `--interval` reliably catches a ~1–2 min within-chapter wander without
   an unaffordable sweep? 90s is coarse enough to *miss* a 60s wander; 20s quadruples GPU time on
   multi-hour tracks. Options: adaptive density (coarse pass, then auto-refine `--interval` only inside
   or adjacent to flagged runs), or a cheap non-ASR pre-screen (e.g. per-segment `wps`/duration
   anomalies) to target dense ASR. Needs a measured GPU-seconds-per-window number on the 7900 XT.
2. **Auto-suggesting the corrected boundary/offset.** `at_seg` from the longest flagged run's cliff is
   straightforward. Can we reliably auto-estimate a `delta_sec` (ASR↔aligned lag via local word
   cross-correlation) so `offset` ops are proposed not hand-measured — and how do we keep that from
   "fixing" mere ASR noise? v1 proposes the boundary only.
3. **Drift vs noise threshold.** Is fixed `min-overlap 0.4` + rolling-median-3 enough to separate a true
   drift run from genuinely hard audio (songs, many proper nouns, accented narration)? May need a
   per-register floor or a "heard text is unrelated prose" check (token-set Jaccard against the WHOLE
   chapter, not just the window) to confirm a run is real drift, not a locally hard passage.
