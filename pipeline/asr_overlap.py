#!/usr/bin/env python3
"""
asr_overlap.py — the SINGLE source of truth for the ASR<->text word-overlap score.

verify_tracks.py uses this number as its ship GATE (a point PASSES when the heard
words cover >= --min-overlap of the aligned-window words). align_book_editaware.py
uses the SAME number, with the SAME normalisation, to decide when the read-along has
fallen off the audio (the resync check) — so the aligner acts on exactly the metric
the gate later judges it by. Keeping it in one place is the discipline the edit-aware
spec calls for: the resync threshold and the gate threshold must be measured the same
way, or "I scored 0.55 here" in the aligner won't mean what verify_tracks means.

Pure stdlib (no torch / no numpy) so the cut-detection logic is unit-testable on
synthetic transcripts with no GPU.
"""
import re

_KEEP = re.compile(r"[^a-z']")


def norm(w):
    """Lower-case and strip to letters+apostrophes (apostrophes only kept internally),
    matching align_book.normalize_word and verify_tracks.norm exactly."""
    return _KEEP.sub("", w.lower()).strip("'")


def words_of(text):
    """Normalised, non-empty word list of `text` (the verify_tracks tokenisation)."""
    return [w for w in (norm(x) for x in text.split()) if w]


def overlap_score(aligned_words, heard_words):
    """Fraction of the ALIGNED window's distinct words that appear in the HEARD ASR.

    This is verify_tracks's exact metric: |set(aligned) & set(heard)| / |set(aligned)|.
    It is asymmetric on purpose — it asks "is the text we're showing actually spoken
    here", which is the read-along's job. Empty aligned text scores 0.0 (nothing to
    confirm -> not on track). `aligned_words` / `heard_words` are word lists (already
    normalised, e.g. from words_of) OR raw strings (auto-tokenised for convenience)."""
    if isinstance(aligned_words, str):
        aligned_words = words_of(aligned_words)
    if isinstance(heard_words, str):
        heard_words = words_of(heard_words)
    aw, hw = set(aligned_words), set(heard_words)
    return (len(aw & hw) / len(aw)) if aw else 0.0
