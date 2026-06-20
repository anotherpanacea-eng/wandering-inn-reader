#!/usr/bin/env python3
"""
wps_check.py -- NO-GPU words-per-second boundary pre-screen. The shared formula + screen that both
the ship gate (verify_tracks.py --wps-pre) and the QA workbench (align_qa.py wps-check) import, so the
wps math lives in EXACTLY ONE place and the numbers MATCH align_chapters.py --auto-wps.

Why this exists (the Book 07 incident): two embedded chapter marks were ~60 minutes out of place.
--auto-wps dutifully computed one wps = n_alignable_words / audio_seconds per (mis-bounded) unit, the
per-chapter forced alignment mapped each chapter onto the WRONG audio span, and the result was
schema-valid + monotonic -- it looked shippable. The sparse ASR gate sampled ~5 points/track and the
misplaced points happened to land where overlap was tolerable, so it PASSED. What caught it was this
arithmetic: a mis-boundary STEALS audio from one unit (too much audio for the text -> wps reads LOW =
"slack") and gives it to a neighbor (text squished into too little audio -> wps reads HIGH = "squished").
A high/low PAIR on adjacent units, both far from the book median, is the wrong-boundary signature. This
screen flags it for nothing -- no GPU, no model load -- before any ASR run.

Pure CPU: imports n_alignable from align_chapters (the EXACT --auto-wps numerator) + soundfile + stdlib.
No torch. Runnable safely while a GPU alignment is in flight. screen() takes pre-computed unit dicts so
the threshold logic is testable on pure data; the audio/word extraction is the thin device-touching shell.

ASCII-only output (cp1252 console-safe): "+-" not the plus-minus sign, "->" not arrows.
"""
import argparse, glob, json, math, os, statistics, sys

from align_chapters import n_alignable, load_track_map, track_no   # reuse VERBATIM so numbers match
from schema import validate_doc

# Default tolerances. The relative tol RECONCILES the gate spec's +-35% against the workbench spec's
# +-15%: 35% is the MEASURED-safe value (Book 07's natural spread is +-6%, MAD ~3.4% of median, so 35%
# is ~6-10x the honest variation -- it cannot fire on pace, only on a stolen/surrendered boundary). The
# module owns 35% as its single default; an advisory caller (the workbench) may pass a tighter tol, but
# the GATE uses 35% so "one implementation" does not ship two different defaults.
DEFAULT_TOL = 0.35
# Absolute narration band (English audiobook w/s). The measured Book 07 band is 2.21..2.49; the floor is
# raised to 1.7 (not 1.5) because the SLACK side of a real 60-min mis-boundary lands at ~1.57 w/s (-34%),
# which sits JUST INSIDE both a 35% relative tol AND a 1.5 floor -- a 1.5 floor would let it pass alone
# (REVIEW P2-1a). 1.7 catches it while staying well below the 2.21 honest minimum.
DEFAULT_ABS_LO = 1.7
DEFAULT_ABS_HI = 3.5
MIN_UNITS_FOR_MEDIAN = 5          # below this, the median is not trustworthy: absolute band only
SYSTEMATIC_FLAG_FRAC = 0.40       # > this fraction flagged => the whole map is likely shifted


def chapter_wps(alignable_words, audio_seconds):
    """words/sec for one unit; 0.0 if the duration is unusable (treated as an outlier, never a pass)."""
    return (alignable_words / audio_seconds) if audio_seconds and audio_seconds > 0 else 0.0


def _ascii(s):
    """cp1252-safe: a chapter title may carry non-ascii (curly quotes, an en-dash in 'Interlude - Flos');
    print it but never let a glyph crash the gate on a cp1252 pipe."""
    return str(s).encode("ascii", "replace").decode("ascii")


