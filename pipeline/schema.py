#!/usr/bin/env python3
"""
schema.py — the SINGLE source of truth for the Inn Reader player/aligner JSON contract.

Every producer (align.py, align_torch.py, align_book.py) validates its output here before
writing, and the consumer-side splitter (split_tracks.py) validates every per-track file it
emits. The player (index.html) carries a small JS mirror of the same checks at load time.
That way a malformed sync file fails LOUD at the boundary instead of silently mis-rendering
(belt-and-suspenders: validate at producer AND consumer; one contract, all parallel paths).

Contract:
  { "title": str (non-empty),
    "audio": str,                                   # filename hint
    "chapters": [ {"title": str, "start": float>=0, "seg": int in 0..len(segments)-1} ]?,  # optional
    "segments": [ {"id": int, "start": float>=0, "end": float>=start, "text": str,
                   "words": [ {"w": str, "s": float>=0, "e": float>=s} ]? } ]   # words optional
  }
"""
import math


class SchemaError(ValueError):
    """Raised by validate_doc(strict=True) when the doc has any ERROR-level problem."""

def _num(x):                                    # reject NaN/Infinity -- json.dump emits them and the
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)  # browser JSON.parse chokes

def validate_doc(doc, source="", *, strict=True):
    """Validate a player doc. Returns a list of (level, message) tuples ("ERROR"/"WARN").
    If strict and any ERROR exists, raises SchemaError. WARN-level issues (e.g. non-monotonic
    segment starts) are reported but never raise — they degrade gracefully in the player."""
    errs, warns = [], []
    tag = f"{source}: " if source else ""

    if not isinstance(doc, dict):
        raise SchemaError(f"{tag}top-level JSON is not an object")

    if not isinstance(doc.get("title"), str) or not doc["title"].strip():
        errs.append("title missing or empty")
    if not isinstance(doc.get("audio"), str):
        errs.append("audio missing (expected a filename-hint string)")

    segs = doc.get("segments")
    if not isinstance(segs, list) or not segs:
        errs.append("segments missing or empty")
        segs = []
    last_start = -1.0
    for i, s in enumerate(segs):
        if not isinstance(s, dict):
            errs.append(f"segment[{i}] is not an object"); continue
        st, en = s.get("start"), s.get("end")
        if not _num(st) or st < 0:
            errs.append(f"segment[{i}].start invalid ({st!r})")
        if not _num(en):
            errs.append(f"segment[{i}].end invalid ({en!r})")
        if _num(st) and _num(en) and en + 1e-6 < st:
            errs.append(f"segment[{i}] end {en} < start {st}")
        if not isinstance(s.get("text"), str):
            errs.append(f"segment[{i}].text is not a string")
        if _num(st):
            if st + 1e-6 < last_start:
                warns.append(f"segment[{i}].start {st} < previous {last_start} (non-monotonic)")
            last_start = max(last_start, st)
        ws = s.get("words")
        if ws is not None and not isinstance(ws, list):
            errs.append(f"segment[{i}].words is not a list")
        elif isinstance(ws, list):
            for j, w in enumerate(ws):
                if not isinstance(w, dict) or not isinstance(w.get("w"), str):
                    errs.append(f"segment[{i}].words[{j}].w invalid"); continue
                ss, ee = w.get("s"), w.get("e")
                if not _num(ss) or ss < 0:
                    errs.append(f"segment[{i}].words[{j}].s invalid ({ss!r})")
                if not _num(ee):
                    errs.append(f"segment[{i}].words[{j}].e invalid ({ee!r})")
                if _num(ss) and _num(ee) and ee + 1e-6 < ss:
                    errs.append(f"segment[{i}].words[{j}] e {ee} < s {ss}")

    chs = doc.get("chapters")
    if chs is not None:
        if not isinstance(chs, list):
            errs.append("chapters is not a list")
        else:
            nseg = len(segs)
            for i, c in enumerate(chs):
                if not isinstance(c, dict):
                    errs.append(f"chapter[{i}] is not an object"); continue
                if not isinstance(c.get("title"), str):
                    errs.append(f"chapter[{i}].title is not a string")
                if not _num(c.get("start")) or c["start"] < 0:
                    errs.append(f"chapter[{i}].start invalid ({c.get('start')!r})")
                sg = c.get("seg")
                if sg is not None and (not isinstance(sg, int) or isinstance(sg, bool)
                                       or not (0 <= sg < nseg)):
                    errs.append(f"chapter[{i}].seg {sg!r} out of range 0..{nseg - 1}")

    if strict and errs:
        raise SchemaError(f"{tag}{len(errs)} schema error(s):\n  - " + "\n  - ".join(errs))
    return [("ERROR", m) for m in errs] + [("WARN", m) for m in warns]
