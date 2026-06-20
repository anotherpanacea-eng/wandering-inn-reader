#!/usr/bin/env python3
"""
test_wps_check.py — the threshold-logic check for the wps boundary pre-screen (spec-wps-gate §8).
wps_check.screen() takes PRE-COMPUTED unit dicts {label, wps, minutes, words}, so the decision logic
is testable on pure data with no soundfile/audio/torch — the audio+word extraction is the thin
device-touching shell around it. Plain stdlib asserts (the repo has no pytest); run directly:
`python3 tests/test_wps_check.py`. Exit 0 = pass.

Covers the spec's required cases plus the REVIEW P2-1 hardenings:
  * a clean book (all within tol) passes;
  * one squished + one slack unit flags BOTH and reports the adjacent PAIR;
  * a slack partner that sits INSIDE both the 35% tol and the 1.7 floor is still caught by
    FLAG-BY-ASSOCIATION (the P2-1b fix — without it the low member of a real mis-boundary passes alone);
  * the 1.7 absolute floor catches a -33% slack member a 1.5 floor would miss (P2-1a);
  * an all-shifted book trips SYSTEMATIC (median leaves the band);
  * > 40%-flagged trips SYSTEMATIC;
  * a < 5-unit set falls back to the absolute band only.
"""
import math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pipeline"))

import wps_check                     # noqa: E402


def _book(n=20, wps=2.36):
    return [{"label": f"u{i:02d}", "wps": wps, "minutes": 60.0, "words": 8000} for i in range(n)]


def test_clean_book_passes():
    r = wps_check.screen(_book())
    assert r["ok"], r
    assert not r["flagged"] and not r["systematic"], r
    assert abs(r["median"] - 2.36) < 1e-9, r["median"]


def test_squished_slack_pair_flags_both_and_reports_pair():
    units = _book()
    units[10] = {"label": "5.12", "wps": 5.50, "minutes": 40.0, "words": 13000}   # squished, +133%
    units[11] = {"label": "5.13", "wps": 1.59, "minutes": 185.0, "words": 17000}  # slack, -33% (< 1.7 floor)
    r = wps_check.screen(units)
    assert not r["ok"], r
    flagged = {f["idx"] for f in r["flagged"]}
    assert 10 in flagged and 11 in flagged, flagged
    pairs = wps_check._pairs(r["flagged"])
    assert any({lo["idx"], hi["idx"]} == {10, 11} for lo, hi in pairs), pairs
    causes = {f["idx"]: f["cause"] for f in r["flagged"]}
    assert causes[10] == "squished" and causes[11] == "slack", causes


def test_slack_partner_inside_tol_caught_by_association():
    # The P2-1b case: the slack side at -22% (1.85 w/s) is INSIDE the 35% tol AND above the 1.7 floor,
    # so it passes both direct tests. It must still flag because its neighbor is a high outlier.
    units = _book()
    units[10] = {"label": "HIGH", "wps": 3.40, "minutes": 60.0, "words": 8000}   # +44% squished
    units[11] = {"label": "low",  "wps": 1.85, "minutes": 60.0, "words": 8000}   # -22%: inside tol + floor
    r = wps_check.screen(units)
    reasons = {f["idx"]: f["reason"] for f in r["flagged"]}
    assert 10 in reasons and 11 in reasons, reasons
    assert reasons[11] == "association", reasons


def test_floor_1p7_catches_minus_33_a_1p5_floor_would_miss():
    # P2-1a: a lone slack unit at 1.59 w/s (-33%) is inside the 35% tol AND above a 1.5 floor; only the
    # raised 1.7 floor catches it. (Standalone, no high partner, to isolate the floor from association.)
    units = _book()
    units[7] = {"label": "lone-slack", "wps": 1.59, "minutes": 185.0, "words": 17000}
    assert not wps_check.screen(units, abs_lo=1.7)["ok"], "the 1.7 floor must flag a -33% slack unit"
    assert wps_check.screen(units, abs_lo=1.5)["ok"], "a 1.5 floor would MISS it (why the spec raised it to 1.7)"


def test_all_shifted_trips_systematic():
    r = wps_check.screen(_book(wps=1.20))      # whole book below the band -> median itself out of band
    assert r["systematic"], r
    assert "band" in (r["systematic_reason"] or ""), r["systematic_reason"]


def test_majority_flagged_trips_systematic():
    # Median stays in-band (most units at 2.36) but > 40% of units are individually out of band ->
    # the median is not trustworthy. 5 of 11 units (45%) pushed above the 3.5 hi band.
    units = _book(n=11)
    for i in range(5):
        units[i]["wps"] = 3.9
    r = wps_check.screen(units)
    assert abs(r["median"] - 2.36) < 1e-9, r["median"]    # median still in-band: not the band rule
    assert r["systematic"], r                             # tripped by the > 40%-flagged rule
    assert "units flag" in (r["systematic_reason"] or ""), r["systematic_reason"]


def test_too_few_units_absolute_band_only():
    r = wps_check.screen(_book(n=3, wps=2.4))
    assert r["too_few"] and r["ok"], r                    # 3 clean units within band pass
    bad = wps_check.screen([{"label": "x", "wps": 0.5, "minutes": 60, "words": 8000}])
    assert not bad["ok"], "a single out-of-band unit must still fail on the absolute band"


def test_chapter_wps_zero_on_bad_duration():
    assert wps_check.chapter_wps(8000, 0) == 0.0          # UNMEASURED -> 0.0 -> below any floor -> a FAIL
    assert wps_check.chapter_wps(8000, None) == 0.0
    assert abs(wps_check.chapter_wps(8000, 4000) - 2.0) < 1e-9


def test_nonfinite_wps_cannot_bypass_hard_gate():
    # Codex #22: a NaN/inf wps (zero/garbage duration or word-count computed upstream) must HARD-FAIL --
    # every comparison against it is False, so it would slip through unflagged AND corrupt the median.
    for bad in (float("nan"), float("inf"), float("-inf")):
        r = wps_check.screen(_book(n=8) + [{"label": "bad", "wps": bad, "minutes": 0.0, "words": 100}])
        assert not r["ok"], f"{bad} bypassed the hard gate"
        assert any(f["reason"] == "non-finite" for f in r["flagged"]), r["flagged"]
        assert math.isfinite(r["median"]) and abs(r["median"] - 2.36) < 1e-9, r["median"]  # median uncorrupted


def main():
    test_clean_book_passes()
    test_squished_slack_pair_flags_both_and_reports_pair()
    test_slack_partner_inside_tol_caught_by_association()
    test_floor_1p7_catches_minus_33_a_1p5_floor_would_miss()
    test_all_shifted_trips_systematic()
    test_majority_flagged_trips_systematic()
    test_too_few_units_absolute_band_only()
    test_chapter_wps_zero_on_bad_duration()
    test_nonfinite_wps_cannot_bypass_hard_gate()
    print("OK test_wps_check: clean / pair+report / association / 1.7-floor / systematic(band) / "
          "systematic(majority) / too-few / unmeasured / non-finite all pass")


if __name__ == "__main__":
    main()