def screen(units, tol=DEFAULT_TOL, abs_lo=DEFAULT_ABS_LO, abs_hi=DEFAULT_ABS_HI):
    """Decide pass/fail over a list of unit dicts {label, wps, minutes, words}.

    Returns a dict:
      { median, mad, n, tol, abs_lo, abs_hi,
        flagged: [ {idx, label, wps, dev, minutes, words, cause, reason} ]  sorted by |dev| desc,
        systematic: bool, systematic_reason: str|None,
        too_few: bool,
        ok: bool }

    A unit is flagged when it is a RELATIVE outlier (|wps-median|/median > tol), an ABSOLUTE outlier
    (wps outside [abs_lo, abs_hi]), or flagged-by-ASSOCIATION (the adjacent low/high partner of a paired
    outlier -- mis-boundaries come in adjacent high/low pairs, and the slack member can sit just inside
    tol; REVIEW P2-1b). SYSTEMATIC failure if the median itself leaves the sane band or > 40% of units
    flag (a noisy median can hide relative outliers but cannot move the absolute band)."""
    # The thresholds are part of a HARD gate. A non-finite tol/abs_lo/abs_hi makes EVERY comparison
    # against it False, silently DISABLING relative or absolute detection -- and in the small-sample
    # path (too_few) the absolute band is the ONLY gate, so non-finite bounds let every short map
    # pass unscreened. A malformed threshold must fail loud, never quietly PASS units (Codex P1).
    tol = float(tol); abs_lo = float(abs_lo); abs_hi = float(abs_hi)
    if not (math.isfinite(tol) and tol >= 0):
        raise ValueError(f"wps tol must be a finite, non-negative number; got {tol!r}")
    if not (math.isfinite(abs_lo) and math.isfinite(abs_hi)):
        raise ValueError(f"wps absolute band must be finite; got abs_lo={abs_lo!r} abs_hi={abs_hi!r}")
    if abs_lo > abs_hi:
        raise ValueError(f"wps absolute band is inverted: abs_lo={abs_lo} > abs_hi={abs_hi}")
    n = len(units)
    wps = [float(u["wps"]) for u in units]
    # A non-finite wps (NaN/inf -- a zero/garbage duration or word count) must NEVER bypass a HARD gate:
    # every comparison against NaN is False, so it would slip through UNFLAGGED *and* corrupt the median
    # (statistics.median sorts, and NaN sorts unpredictably). Treat each as a hard failure and drop it
    # from the median/MAD so the band stays honest. chapter_wps() already maps an unusable duration to
    # 0.0 (an absolute outlier), but a wps computed upstream can still arrive NaN/inf -- catch it here.
    nonfinite = {i for i, w in enumerate(wps) if not math.isfinite(w)}
    finite = [w for i, w in enumerate(wps) if i not in nonfinite]
    too_few = len(finite) < MIN_UNITS_FOR_MEDIAN
    median = statistics.median(finite) if finite else 0.0
    mad = statistics.median([abs(w - median) for w in finite]) if finite else 0.0

    flag_idx = {}                 # idx -> reason string (first/strongest reason wins for the label)

    def mark(i, reason):
        if i not in flag_idx:
            flag_idx[i] = reason

    for i in nonfinite:
        mark(i, "non-finite")     # NaN/inf wps == unusable audio/text for this unit; hard fail, never a pass

    for i, w in enumerate(wps):
        if i in nonfinite:
            continue
        if not too_few and median > 0 and abs(w - median) / median > tol:
            mark(i, "relative")
        if w < abs_lo or w > abs_hi:
            mark(i, "absolute")

    # Flag-by-association: a mis-boundary is an adjacent high/low PAIR; if one member flags as a relative
    # or absolute outlier, flag its immediate neighbor on the opposite side of the median too, even if it
    # is sub-threshold (the slack side at -34% can squeak under both tests alone). REVIEW P2-1b.
    if not too_few and median > 0:
        for i in list(flag_idx):
            wi = wps[i]
            for j in (i - 1, i + 1):
                if 0 <= j < n and j not in flag_idx:
                    wj = wps[j]
                    # opposite sides of the median (one high, one low) = the pair signature
                    if (wi - median) * (wj - median) < 0:
                        mark(j, "association")

    flagged = []
    for i in sorted(flag_idx, key=lambda k: float("-inf") if not math.isfinite(wps[k])
                    else -abs(wps[k] - median)):            # non-finite sort first (most severe)
        u = units[i]
        w = wps[i]
        dev = float("nan") if not math.isfinite(w) else (((w - median) / median) if median > 0 else 0.0)
        cause = "squished" if (math.isfinite(w) and w >= median) else "slack"  # high=text>>audio, low=audio>>text
        flagged.append({"idx": i, "label": u["label"], "wps": w, "dev": dev,
                        "minutes": u.get("minutes"), "words": u.get("words"),
                        "cause": cause, "reason": flag_idx[i]})

    systematic = False
    systematic_reason = None
    if n:
        if not too_few and not (abs_lo <= median <= abs_hi):
            systematic = True
            systematic_reason = (f"median {median:.2f} w/s is outside the sane narration band "
                                 f"{abs_lo:.1f}..{abs_hi:.1f} -- the whole track map is likely shifted")
        elif not too_few and len(flag_idx) > SYSTEMATIC_FLAG_FRAC * n:
            systematic = True
            systematic_reason = (f"{len(flag_idx)}/{n} units flag (> {SYSTEMATIC_FLAG_FRAC:.0%}) -- "
                                 f"the median is not trustworthy; the whole track map is likely shifted")

    ok = (len(flag_idx) == 0) and not systematic
    return {"median": median, "mad": mad, "n": n, "tol": tol, "abs_lo": abs_lo, "abs_hi": abs_hi,
            "flagged": flagged, "systematic": systematic, "systematic_reason": systematic_reason,
            "too_few": too_few, "ok": ok}


