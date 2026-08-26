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
import math
import re
from difflib import SequenceMatcher

_KEEP = re.compile(r"[^a-z']")
_FUZZY_MIN_LEN = 4          # only fuzzy-match content-length words; short function words stay exact


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


def _validate_fuzzy_threshold(thresh):
    """Return a finite fuzzy threshold in the fail-closed interval ``(0, 1]``."""
    try:
        value = float(thresh)
    except (TypeError, ValueError) as exc:
        raise ValueError("fuzzy threshold must be a finite number in (0, 1]") from exc
    if not math.isfinite(value) or not 0.0 < value <= 1.0:
        raise ValueError("fuzzy threshold must be a finite number in (0, 1]")
    return value


def _fuzzy_matches(aligned, heard, thresh):
    """Maximum one-to-one fuzzy matches after exact matches are removed.

    One ASR token cannot cover several similar aligned words. Requiring both sides
    to have at least four characters also keeps short function words exact-only.
    """
    candidates = {}
    for word in aligned:
        if len(word) < _FUZZY_MIN_LEN:
            continue
        matches = []
        for heard_word in heard:
            if len(heard_word) < _FUZZY_MIN_LEN:
                continue
            if abs(len(heard_word) - len(word)) > 2:
                continue
            ratio = SequenceMatcher(None, word, heard_word).ratio()
            if ratio >= thresh:
                matches.append((ratio, heard_word))
        candidates[word] = [heard_word for _, heard_word in sorted(matches, reverse=True)]

    assigned = {}

    def augment(word, visited):
        for heard_word in candidates.get(word, ()):
            if heard_word in visited:
                continue
            visited.add(heard_word)
            prior = assigned.get(heard_word)
            if prior is None or augment(prior, visited):
                assigned[heard_word] = word
                return True
        return False

    return sum(1 for word in sorted(candidates) if augment(word, set()))


def overlap_score_fuzzy(aligned_words, heard_words, thresh=0.8):
    """Name-tolerant variant of ``overlap_score`` for proper-noun-dense prose,
    where wav2vec2 ASR reliably mangles fantasy names and the exact-match metric then
    FALSE-FLAGS correctly-aligned points. Same asymmetric "is the shown text spoken here"
    question and empty->0.0 convention; an aligned word counts as covered on an exact OR
    one-to-one near (>= thresh) match. Use only for dense-name books -- the exact
    ``overlap_score`` remains the default gate."""
    thresh = _validate_fuzzy_threshold(thresh)
    if isinstance(aligned_words, str):
        aligned_words = words_of(aligned_words)
    if isinstance(heard_words, str):
        heard_words = words_of(heard_words)
    aw = set(aligned_words)
    if not aw:
        return 0.0
    hw = set(heard_words)
    exact = aw & hw
    covered = len(exact) + _fuzzy_matches(aw - exact, hw - exact, thresh)
    return covered / len(aw)
