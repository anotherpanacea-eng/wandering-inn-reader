#!/usr/bin/env python3
"""
test_editaware.py — SYNTHETIC, no-GPU unit test for align_book_editaware.py's cut-detection /
cursor-skip / gap-segment-emission LOGIC. The forced-alignment and ASR calls are the thin device
shell around the pure functions tested here (forward_match, apply_skip, rollback_skip, starved_step,
aligned_text_for_window, assemble_segments cut-awareness), so the skip/emit decision is fully testable
on data with NO torch / NO GPU / NO audio.

Plain stdlib asserts (the repo has no pytest); run directly:  python3 tests/test_editaware.py
Exit 0 = pass. Mirrors tests/test_align.py's style and the spec's §10 no-GPU acceptance items.
"""
import os, sys

try:                                       # a cp1252 Windows console can't encode the check glyph
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pipeline"))

import align_book_editaware as ea       # noqa: E402
from align_book import build_tokens, assemble_segments   # noqa: E402
from asr_overlap import words_of, overlap_score          # noqa: E402
from schema import validate_doc                           # noqa: E402


# --- a synthetic chapter with DISTINCT words everywhere so set-overlap is unambiguous -------------
# Built so a cut is genuinely FAR from the cursor (past --min-skip-words) and on-cursor text has real,
# non-matching forward text after it (so a no-cut window can't accidentally match a distant span).
# IMPORTANT: align_book.normalize_word strips to LETTERS only (digits removed), so the unique tokens
# must be letters-only — we spell each (sentence, word) index as letters so every token is distinct
# AND survives normalisation (zero cross-sentence overlap).
_ALPHA = "abcdefghijklmnopqrstuvwxyz"

def _letters(n):
    """Map a small int to a short distinct letter string (base-26, letters only)."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = _ALPHA[r] + s
    return s

def _make_sentences(n_sents=20, words_per=10):
    out = []
    for si in range(n_sents):
        # e.g. "sa wa sa wb ..." -> distinct letter tokens "s<si> w<wi>" joined, all letters
        out.append(" ".join(f"sent{_letters(si)}word{_letters(wi)}" for wi in range(words_per)))
    return out

SENTENCES = _make_sentences()                       # 20 sentences x 10 words = 200 alignable tokens
CUT_FROM_SEG = 2                                     # the audiobook (pretend) cuts segs 2..8 ...
CUT_TO_SEG = 9                                       # ... and the audio resumes at seg 9


def _tokens_and_idx():
    tokens = build_tokens(SENTENCES)
    align_idx = [i for i, t in enumerate(tokens) if t["nw"]]
    return tokens, align_idx


def _seg_start_pos(tokens, align_idx, seg):
    return next(p for p, ti in enumerate(align_idx) if tokens[ti]["seg"] == seg)


def _heard_of(seg):
    return words_of(SENTENCES[seg])


def test_forward_match_finds_a_clear_cut():
    """A heard transcript that matches seg 9 — clearly AHEAD of a cursor sitting at the cut start (seg 2)
    — is detected as a forward jump well past --min-skip-words, landing on seg 9's first alignable word."""
    tokens, align_idx = _tokens_and_idx()
    pos = _seg_start_pos(tokens, align_idx, CUT_FROM_SEG)        # cursor where the cut begins
    heard = _heard_of(CUT_TO_SEG)                                 # the ASR heard the POST-cut narration
    best_off, best_score = ea.forward_match(heard, tokens, align_idx, pos,
                                            forward_words=1500, min_skip_words=40)
    to_start = _seg_start_pos(tokens, align_idx, CUT_TO_SEG)
    landed_seg = tokens[align_idx[pos + best_off]]["seg"]
    assert best_score >= 0.6, f"clear match should score high, got {best_score:.2f}"
    assert best_off >= 40, "the jump must clear the min-skip floor"
    # The windowed overlap peaks on a small PLATEAU of offsets that fully contain seg 9 (any width-13
    # window over seg 9's 10 words ties), so the cursor lands AT or a few words before seg 9's start —
    # forced alignment then self-corrects. Assert it landed in seg 9's immediate neighbourhood, not drifting.
    span_w = max(1, int(len(heard) * 1.3))
    assert 0 <= (to_start - pos) - best_off <= span_w - len(heard), \
        f"offset {best_off} should land within a window-width of seg{CUT_TO_SEG} start {to_start-pos}"
    assert landed_seg in (CUT_TO_SEG - 1, CUT_TO_SEG), \
        f"cursor landed in seg {landed_seg}, expected the seg{CUT_TO_SEG} neighbourhood"