def _pairs(flagged):
    """Adjacent flagged high/low pairs (idx, idx+1 both flagged, opposite cause) -- the actionable hint:
    a displaced boundary BETWEEN them. Returns list of (lo_entry, hi_entry)."""
    by_idx = {f["idx"]: f for f in flagged}
    out = []
    for f in flagged:
        nb = by_idx.get(f["idx"] + 1)
        if nb and f["cause"] != nb["cause"]:
            lo, hi = (f, nb) if f["cause"] == "slack" else (nb, f)
            out.append((lo, hi))
    return out


def report(result, header_units, tol):
    """Print the ASCII screen result. Returns True if it PASSES (no flags, not systematic)."""
    r = result
    pct = lambda x: f"{x*100:+.0f}%"
    print(f"=== wps pre-screen: {r['n']} units, median {r['median']:.2f} w/s, "
          f"MAD {r['mad']:.2f} (tol +-{tol*100:.0f}%) ===", flush=True)
    if r["too_few"]:
        print(f"NOTE: too few units ({r['n']} < {MIN_UNITS_FOR_MEDIAN}) for a median; "
              f"absolute band {r['abs_lo']:.1f}..{r['abs_hi']:.1f} w/s only", flush=True)
    for f in r["flagged"]:
        mins = f"{f['minutes']:.1f}min" if f["minutes"] is not None else "   ?min"
        wrds = f"{f['words']}w" if f["words"] is not None else "?w"
        hint = ("(text >> audio: boundary too late?)" if f["cause"] == "squished"
                else "(audio >> text: boundary too early?)")
        tag = "FLAG" if f["reason"] != "association" else "FLAG*"
        print(f"  [{f['idx']:02d}] {_ascii(f['label'])[:18]:18s} wps {f['wps']:5.2f}  "
              f"{pct(f['dev']):>5s}  {mins:>8s}  {wrds:>7s}  {tag} {f['cause']:8s} {hint}", flush=True)
    for lo, hi in _pairs(r["flagged"]):
        print(f"=== PAIR [{lo['idx']:02d}]<->[{hi['idx']:02d}]: adjacent slack/squished -- a displaced "
              f"boundary BETWEEN them is the classic signature. ===", flush=True)
    if r["systematic"]:
        print(f"=== WPS GATE FAIL: SYSTEMATIC -- {r['systematic_reason']}. ===", flush=True)
        return False
    if r["flagged"]:
        nflag = len(r["flagged"])
        assoc = " (* = flagged by adjacency to a paired outlier)" if any(
            f["reason"] == "association" for f in r["flagged"]) else ""
        print(f"=== WPS GATE FAIL: {nflag}/{r['n']} units deviate from median / leave the "
              f"{r['abs_lo']:.1f}..{r['abs_hi']:.1f} band (likely a misplaced boundary).{assoc} ===",
              flush=True)
        return False
    maxdev = max((abs((float(u['wps']) - r['median']) / r['median']) for u in header_units),
                 default=0.0) if r["median"] > 0 else 0.0
    print(f"=== WPS GATE PASS: all {r['n']} units within {tol*100:.0f}% of median / inside the "
          f"{r['abs_lo']:.1f}..{r['abs_hi']:.1f} band (max dev {maxdev*100:.0f}%). ===", flush=True)
    return True


