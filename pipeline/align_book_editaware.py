#!/usr/bin/env python3
"""
align_book_editaware.py — EDIT-AWARE / gap-skipping forced aligner.

A SIBLING of align_book.py for the audiobooks the narration team EDITED: when the AUDIO cuts a run of
prose the web TEXT still contains, align_book.py's strictly-monotonic cursor cannot skip the cut text
with it, so forced alignment maps the still-present (un-narrated) sentences onto whatever audio is
actually playing and drifts one cut behind for the rest of the stretch (Book 07's 5.13 has a ~60-min
0.00-overlap middle; Book 12 6.42E, Book 17 Strategists Pt 2 are the other confirmed cases).

This aligner runs the SAME streaming sliding-window forced-alignment loop as align_book.py — it IMPORTS
align_book's reusable pieces (AudioStream, emission_for, fit_and_align, assemble_segments, save/load
checkpoint, the thermal guard) rather than forking them — and adds ONE thing: a periodic ASR RESYNC
CHECK (the SAME WAV2VEC2_ASR_BASE_960H model + the SAME word-overlap metric verify_tracks.py gates on)
that can advance the text cursor NON-contiguously past a detected cut. The skipped text is emitted as
schema-valid ZERO-DURATION "gap" segments (word_time[ti] = 'CUT'), so the existing player renders the
skipped prose but never highlights it — no player change required.

On an UN-edited chapter the forward search effectively never fires (here-overlap stays high), so the
pass is behaviourally identical to align_book.py there, costing only the periodic ~8s ASR (~9% more
GPU-seconds worst case, inside the existing cooldown/thermal budget).

GPU/thermal rules are align_book.py's, unchanged: one model load each, model.train(False) on both,
the resync ASR counts toward the existing --cooldown-every budget, checkpoints are fsync-durable, and
the preflight refuses a 2nd concurrent GPU job. DO NOT run this while another alignment is in flight.

See spec-editaware-aligner.md for the full design. The pure cut-detection / cursor-skip / gap-emit
logic (forward_match, apply_skip, rollback_skip) is stdlib-only and unit-tested in tests/test_editaware.py
with stubbed ASR — no torch/GPU needed for that test.

Run with the GPU interpreter:
    py -3.12 align_book_editaware.py --audio ... --text ... --out ... --edit-aware-defaults
"""
import argparse, json, os, sys, time

# Import the reusable, stable pieces from align_book — single source of truth, do NOT fork them.
from align_book import (
    normalize_word, read_sentences, build_tokens, AudioStream, emission_for,
    fit_and_align, assemble_segments, save_ckpt, load_ckpt, read_drive_temp, thermal_guard,
)
from asr_overlap import words_of, overlap_score   # the EXACT verify_tracks metric (shared helper)
from schema import validate_doc


# --------------------------------------------------------------------------------------------------
# PURE cut-detection / cursor logic (stdlib only — torch-free, fully unit-testable on synthetic data).
# The forced-alignment and ASR calls are the thin device shell around this; these functions decide the
# skip/emit, so the decision is testable without a GPU. See tests/test_editaware.py.
# --------------------------------------------------------------------------------------------------