def test_forward_match_on_cursor_does_not_fire():
    """When the heard words ARE the text at the cursor (no cut), no DISTANT span (>= --min-skip-words
    ahead) matches — every sentence is distinct — so no cut is accepted."""
    tokens, align_idx = _tokens_and_idx()
    pos = _seg_start_pos(tokens, align_idx, CUT_FROM_SEG)
    heard = _heard_of(CUT_FROM_SEG)          # exactly the cursor text (audio is ON track here)
    best_off, best_score = ea.forward_match(heard, tokens, align_idx, pos,
                                            forward_words=1500, min_skip_words=40)
    assert not (best_score >= 0.6 and best_off >= 40), \
        f"on-cursor text must not trigger a far skip (off={best_off}, score={best_score:.2f})"


def test_apply_skip_marks_cut_and_advances():
    """apply_skip marks the skipped alignable tokens 'CUT', records a cut_spans entry, and returns the
    advanced cursor; the audio buffer is untouched (caller's concern)."""
    tokens, align_idx = _tokens_and_idx()
    word_time = [None] * len(tokens)
    cut_spans = []
    pos = _seg_start_pos(tokens, align_idx, CUT_FROM_SEG)
    to_start = _seg_start_pos(tokens, align_idx, CUT_TO_SEG)
    best_off = to_start - pos
    new_pos = ea.apply_skip(word_time, cut_spans, align_idx, pos, best_off, audio_min=12.3)
    assert new_pos == pos + best_off
    for p in range(pos, pos + best_off):
        assert word_time[align_idx[p]] == "CUT", f"token at align pos {p} should be CUT"
    # segs CUT_FROM_SEG..CUT_TO_SEG-1 are entirely CUT; the boundary segs are untouched
    cut_segs = {tokens[align_idx[p]]["seg"] for p in range(pos, pos + best_off)}
    assert cut_segs == set(range(CUT_FROM_SEG, CUT_TO_SEG)), cut_segs
    assert len(cut_spans) == 1 and cut_spans[0]["a0"] == pos and cut_spans[0]["a1"] == pos + best_off
    assert cut_spans[0]["n_words"] == best_off and cut_spans[0]["kind"] == "cut"


def test_rollback_restores_state():
    """rollback_skip undoes apply_skip AND the confirm-step commits made after it (Codex #23): the next
    step aligns words [a1, cur_pos) against the post-skip audio BEFORE the confirm runs, so rollback must
    clear the WHOLE [a0, cur_pos) span (CUT marks + those stale timestamps), drop the entry, return a0."""
    tokens, align_idx = _tokens_and_idx()
    word_time = [None] * len(tokens)
    cut_spans = []
    pos = _seg_start_pos(tokens, align_idx, CUT_FROM_SEG)
    to_start = _seg_start_pos(tokens, align_idx, CUT_TO_SEG)
    a1 = ea.apply_skip(word_time, cut_spans, align_idx, pos, to_start - pos, 12.3)   # cursor -> a1
    entry = cut_spans[-1]
    # the confirm step commits a few words AFTER a1 against the (wrong) post-skip audio, advancing cursor:
    cur_pos = min(a1 + 3, len(align_idx))
    for p in range(a1, cur_pos):
        word_time[align_idx[p]] = (float(p), float(p) + 0.5)                         # stale committed times
    restored = ea.rollback_skip(word_time, cut_spans, align_idx, entry, cur_pos)
    assert restored == pos, restored
    assert cut_spans == [], "the cut entry must be removed"
    # BOTH the skip span [pos, a1) AND the confirm-step commits [a1, cur_pos) must be cleared:
    assert all(word_time[align_idx[p]] is None for p in range(pos, cur_pos)), \
        "rollback must clear the skip span AND the stale confirm-step commits"


def test_assemble_emits_valid_zero_duration_gap_segments():
    """After a skip, the CUT sentences assemble into ZERO-DURATION gap segments that pass validate_doc,
    and a 'CUT' token does NOT make the assembler treat later real sentences as unaligned (first_unaligned
    must ignore 'CUT'). Aligns seg 0..1 (real), CUTs 2..8, aligns 9 (real, post-cut), leaves 10+ None."""
    tokens, align_idx = _tokens_and_idx()
    word_time = [None] * len(tokens)
    cut_spans = []
    real_before = list(range(0, CUT_FROM_SEG))       # segs 0,1 aligned before the cut
    t = 0.0
    for ti, tok in enumerate(tokens):
        if tok["seg"] in real_before and tok["nw"]:
            word_time[ti] = (t, t + 0.3); t += 0.3
    pos = _seg_start_pos(tokens, align_idx, CUT_FROM_SEG)
    to_start = _seg_start_pos(tokens, align_idx, CUT_TO_SEG)
    ea.apply_skip(word_time, cut_spans, align_idx, pos, to_start - pos, 0.0)
    # align seg 9 (post-cut) with real timings; segs 10+ stay None -> doc truncates after 9
    t = 50.0
    for ti, tok in enumerate(tokens):
        if tok["seg"] == CUT_TO_SEG and tok["nw"]:
            word_time[ti] = (t, t + 0.3); t += 0.3

    segments = assemble_segments(tokens, SENTENCES, word_time)
    # segs 0..9 emitted (the CUT ones too — the reader sees the skipped prose); 10+ truncated (None)
    assert [s["id"] for s in segments] == list(range(0, CUT_TO_SEG + 1)), \
        f"a CUT must NOT truncate the doc; got {[s['id'] for s in segments]}"
    # the cut sentences are zero-duration at the boundary clock
    for si in range(CUT_FROM_SEG, CUT_TO_SEG):
        seg = segments[si]
        assert abs(seg["end"] - seg["start"]) < 1e-6, f"seg {si} should be zero-duration, got {seg}"
        for w in seg["words"]:
            assert abs(w["e"] - w["s"]) < 1e-6, f"cut word should be zero-width: {w}"
    # the post-cut sentence keeps its real, non-zero duration
    assert segments[CUT_TO_SEG]["end"] > segments[CUT_TO_SEG]["start"], "post-cut sentence keeps real timing"
    # schema-valid (zero-duration end==start is allowed), and segment starts stay non-decreasing
    validate_doc({"title": "T", "audio": "a.mp3", "segments": segments}, source="test")