# --------------------------------------------------------------------------------------------------
# Unit extraction (the thin, device-touching shell). Two input shapes, auto-detected by flags present.
# --------------------------------------------------------------------------------------------------

def _audio_by_no(audio_glob):
    by = {}
    for p in glob.glob(audio_glob):
        n = track_no(p)
        if n is not None:
            by[n] = p
    return by


def _sf_duration(path):
    import soundfile as sf
    return float(sf.info(path).duration)


def units_from_track_map(track_map, text, audio_glob):
    """Mode 1 (canonical) -- EXACT --auto-wps reproduction: per track-map entry, words from the SAME
    sentence slice the aligner aligns, seconds from the real audio. Same inputs as align_chapters.py."""
    tmap = load_track_map(track_map)
    with open(text, encoding="utf-8") as f:
        sents = [ln.strip() for ln in f if ln.strip()]
    by_no = _audio_by_no(audio_glob)
    for e in tmap:
        for t in e["tracks"]:
            if t not in by_no:
                sys.exit(f"--audio-glob matched no file for track {t:02d}")
    bounds = [e["seg"] for e in tmap] + [len(sents)]
    if bounds != sorted(bounds):
        sys.exit(f"track-map seg values are not ascending / exceed the text length: {bounds}")
    units = []
    for i, e in enumerate(tmap):
        s0, s1 = bounds[i], bounds[i + 1]
        words = n_alignable(sents[s0:s1])
        asec = sum(_sf_duration(by_no[t]) for t in e["tracks"])
        units.append({"label": e["title"], "wps": chapter_wps(words, asec),
                      "minutes": asec / 60.0, "words": words})
    return units


def _load_manifest_minutes(manifest_path):
    """track-number (int) -> minutes, from a manifest.json (the m4b duration source -- AAC is not
    soundfile-readable). Mirrors m4b_package.py's per-track 'minutes'."""
    with open(manifest_path, encoding="utf-8") as f:
        man = json.load(f)
    tracks = man.get("tracks", man) if isinstance(man, dict) else man
    out = {}
    for t in tracks:
        no = track_no(str(t.get("track", t.get("file", ""))))
        if no is not None and t.get("minutes") is not None:
            out[no] = float(t["minutes"])
    return out


def _load_units_minutes(units_path):
    """track-number (int) -> minutes, from a *_units.json ([{track, start, end, ...}]). The end-start
    duration is the same source m4b_package.py uses for an .m4a book."""
    with open(units_path, encoding="utf-8") as f:
        us = json.load(f)
    out = {}
    for u in us:
        no = track_no(str(u.get("track", "")))
        if no is not None and u.get("start") is not None and u.get("end") is not None:
            out[no] = (float(u["end"]) - float(u["start"])) / 60.0
    return out


def units_from_dir(align_dir, audio_glob=None, manifest=None, units_json=None):
    """Mode 2 (post-package) -- one alignNN.json/chapNN_*.json per unit. Words from segments[].text;
    seconds from the real audio (mp3/wav). For an .m4a book (AAC not soundfile-readable) fall back to
    manifest/units durations. A unit with NO usable duration is UNMEASURED -> FAILURE, never a silent
    pass (mirrors the IP guard's couldn't-measure-is-failure rule)."""
    files = sorted(glob.glob(os.path.join(align_dir, "align*.json")))
    if not files:
        files = sorted(glob.glob(os.path.join(align_dir, "chap*.json")))
    if not files:
        sys.exit(f"no align*.json / chap*.json in {align_dir}")
    by_no = _audio_by_no(audio_glob) if audio_glob else {}
    man_min = _load_manifest_minutes(manifest) if manifest else {}
    unit_min = _load_units_minutes(units_json) if units_json else {}

    units = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            doc = json.load(f)
        validate_doc(doc, source=fp)                      # consumer-side fail-loud, as the gate does
        # the per-track file's REAL track number: prefer its audio hint, else the filename index
        no = track_no(str(doc.get("audio", ""))) or track_no(fp)
        words = n_alignable([s.get("text", "") for s in doc["segments"]])
        label = doc.get("chapters", [{}])[0].get("title") or doc.get("title") or os.path.basename(fp)
        minutes = None
        if no in by_no:
            try:
                minutes = _sf_duration(by_no[no]) / 60.0
            except Exception:
                minutes = None
        if minutes is None and no in man_min:
            minutes = man_min[no]
        if minutes is None and no in unit_min:
            minutes = unit_min[no]
        if minutes is None or minutes <= 0:
            # UNMEASURED -> wps 0.0, which is below any abs_lo => an outlier => a FAILURE, never a pass.
            print(f"  WARN [{os.path.basename(fp)}] track {no}: NO usable audio duration "
                  f"(no soundfile audio, no manifest/units minutes) -- UNMEASURED, treated as FAIL",
                  flush=True)
            units.append({"label": label, "wps": 0.0, "minutes": None, "words": words})
            continue
        units.append({"label": label, "wps": chapter_wps(words, minutes * 60.0),
                      "minutes": minutes, "words": words})
    return units


