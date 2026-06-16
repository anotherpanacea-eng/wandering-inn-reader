#!/usr/bin/env python3
"""
align.py — turn an aeneas sync map into the Inn Reader player's JSON.

The hard work (lining audio up to text) is done by a forced aligner. We do NOT
transcribe: the text already exists, so we align the known words to the audio and
read back timestamps. aeneas is the recommended aligner (see README) and emits a
"sync map" of text fragments with begin/end times. This script converts that sync
map into the schema index.html expects.

Schema produced:
  { "title": str, "audio": str,
    "chapters": [ {"title": str, "start": float, "seg": int}, ... ],  # optional
    "segments": [
      { "id": int, "start": float, "end": float, "text": str,
        "words": [ {"w": str, "s": float, "e": float}, ... ]   # optional
      }, ... ] }

Usage:
  python3 align.py --sync sync.json --title "Book 12 — Witch of Webs" --out align.json
  python3 align.py --sync sync.json --audio volume12.mp3 --title "..." --out align.json
  python3 align.py --sync sync.json --chapters book12.chapters.json --title "..." --out align.json

Aeneas sync-map input (default JSON format) looks like:
  {"fragments":[{"id":"f0001","lines":["A sentence."],"begin":"0.000","end":"4.2"}, ...]}

If your aligner produced word-level data instead, pass --words-json pointing at a
list of {"word"/"w", "start"/"s", "end"/"e"} and the script will pack words into
their containing sentences by time.
"""
import argparse, json, sys, re
from schema import validate_doc

def load(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def frag_text(fr):
    if "lines" in fr and fr["lines"]:
        return " ".join(s.strip() for s in fr["lines"] if s.strip())
    return (fr.get("text") or "").strip()

def to_segments(sync):
    frags = sync.get("fragments", sync if isinstance(sync, list) else [])
    segs = []
    for i, fr in enumerate(frags):
        txt = frag_text(fr)
        if not txt:
            continue
        try:
            start = float(fr["begin"]); end = float(fr["end"])
        except (KeyError, ValueError):
            continue
        segs.append({"id": len(segs), "start": round(start, 3),
                     "end": round(end, 3), "text": txt, "words": []})
    return segs

def attach_words(segs, words):
    """Distribute a flat word-timestamp list into segments by time overlap."""
    def gw(w, *keys):
        for k in keys:
            if k in w: return w[k]
        return None
    norm = []
    for w in words:
        tok = gw(w, "w", "word", "text")
        s = gw(w, "s", "start", "begin"); e = gw(w, "e", "end")
        if tok is None or s is None or e is None: continue
        norm.append({"w": str(tok).strip(), "s": float(s), "e": float(e)})
    norm.sort(key=lambda x: x["s"])
    si = 0
    for w in norm:
        while si < len(segs) and w["s"] >= segs[si]["end"] - 1e-6:
            si += 1
        if si >= len(segs): break
        if w["s"] >= segs[si]["start"] - 0.25:
            segs[si]["words"].append({"w": w["w"], "s": round(w["s"], 3), "e": round(w["e"], 3)})
    return segs

def attach_chapters(segs, markers):
    """Turn fetch_text.py chapter markers into player chapters keyed by segment.

    Each marker carries `seg` — the index of the chapter's first sentence among
    the non-blank lines, which is exactly the segment index here (both sides skip
    blanks in input order). A `seg` past the end means the sentence-count the
    fetcher saw and the fragment-count aeneas produced disagree — surface it
    loudly rather than silently dropping the chapter.
    """
    chapters, dropped = [], 0
    for m in markers:
        seg = m.get("seg", m.get("first_line"))   # tolerate the older key name
        title = (m.get("title") or "Chapter").strip()
        if seg is None or not (0 <= seg < len(segs)):
            print(f"  ! chapter {title!r}: seg index {seg} out of range "
                  f"(0..{len(segs) - 1}) — text/audio fragment counts disagree; dropping",
                  file=sys.stderr)
            dropped += 1
            continue
        chapters.append({"title": title, "start": segs[seg]["start"], "seg": seg})
    if dropped:
        print(f"  ! {dropped} chapter marker(s) dropped; check that the text fed to the "
              f"aligner matches the chapters file.", file=sys.stderr)
    return chapters

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", required=True, help="aeneas sync map JSON")
    ap.add_argument("--words-json", help="optional flat word-timestamp list")
    ap.add_argument("--chapters", help="fetch_text.py <out>.chapters.json for chapter markers")
    ap.add_argument("--title", default="Untitled")
    ap.add_argument("--audio", default="", help="audio filename hint stored in output")
    ap.add_argument("--out", default="align.json")
    a = ap.parse_args()

    segs = to_segments(load(a.sync))
    if not segs:
        sys.exit("No usable fragments found in the sync map.")
    if a.words_json:
        attach_words(segs, load(a.words_json))

    doc = {"title": a.title, "audio": a.audio, "segments": segs}
    chapters = attach_chapters(segs, load(a.chapters)) if a.chapters else []
    if chapters:
        doc["chapters"] = chapters

    validate_doc(doc, source=a.out)        # fail loud on a malformed envelope before writing
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    nwords = sum(len(s["words"]) for s in segs)
    dur = segs[-1]["end"]
    print(f"Wrote {a.out}: {len(segs)} sentences, {nwords} word-timings, "
          f"{len(chapters)} chapters, {dur/60:.1f} min of timeline.")

if __name__ == "__main__":
    main()