def test_no_cut_assembles_like_the_greedy_path():
    """With NO 'CUT' marks, assemble_segments behaves exactly as align_book's greedy assembly: a trailing
    unaligned (None) alignable token truncates the doc at the first sentence holding one."""
    tokens, align_idx = _tokens_and_idx()
    word_time = [None] * len(tokens)
    # align segs 0 and 1 fully; leave 2+ unaligned (None) -> doc should stop after seg 1
    t = 0.0
    for ti, tok in enumerate(tokens):
        if tok["seg"] in (0, 1) and tok["nw"]:
            word_time[ti] = (t, t + 0.2); t += 0.2
    segments = assemble_segments(tokens, SENTENCES, word_time)
    assert [s["id"] for s in segments] == [0, 1], \
        f"a trailing None alignable token must truncate at seg 1, got {[s['id'] for s in segments]}"


def test_starved_step_flags_under_committed():
    """A step that commits far fewer words than its audio span predicts at wps is 'starved' (cut suspicion);
    a healthy step is not."""
    # wps 2.5, 30s committed -> expected ~75 words; floor-frac 0.5 -> threshold 37.5
    assert ea.starved_step(committed_words=10, committed_sec=30.0, wps=2.5, commit_floor_frac=0.5)
    assert not ea.starved_step(committed_words=70, committed_sec=30.0, wps=2.5, commit_floor_frac=0.5)
    assert not ea.starved_step(committed_words=0, committed_sec=0.0, wps=2.5, commit_floor_frac=0.5)


def test_aligned_text_for_window_reads_committed_only():
    """aligned_text_for_window returns the normalised web words whose committed (s,e) overlaps [t0,t1],
    ignoring None and 'CUT' tokens — the 'here' side of the resync overlap."""
    tokens, align_idx = _tokens_and_idx()
    word_time = [None] * len(tokens)
    # put seg 0's words at 0..3s, mark seg 1 CUT, leave the rest None
    t = 0.0
    for ti, tok in enumerate(tokens):
        if tok["seg"] == 0 and tok["nw"]:
            word_time[ti] = (t, t + 0.3); t += 0.3
        elif tok["seg"] == 1 and tok["nw"]:
            word_time[ti] = "CUT"
    here = ea.aligned_text_for_window(tokens, word_time, 0.0, 2.0)
    seg0_words = set(words_of(SENTENCES[0]))
    seg1_words = set(words_of(SENTENCES[1]))
    assert seg0_words & set(here), f"seg 0 words should appear in the here-window: {here}"
    assert not (seg1_words & set(here)), "CUT tokens must not appear in the aligned-here words"
    # a window with nothing committed -> empty
    assert ea.aligned_text_for_window(tokens, word_time, 50.0, 52.0) == []