def forward_match(heard_words, tokens, align_idx, pos_in_align, forward_words, min_skip_words):
    """Slide the HEARD transcript across a FORWARD window of the REMAINING web text and find where the
    audio actually is. Returns (best_off, best_score):
      * best_off   — the cursor-RELATIVE alignable offset (into align_idx) of the best-overlapping text
                     span, i.e. advancing pos_in_align by best_off lands on the matched prose.
      * best_score — that span's verify_tracks set-overlap against the heard words.

    Scoring window width ~ len(heard_words) * 1.3 alignable words (a hair wider than the heard span, to
    tolerate ASR insertions). Only offsets >= min_skip_words are considered candidate CUTS — a match
    nearer than that is normal step jitter, not a cut, and is ignored (returns best_off 0). The forward
    reach is bounded by forward_words to cap both cost and the false-match surface.

    Pure: `tokens`/`align_idx` are the same structures align_book builds; no audio, no model. heard_words
    is a normalised word list (from words_of)."""
    if not heard_words:
        return 0, 0.0
    span_w = max(1, int(len(heard_words) * 1.3))
    hi = min(len(align_idx), pos_in_align + forward_words)
    # Precompute the normalised web word at each alignable position in [pos_in_align, hi).
    web = [tokens[align_idx[p]]["nw"] for p in range(pos_in_align, hi)]
    best_off, best_score = 0, 0.0
    # Candidate offsets start at min_skip_words (a nearer match is jitter, not a cut). Require the scoring
    # span to be reasonably FULL-WIDTH (>= ~half span_w): a degenerate short tail at the end of the
    # forward window is a sub-span of the heard words and would score spuriously high (the asymmetric
    # overlap divides by the SHORT span), inventing a phantom jump just before the text runs out. Only
    # consider offsets that still have enough remaining text to fill the span.
    min_span = max(1, span_w // 2)
    last_off = len(web) - min_span
    for off in range(min_skip_words, last_off + 1):
        span = web[off: off + span_w]
        score = overlap_score(span, heard_words)
        if score > best_score:
            best_score, best_off = score, off
    return best_off, best_score


def resync_decision(heard_words, here_words, tokens, align_idx, pos_in_align, *,
                    resync_min, match_min, forward_words, min_skip_words, force):
    """The PURE resync decision from a HEARD transcript (what the ASR returned) and the HERE transcript
    (the web words currently aligned to that audio window). Returns one of:
      ("ok",       score_here)
      ("cut",      (best_off, best_score, score_here))   # advance the cursor by best_off
      ("deadzone", (score_here, best_score))             # unmatched; do NOT skip
    so the cut-detection logic is unit-testable with stubbed transcripts and no GPU/audio (spec §10).
    The GPU wrapper (resync_check, inside main) only does the ASR + window slicing, then calls this.

    `force=True` (post-skip confirm / starved step) runs the forward search even when here-overlap is
    high — but a high here-overlap with no confident forward jump is 'ok', NOT a dead-zone (so a clean
    confirm doesn't false-trip the dead-zone counter)."""
    score_here = overlap_score(here_words, heard_words)
    on_track = score_here >= resync_min
    if on_track and not force:
        return "ok", score_here
    best_off, best_score = forward_match(heard_words, tokens, align_idx, pos_in_align,
                                         forward_words, min_skip_words)
    if best_score >= match_min and best_off >= min_skip_words:
        return "cut", (best_off, best_score, score_here)
    if on_track:                                          # forced check, but clearly fine here
        return "ok", score_here
    return "deadzone", (score_here, best_score)


def apply_skip(word_time, cut_spans, align_idx, pos_in_align, best_off, audio_min):
    """ACCEPT a cut at cursor-relative offset best_off: mark the skipped alignable tokens 'CUT', record
    a cut_spans entry, and return the NEW pos_in_align (advanced by best_off). Mutates word_time and
    cut_spans in place. The audio buffer is NOT touched (the skip consumed no audio — the buffer head is
    the POST-cut narration we are about to align). Pure stdlib."""
    a0, a1 = pos_in_align, pos_in_align + best_off
    for p in range(a0, a1):
        word_time[align_idx[p]] = "CUT"
    cut_spans.append({"a0": a0, "a1": a1, "kind": "cut", "audio_min": round(audio_min, 2),
                      "n_words": best_off})
    return a1


def rollback_skip(word_time, cut_spans, align_idx, entry):
    """Undo the most recent apply_skip (the post-skip confirm-ASR said the skip was WRONG): clear the
    'CUT' marks back to None ("not reached") and drop the cut_spans entry. Returns the restored
    pos_in_align (entry['a0']). `entry` is the dict apply_skip appended (and must be its last). Pure."""
    a0, a1 = entry["a0"], entry["a1"]
    for p in range(a0, a1):
        word_time[align_idx[p]] = None
    if cut_spans and cut_spans[-1] is entry:
        cut_spans.pop()
    return a0


def starved_step(committed_words, committed_sec, wps, commit_floor_frac):
    """A cut makes forced alignment cram the wrong text and commit oddly few words for the audio it ate.
    True when the step committed fewer than commit_floor_frac * wps * committed_sec words — the cheap
    early-warning that triggers an off-cadence resync check. Pure."""
    if committed_sec <= 0:
        return False
    expected = commit_floor_frac * wps * committed_sec
    return committed_words < expected


def heard_window_words(segments_or_word_stream):
    """(helper kept for symmetry / testing) normalised word list of a piece of aligned text."""
    if isinstance(segments_or_word_stream, str):
        return words_of(segments_or_word_stream)
    return list(segments_or_word_stream)


# --------------------------------------------------------------------------------------------------
# The ASR resync model wrapper — the ONLY GPU part of the resync. Isolated so the test stubs it.
# --------------------------------------------------------------------------------------------------

class ResyncASR:
    """Wraps WAV2VEC2_ASR_BASE_960H (the SAME bundle verify_tracks.py loads) for greedy CTC transcription
    of a short audio window. model.train(False) for inference mode (the repo's safe-pattern guard). The
    test never instantiates this — it injects a stub object with a .transcribe(samples) method."""
    def __init__(self, device, torch, torchaudio):
        self.torch = torch
        bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
        self.model = bundle.get_model().to(device)
        self.model.train(False)                       # inference mode (NOT the parenthesised eval call)
        self.labels = bundle.get_labels()
        self.sr = bundle.sample_rate
        self.device = device

    def transcribe(self, samples):
        """Greedy-CTC transcript (lower-case, spaces from '|') of a 1-D np.float32 mono-16k window —
        the exact decode verify_tracks.asr() uses. Returns a string."""
        torch = self.torch
        wav = torch.from_numpy(samples).unsqueeze(0).to(self.device)
        with torch.inference_mode():
            emi, _ = self.model(wav)
        ids = emi.argmax(-1)[0].tolist(); out, prev = [], None
        for i in ids:
            if i != prev and i != 0:
                out.append(self.labels[i])
            prev = i
        return "".join(out).replace("|", " ").strip().lower()


class CommittedTail:
    """A small rolling ring of the most-recently-COMMITTED audio samples, so the resync check can ASR
    'the last asr-win seconds' AFTER stream.commit() has already dropped them from AudioStream's buffer
    (AudioStream never seeks backward by design — it only keeps un-committed audio). We retain just
    keep_sec of tail audio (~asr-win, default 8s -> 128k float32 ~ 512 KB) and the global clock at its
    head, so we can slice [t0, t1] back out for the ASR. Torch-free."""
    def __init__(self, np, sr, keep_sec):
        self.np = np
        self.sr = sr
        self.keep = int(keep_sec * sr)
        self.buf = np.zeros(0, dtype=np.float32)
        self.end_t = 0.0                              # global seconds at buf[-1] (the committed head)

    def push(self, samples, end_t):
        """Append `samples` (the audio just committed, ending at global time end_t) and trim to keep_sec."""
        self.buf = self.np.concatenate([self.buf, samples])
        if len(self.buf) > self.keep:
            self.buf = self.buf[-self.keep:]
        self.end_t = end_t

    def slice(self, t0, t1):
        """Samples for global window [t0, t1], or None if it isn't fully retained in the ring."""
        start_t = self.end_t - len(self.buf) / self.sr
        if t0 < start_t - 1e-6 or t1 > self.end_t + 1e-6:
            return None
        i0 = int(round((t0 - start_t) * self.sr))
        i1 = int(round((t1 - start_t) * self.sr))
        i0 = max(0, i0); i1 = min(len(self.buf), i1)
        if i1 - i0 < 1:
            return None
        return self.buf[i0:i1].copy()


def aligned_text_for_window(tokens, word_time, t0, t1):
    """The web words whose committed timestamps fall in [t0, t1] — the 'aligned here' side of the resync
    overlap (mirrors verify_tracks.aligned_window but over the live word_time, not finished segments).
    Returns a normalised word list. Pure given word_time."""
    out = []
    for i, wt in enumerate(word_time):
        if isinstance(wt, tuple):
            s, e = wt
            if e >= t0 and s <= t1:
                nw = tokens[i]["nw"]
                if nw:
                    out.append(nw)
    return out


# --------------------------------------------------------------------------------------------------
# CLI — superset of align_book.py's flags + the edit-aware knobs.
# --------------------------------------------------------------------------------------------------

def build_argparser():
    ap = argparse.ArgumentParser(description="Edit-aware (gap-skipping) forced aligner — sibling of "
                                             "align_book.py for chapters the audiobook EDITED.")
    # --- pass-throughs identical to align_book.py ---
    ap.add_argument("--audio", nargs="+", required=True, help="ordered audio files (one chapter/book)")
    ap.add_argument("--text", required=True)
    ap.add_argument("--chapters")
    ap.add_argument("--title", default="Untitled")
    ap.add_argument("--out", default="book.json")
    ap.add_argument("--device", default=None)
    ap.add_argument("--window", type=float, default=150.0)
    ap.add_argument("--safety", type=float, default=12.0)
    ap.add_argument("--sub", type=float, default=30.0)
    ap.add_argument("--wps", type=float, default=2.5, help="narration words/sec (see align_book.py)")
    ap.add_argument("--overprovide", type=float, default=1.0)
    ap.add_argument("--max-seconds", type=float, default=None, help="debug: stop after N audio sec")
    ap.add_argument("--checkpoint", help="resume checkpoint path (LOCAL dir, not Dropbox)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--checkpoint-every", type=int, default=15)
    ap.add_argument("--cooldown-every", type=float, default=0.0)
    ap.add_argument("--cooldown", type=float, default=45.0)
    ap.add_argument("--smartctl")
    ap.add_argument("--smartctl-dev", default="/dev/sdb")
    ap.add_argument("--temp-pause", type=float, default=70.0)
    ap.add_argument("--temp-hot-pause", type=float, default=82.0)
    ap.add_argument("--temp-resume", type=float, default=64.0)
    ap.add_argument("--temp-max-wait", type=float, default=900.0)
    ap.add_argument("--temp-poll-every", type=int, default=1)
    ap.add_argument("--allow-undercover", action="store_true")
    # --- NEW edit-aware knobs ---
    ap.add_argument("--resync-every", type=float, default=90.0,
                    help="committed-audio seconds between routine ASR resync checks (cadence)")
    ap.add_argument("--asr-win", type=float, default=8.0,
                    help="seconds of recently-committed audio ASR'd per resync check (verify_tracks default)")
    ap.add_argument("--resync-min", type=float, default=0.5,
                    help="here-overlap below this triggers the forward search (verify_tracks flags <0.4; "
                         "act a hair earlier)")
    ap.add_argument("--match-min", type=float, default=0.6,
                    help="forward-match overlap required to ACCEPT a cut")
    ap.add_argument("--forward-words", type=int, default=1500,
                    help="alignable tokens of forward text searched for the jump target")
    ap.add_argument("--min-skip-words", type=int, default=40,
                    help="ignore matches nearer than this (step jitter, not a cut)")
    ap.add_argument("--commit-floor-frac", type=float, default=0.5,
                    help="starved-step suspicion trigger (fraction of wps-predicted words committed)")
    ap.add_argument("--max-deadzones", type=int, default=6,
                    help="consecutive unresolved dead-zones -> fail loud rather than smear")
    ap.add_argument("--cut-log", default=None,
                    help="sidecar audit log of detected cuts/dead-zones (default: <out>.cuts.json)")
    return ap


def main():
    a = build_argparser().parse_args()

    import torch, torchaudio, numpy as np
    dev = a.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"  [edit-aware] device={dev}  window={a.window}s safety={a.safety}s sub={a.sub}s  "
          f"resync-every={a.resync_every}s asr-win={a.asr_win}s", file=sys.stderr)

    sentences = read_sentences(a.text)
    tokens = build_tokens(sentences)
    word_time = [None] * len(tokens)                 # (s,e) | None | 'CUT'
    align_idx = [i for i, t in enumerate(tokens) if t["nw"]]
    pos_in_align = 0
    cut_spans = []
    since_resync_sec = 0.0
    deadzones = []                                    # sidecar dead-zone records
    consecutive_deadzones = 0

    # --- both models live on the GPU for the whole run (loaded once, model.train(False) each) ---
    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model(with_star=False).to(dev); model.train(False)
    tokenizer = bundle.get_tokenizer(); aligner = bundle.get_aligner()
    asr = ResyncASR(dev, torch, torchaudio)           # WAV2VEC2_ASR_BASE_960H (verify_tracks bundle)

    stream = AudioStream(a.audio, target_sr=bundle.sample_rate, block_sec=a.sub)
    win, safety, sr = a.window, a.safety, bundle.sample_rate
    grab = int(a.window * a.wps * a.overprovide)
    cut_log = a.cut_log or (a.out + ".cuts.json")
    tail = CommittedTail(np, sr, keep_sec=max(a.asr_win + 1.0, 10.0))   # retain recent committed audio

    def _save():
        save_ckpt(a.checkpoint, pos_in_align, stream.buf_start, word_time, cut_spans, since_resync_sec)

    cool_anchor = 0.0
    if a.resume and a.checkpoint and (os.path.exists(a.checkpoint) or os.path.exists(a.checkpoint + ".bak")):
        c = load_ckpt(a.checkpoint)
        if c is None:
            print("  ! --resume requested but checkpoint AND .bak are both unusable; starting FRESH",
                  file=sys.stderr)
        else:
            pos_in_align = c["pos"]; word_time = c["word_time"]
            cut_spans = c.get("cut_spans", [])        # backward-tolerant: pre-edit-aware ckpt -> []
            since_resync_sec = c.get("since_resync_sec", 0.0)
            stream.fast_forward(c["audio_pos"]); cool_anchor = c["audio_pos"]
            print(f"  RESUMED from {os.path.basename(a.checkpoint)}: {pos_in_align}/{len(align_idx)} words, "
                  f"audio at {c['audio_pos']/60:.1f}min, {len(cut_spans)} cuts so far", file=sys.stderr)
    elif a.checkpoint and (os.path.exists(a.checkpoint) or os.path.exists(a.checkpoint + ".bak")):
        print(f"  ! a checkpoint exists at {a.checkpoint} but --resume was NOT passed -- starting FRESH",
              file=sys.stderr)

    def resync_check(force=False):
        """ASR the last --asr-win seconds of COMMITTED audio and decide whether the audio jumped past a
        cut. Returns ('ok'|'cut'|'deadzone', detail). On 'cut', mutates pos_in_align/word_time/cut_spans
        via apply_skip. force=True ignores the here-overlap gate and always runs the forward search
        (used as the post-skip confirm and on a starved step). The ASR window ends at the current buffer
        head (stream.buf_start); start = head - asr-win, clamped at 0."""
        nonlocal pos_in_align, consecutive_deadzones
        t1 = stream.buf_start                          # the committed head (audio consumed so far)
        t0 = max(0.0, t1 - a.asr_win)
        if t1 - t0 < 0.5:                              # nothing committed yet to look back on
            return "ok", 0.0
        seg = tail.slice(t0, t1)                        # samples for [t0, t1] from the committed-tail ring
        if seg is None or len(seg) < sr * 0.5:
            return "ok", 0.0
        heard = words_of(asr.transcribe(seg))            # the ONLY GPU work in the resync
        here = aligned_text_for_window(tokens, word_time, t0, t1)
        kind, detail = resync_decision(heard, here, tokens, align_idx, pos_in_align,
                                       resync_min=a.resync_min, match_min=a.match_min,
                                       forward_words=a.forward_words, min_skip_words=a.min_skip_words,
                                       force=force)
        # the dead-zone RUN counter lives here (the decision fn is stateless): reset on ok/cut, bump on
        # a real dead-zone, so --max-deadzones counts only CONSECUTIVE unmatched windows.
        consecutive_deadzones = (consecutive_deadzones + 1) if kind == "deadzone" else 0
        return kind, detail

    step = 0
    pending_confirm = False                            # the next committed step must run a confirm-ASR
    last_skip_entry = None
    while pos_in_align < len(align_idx):
        samples, is_final = stream.window(win)
        if len(samples) < sr * 0.5:
            break
        wstart = stream.buf_start
        wlen = len(samples) / sr
        a_take = align_idx[pos_in_align: pos_in_align + grab]
        if not a_take:
            break
        words = [tokens[i]["nw"] for i in a_take]
        emission = emission_for(model, dev, samples, a.sub, sr, torch)
        if emission is None or emission.size(1) == 0:
            break
        spans, nfit = fit_and_align(aligner, tokenizer, emission, words, step)
        if spans is None:
            break
        if nfit < len(a_take):
            print(f"  (step {step}: short tail window held {nfit}/{len(a_take)} queued words; rest re-queued)",
                  file=sys.stderr)
            a_take = a_take[:nfit]
        ratio = len(samples) / emission.size(1) / sr
        accept_thresh = (wstart + wlen) if is_final else (wstart + wlen - safety)

        last_committed_align = -1
        committed_end = wstart
        committed_words = 0
        for k, ti in enumerate(a_take):
            if k >= len(spans): break
            sp = spans[k]
            s = wstart + sp[0].start * ratio
            e = wstart + sp[-1].end * ratio
            if e <= accept_thresh or is_final:
                word_time[ti] = (s, e)
                last_committed_align = pos_in_align + k
                committed_end = e
                committed_words += 1
            else:
                break
        if last_committed_align < 0:                  # nothing trusted -> force one word (progress guarantee)
            if is_final: break
            sp = spans[0]; s = wstart + sp[0].start*ratio; e = wstart + sp[-1].end*ratio
            word_time[a_take[0]] = (s, e); last_committed_align = pos_in_align; committed_end = e
            committed_words = 1

        pos_in_align = last_committed_align + 1
        committed_sec = max(0.0, committed_end - wstart)
        # Retain the audio we're about to drop, so the resync ASR can look back at it after commit.
        ncommit = min(len(samples), int(committed_sec * sr))
        if ncommit > 0:
            tail.push(samples[:ncommit], committed_end)
        stream.commit(committed_sec)
        since_resync_sec += committed_sec
        step += 1

        # ---- RESYNC: after the commit, decide whether a cut just happened ---------------------------
        starved = starved_step(committed_words, committed_sec, a.wps, a.commit_floor_frac)
        do_resync = pending_confirm or starved or (since_resync_sec >= a.resync_every)
        if do_resync and not is_final:
            kind, detail = resync_check(force=pending_confirm or starved)
            since_resync_sec = 0.0
            confirming = pending_confirm                # was THIS step the post-skip confirm?
            pending_confirm = False
            if confirming and kind == "deadzone" and last_skip_entry is not None:
                # Post-skip confirm came back a dead-zone: the skip may have been WRONG. Roll it back and
                # treat the window conservatively (a bad skip self-heals in one step). Handled here in full
                # so it doesn't also fall through and double-count as a fresh dead-zone.
                score_here = detail[0]
                pos_in_align = rollback_skip(word_time, cut_spans, align_idx, last_skip_entry)
                last_skip_entry = None
                print(f"  ROLLBACK @ {wstart/60:.1f}min: post-skip confirm here-overlap "
                      f"{score_here:.2f} (no forward match) — undid the last skip, treating as dead-zone",
                      file=sys.stderr)
                deadzones.append({"audio_min": round(wstart/60, 2), "kind": "rollback",
                                  "resync_score": round(score_here, 3)})
                if consecutive_deadzones >= a.max_deadzones:
                    _save()
                    sys.exit(f"FAIL: {consecutive_deadzones} consecutive dead-zones at {wstart/60:.1f}min "
                             f"after a rolled-back skip — audio unmatched, skipping would smear. "
                             f"Checkpoint saved; inspect this stretch. cuts log: {cut_log}")
            elif kind == "cut":
                # A real forward jump (possibly back-to-back with a prior cut on the confirm step).
                best_off, best_score, score_here = detail
                s0 = tokens[align_idx[pos_in_align]]["seg"]
                last_p = min(pos_in_align + best_off - 1, len(align_idx) - 1)
                s1 = tokens[align_idx[last_p]]["seg"]
                pos_in_align = apply_skip(word_time, cut_spans, align_idx, pos_in_align, best_off,
                                          wstart / 60.0)
                last_skip_entry = cut_spans[-1]
                pending_confirm = True                # next step confirms the skip
                print(f"  CUT @ {wstart/60:.1f}min: skipped {best_off} web words (sentences {s0}..{s1}); "
                      f"here-overlap {score_here:.2f}, forward-match {best_score:.2f}", file=sys.stderr)
            elif kind == "deadzone":
                score_here, best_score = detail
                deadzones.append({"audio_min": round(wstart/60, 2), "kind": "deadzone",
                                  "resync_score": round(score_here, 3), "best_forward": round(best_score, 3)})
                print(f"  DEAD-ZONE @ {wstart/60:.1f}min: here-overlap {score_here:.2f}, best forward "
                      f"{best_score:.2f} (< {a.match_min}); conservative — no skip "
                      f"({consecutive_deadzones}/{a.max_deadzones})", file=sys.stderr)
                if consecutive_deadzones >= a.max_deadzones:
                    _save()
                    sys.exit(f"FAIL: {consecutive_deadzones} consecutive dead-zones at "
                             f"{wstart/60:.1f}min — the audio is unmatched and skipping would smear. "
                             f"Checkpoint saved; inspect this stretch (likely a backward reorder / "
                             f"re-recording). cuts log: {cut_log}")
            else:                                        # kind == "ok"
                last_skip_entry = None

        if step % 20 == 0 or is_final:
            pct = 100 * pos_in_align / len(align_idx)
            print(f"  step {step:4d}  t={committed_end/60:6.1f}min  text {pct:5.1f}%  "
                  f"({pos_in_align}/{len(align_idx)} words, {len(cut_spans)} cuts)", file=sys.stderr)
        if a.checkpoint and step % a.checkpoint_every == 0:
            _save()
        if a.cooldown_every and not is_final and (committed_end - cool_anchor) >= a.cooldown_every:
            if a.checkpoint:
                _save()
            pct = 100 * pos_in_align / len(align_idx)
            print(f"  COOLDOWN t={committed_end/60:.1f}min ({pct:.1f}%): idling {a.cooldown:.0f}s "
                  f"(GPU+CPU quiescent to shed heat)", file=sys.stderr); sys.stderr.flush()
            time.sleep(a.cooldown)
            cool_anchor = committed_end
        if a.smartctl and step % a.temp_poll_every == 0:
            thermal_guard(a, _save)
        if a.max_seconds and committed_end >= a.max_seconds:
            print(f"  (stopped at --max-seconds {a.max_seconds})", file=sys.stderr); break
        if is_final: break

    # ---- assemble (cut-aware: 'CUT' tokens -> zero-duration gap segments) ----
    segments = assemble_segments(tokens, sentences, word_time)
    last_end = max((s["end"] for s in segments), default=0.0)

    doc = {"title": a.title, "audio": os.path.basename(a.audio[0]), "segments": segments}
    if a.chapters:
        with open(a.chapters, encoding="utf-8") as _cf:
            markers = json.load(_cf)
        chs = []
        for m in markers:
            seg = m.get("seg", m.get("first_line"))
            if seg is None or not (0 <= seg < len(segments)): continue
            chs.append({"title": (m.get("title") or "Chapter").strip(),
                        "start": segments[seg]["start"], "seg": seg})
        if chs: doc["chapters"] = chs

    validate_doc(doc, source=a.out)
    with open(a.out, "w", encoding="utf-8") as _of:
        json.dump(doc, _of, ensure_ascii=False, indent=2)

    # ---- sidecar cut audit log (counts/times/sentence-indices only — IP-safe, no prose) ----
    cut_words = sum(c["a1"] - c["a0"] for c in cut_spans)
    with open(cut_log, "w", encoding="utf-8") as _cl:
        json.dump({"title": a.title, "out": os.path.basename(a.out),
                   "n_cuts": len(cut_spans), "cut_words": cut_words,
                   "cuts": cut_spans, "deadzones": deadzones}, _cl, ensure_ascii=False, indent=2)

    if a.checkpoint:
        for _ck in (a.checkpoint, a.checkpoint + ".bak", a.checkpoint + ".tmp"):
            try: os.remove(_ck)
            except OSError: pass

    naligned = sum(1 for w in word_time if isinstance(w, tuple))
    print(f"Wrote {a.out}: {len(segments)} sentences, {naligned}/{len(align_idx)} words aligned, "
          f"{len(cut_spans)} cuts ({cut_words} words skipped), {len(deadzones)} dead-zones, "
          f"{last_end/60:.1f} min. Cut log: {cut_log}", file=sys.stderr)

    # ---- coverage fail-loud (edit-aware: skipped 'CUT' words are resolved, count toward text exhaustion) ----
    import soundfile as _sf
    total_audio = sum(_sf.info(f).duration for f in a.audio)
    text_exhausted = pos_in_align >= len(align_idx)
    audio_gap = total_audio - last_end
    undercovered = False
    if naligned == 0:
        sys.exit("FAIL: 0 words aligned — check the audio/text inputs.")
    if a.max_seconds:
        pass
    elif text_exhausted and audio_gap > max(120.0, 0.02 * total_audio):
        print(f"\n*** COVERAGE GAP — FAILING LOUD ***\n"
              f"    text ran out at {last_end/60:.1f} min, but the audio is {total_audio/60:.1f} min "
              f"({audio_gap/60:.1f} min / {100*audio_gap/total_audio:.0f}% UNALIGNED).\n"
              f"    With edit-aware skips this most likely means a MISSING text slice (not an audio cut),\n"
              f"    or the audio has un-narrated tails. Fetch the rest / check inputs, or pass "
              f"--allow-undercover if intended.", file=sys.stderr)
        undercovered = not a.allow_undercover
    elif not text_exhausted:
        unused = len(align_idx) - pos_in_align
        if unused:
            print(f"    note: audio fully covered ({last_end/60:.1f} min); {unused} words of over-fetched "
                  f"text beyond the audiobook's end were unused (expected, not an error).", file=sys.stderr)
    if undercovered:
        sys.exit(3)


if __name__ == "__main__":
    main()