def build_units(args):
    """Pick the input mode from the flags present. Mode 1 (track-map + text) is canonical; Mode 2 (dir)
    is the post-package convenience. A heavily-edited chapter is the one place they can disagree (web-text
    word count vs shorter edited audio raises Mode-2 wps); Mode 1 is the documented-canonical default."""
    if args.track_map and args.text:
        return units_from_track_map(args.track_map, args.text, args.audio_glob)
    if args.dir:
        return units_from_dir(args.dir, audio_glob=args.audio_glob,
                              manifest=args.manifest, units_json=args.units)
    sys.exit("need --track-map + --text (Mode 1, canonical) OR --dir (Mode 2). See --help.")


def add_args(ap):
    """Attach the wps flags to an argparse parser (so verify_tracks.py can mount the same screen). Skips
    any flag the host parser already defines (verify_tracks owns --dir/--audio-glob) so it can be mounted
    on top of an existing gate parser without an argparse conflict."""
    have = {s for act in ap._actions for s in act.option_strings}
    def add(flag, **kw):
        if flag not in have:
            ap.add_argument(flag, **kw)
    add("--track-map", help="Mode 1: JSON list of {title, seg, tracks[]} (exact auto-wps inputs)")
    add("--text", help="Mode 1: sentence-per-line prose for the whole book")
    add("--dir", help="Mode 2: per-track dir (alignNN.json) or per-chapter dir (chapNN_*.json)")
    add("--audio-glob", help="glob for the audiobook tracks (numbered NN); mp3/wav are soundfile-readable")
    add("--manifest", help="Mode 2 .m4a/.m4b: manifest.json carrying per-track 'minutes' (AAC is not soundfile-readable)")
    add("--units", help="Mode 2 .m4a/.m4b: a *_units.json ([{track,start,end}]) -- end-start durations")
    add("--wps-tol", type=float, default=DEFAULT_TOL, help="relative outlier tol (default 0.35 = +-35%%)")
    add("--wps-abs", type=float, nargs=2, metavar=("LO", "HI"),
        default=[DEFAULT_ABS_LO, DEFAULT_ABS_HI],
        help="absolute narration band w/s (default 1.7 3.5); catches a systematic shift the median hides")
    add("--allow-wps-outliers", action="store_true",
        help="DEMOTE wps failures to a printed WARNING and proceed (documented escape hatch for a GENUINE outlier)")
    return ap


def run(args):
    """Build units, screen, report. Returns (passed: bool, result: dict). Does NOT exit -- the caller
    (the gate) decides exit semantics (HARD), so the workbench can call the same screen advisory-only."""
    units = build_units(args)
    result = screen(units, tol=args.wps_tol, abs_lo=args.wps_abs[0], abs_hi=args.wps_abs[1])
    passed = report(result, units, args.wps_tol)
    return passed, result


def main():
    ap = argparse.ArgumentParser(description="NO-GPU words-per-second boundary pre-screen (the auto-wps tell).")
    add_args(ap)
    ap.add_argument("--strict", action="store_true",
                    help="exit nonzero on any flag (default: advisory -- print only). The gate sets this implicitly.")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        passed, _ = run(a)
    except ValueError as e:
        sys.exit(f"WPS SCREEN ABORT: {e}")     # a malformed threshold fails loud, never a silent pass
    if not passed and a.strict and not a.allow_wps_outliers:
        sys.exit("WPS SCREEN FAIL (--strict).")
    if not passed and a.allow_wps_outliers:
        print("=== --allow-wps-outliers: wps flags DEMOTED to warning; proceeding. ===", flush=True)


if __name__ == "__main__":
    main()