def test_committed_tail_ring_slices_recent_audio():
    """CommittedTail retains the last keep_sec of committed audio and can slice a sub-window back out
    AFTER the main stream dropped it (the resync look-back). Uses a plain list-backed fake of numpy so
    the test needs no numpy/torch."""
    class Arr(list):
        """A list that mimics the two numpy methods CommittedTail uses: slicing returns an Arr (so it
        keeps .copy()), and .copy() returns an Arr. Lets us exercise the ring with no numpy/torch."""
        def __getitem__(self, k):
            r = super().__getitem__(k)
            return Arr(r) if isinstance(k, slice) else r
        def copy(self): return Arr(self)

    class FakeNp:
        float32 = "f"
        @staticmethod
        def zeros(n, dtype="f"): return Arr([0.0] * n)
        @staticmethod
        def concatenate(parts):
            out = Arr()
            for p in parts: out.extend(p)
            return out

    sr = 100  # 100 "samples"/sec for an easy-to-reason synthetic clock
    tail = ea.CommittedTail(FakeNp, sr, keep_sec=2.0)   # keep 200 samples
    # commit 3 one-second blocks; block k has value k+1, ending at global times 1,2,3
    for k in range(3):
        block = Arr([float(k + 1)] * sr)
        tail.push(block, end_t=float(k + 1))
    # only the last 2s (values 2 then 3) remain; slice [1.0, 3.0] -> 200 samples, first half 2.0, second 3.0
    seg = tail.slice(1.0, 3.0)
    assert seg is not None and len(seg) == 200, f"expected 200 samples, got {None if seg is None else len(seg)}"
    assert seg[0] == 2.0 and seg[-1] == 3.0, (seg[0], seg[-1])
    # asking for audio older than the ring no longer holds -> None (don't ASR stale/missing audio)
    assert tail.slice(0.0, 1.0) is None, "a window dropped from the ring must return None"


def test_resync_decision_cut_oncursor_deadzone():
    """The integrated resync DECISION (spec §10): given stubbed HEARD + HERE transcripts and a cursor,
    it returns 'cut' (advance to a forward match), 'ok' (on cursor), or 'deadzone' (unmatched). No GPU."""
    tokens, align_idx = _tokens_and_idx()
    pos = _seg_start_pos(tokens, align_idx, CUT_FROM_SEG)
    kw = dict(resync_min=0.5, match_min=0.6, forward_words=1500, min_skip_words=40)

    # (a) CUT: the audio (heard) is seg 9, but the text aligned HERE is seg 2 (the cut start) -> jump.
    heard_cut = _heard_of(CUT_TO_SEG)
    here_off = _heard_of(CUT_FROM_SEG)               # what's currently aligned to this window (wrong)
    kind, detail = ea.resync_decision(heard_cut, here_off, tokens, align_idx, pos, force=False, **kw)
    assert kind == "cut", (kind, detail)
    best_off, best_score, score_here = detail
    landed = tokens[align_idx[pos + best_off]]["seg"]
    assert best_score >= 0.6 and landed in (CUT_TO_SEG - 1, CUT_TO_SEG), (best_off, best_score, landed)
    assert score_here < 0.5, "here-overlap should be low when off the audio"

    # (b) ON CURSOR (no cut): heard == the text aligned here -> 'ok', no skip (cadence path).
    heard_ok = _heard_of(CUT_FROM_SEG)
    here_ok = _heard_of(CUT_FROM_SEG)
    kind, _ = ea.resync_decision(heard_ok, here_ok, tokens, align_idx, pos, force=False, **kw)
    assert kind == "ok", kind

    # (c) DEAD-ZONE: heard matches NOTHING (silence/music) and here-overlap is low -> conservative, no skip.
    heard_dead = words_of("zzqx qxzz noise hiss static rumble")   # no overlap with any sentence
    here_dead = _heard_of(CUT_FROM_SEG)
    kind, detail = ea.resync_decision(heard_dead, here_dead, tokens, align_idx, pos, force=False, **kw)
    assert kind == "deadzone", (kind, detail)

    # (d) FORCED confirm that's actually fine (high here-overlap, no forward jump) -> 'ok', NOT a deadzone.
    kind, _ = ea.resync_decision(heard_ok, here_ok, tokens, align_idx, pos, force=True, **kw)
    assert kind == "ok", f"a clean forced confirm must be 'ok', not {kind}"


def test_overlap_metric_matches_verify_tracks():
    """Sanity: the shared overlap metric is verify_tracks's asymmetric set-overlap (|a&h|/|a|)."""
    assert overlap_score(["a", "b", "c", "d"], ["a", "b", "z"]) == 0.5
    assert overlap_score([], ["a"]) == 0.0
    assert abs(overlap_score("alpha bravo charlie", "bravo charlie") - 2 / 3) < 1e-9


def main():
    test_forward_match_finds_a_clear_cut()
    test_forward_match_on_cursor_does_not_fire()
    test_apply_skip_marks_cut_and_advances()
    test_rollback_restores_state()
    test_assemble_emits_valid_zero_duration_gap_segments()
    test_no_cut_assembles_like_the_greedy_path()
    test_starved_step_flags_under_committed()
    test_aligned_text_for_window_reads_committed_only()
    test_committed_tail_ring_slices_recent_audio()
    test_resync_decision_cut_oncursor_deadzone()
    test_overlap_metric_matches_verify_tracks()
    print("✓ test_editaware: forward-match cut detect / on-cursor no-skip / apply+rollback / "
          "zero-duration gap segments / greedy-parity / starved-step / committed-tail ring — all pass")


if __name__ == "__main__":
    main()
