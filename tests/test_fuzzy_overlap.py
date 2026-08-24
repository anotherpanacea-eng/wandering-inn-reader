#!/usr/bin/env python3
"""Pure-stdlib boundary tests for the opt-in fuzzy ASR overlap gate."""

from __future__ import annotations

import contextlib
import io
import math
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE))

from asr_overlap import overlap_score, overlap_score_fuzzy  # noqa: E402
import verify_tracks  # noqa: E402


def test_default_gate_stays_exact_and_fuzzy_is_opt_in():
    args = verify_tracks.build_parser().parse_args([])
    assert args.fuzzy is False
    assert args.fuzzy_thresh == 0.8
    assert overlap_score("orthanon arrives", "orthenon arrives") == 0.5
    assert overlap_score_fuzzy("orthanon arrives", "orthenon arrives") == 1.0


def test_short_words_remain_exact_on_both_sides():
    assert overlap_score_fuzzy(["the", "four"], ["tha", "for"]) == 0.0
    assert overlap_score_fuzzy(["the", "four"], ["the", "for"]) == 0.5


def test_fuzzy_matches_are_one_to_one():
    # One heard token must not cover two distinct aligned variants.
    assert overlap_score_fuzzy(["orthanon", "orthenon"], ["orthanon"]) == 0.5


def test_threshold_boundary_is_inclusive():
    # SequenceMatcher('abcd', 'abce') == 0.75 exactly.
    assert overlap_score_fuzzy(["abcd"], ["abce"], thresh=0.75) == 1.0
    assert overlap_score_fuzzy(["abcd"], ["abce"], thresh=0.750001) == 0.0


def test_empty_aligned_text_stays_fail_closed():
    assert overlap_score_fuzzy([], ["orthanon"]) == 0.0


def test_invalid_thresholds_fail_loud():
    for value in (0, -0.1, 1.01, math.nan, math.inf, "nope"):
        try:
            overlap_score_fuzzy(["orthanon"], ["orthenon"], thresh=value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"threshold {value!r} did not fail")

    parser = verify_tracks.build_parser()
    for value in ("0", "-0.1", "1.01", "nan", "inf"):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(["--fuzzy-thresh", value])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError(f"CLI threshold {value!r} did not fail")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"fuzzy overlap: {len(tests)} boundary/default-off tests pass")
