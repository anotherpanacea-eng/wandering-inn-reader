#!/usr/bin/env python3
"""
m4b_package.py --units book_units.json --per-chapter DIR --out DIR --book-title "Hell's Wardens"

Turn the per-chapter alignments (align_chapters.py output: chapNN_*.json) into the player's per-track
files for an .m4b book. Each web chapter is one audio unit, so each per-chapter JSON is ALREADY a
0-based per-track file -- no recombine/split needed (that's the mp3-multitrack flow). We point the audio
hint at the .m4a, add the single chapter marker, validate against the player schema, and build the
manifest. Per-track durations come from the units (end-start), since libsndfile can't read the .m4a.

The chapNN file for web-chapter index i is matched by its parsed index (chap{i:02d}_*.json), NOT sort
order, so an interlude title sorting oddly can't misalign a track. Then ship the OUT dir's alignNN.json
+ manifest.json next to m4b_cut.py's NN.m4a into the Dropbox app folder.
"""
import argparse, glob, json, os, sys
from schema import validate_doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True)
    ap.add_argument("--per-chapter", required=True, help="dir of align_chapters.py output (chapNN_*.json)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--book-title", required=True)
    a = ap.parse_args()

    units = json.load(open(a.units, encoding="utf-8"))
    os.makedirs(a.out, exist_ok=True)
    manifest = []
    for i, u in enumerate(units):
        tno, title = u["track"], u["title"]
        dur = float(u["end"]) - float(u["start"])
        matches = sorted(glob.glob(os.path.join(a.per_chapter, f"chap{i:02d}_*.json")))
        if not matches:
            sys.exit(f"no per-chapter JSON for chapter index {i:02d} ({title}) in {a.per_chapter}")
        with open(matches[0], encoding="utf-8") as fh:
            doc = json.load(fh)
        segs = doc["segments"]
        audio = f"{tno:02d}.m4a"
        start0 = round(segs[0]["start"], 3)
        out = {"title": f"{a.book_title} — {title}", "audio": audio, "segments": segs,
               "chapters": [{"title": title, "start": start0, "seg": 0}]}
        validate_doc(out, source=f"align{tno:02d}.json")
        with open(os.path.join(a.out, f"align{tno:02d}.json"), "w", encoding="utf-8") as of:
            json.dump(out, of, ensure_ascii=False, indent=2)
        manifest.append({"track": f"{tno:02d}", "file": f"align{tno:02d}.json", "audio": audio,
                         "minutes": round(dur / 60, 1), "sentences": len(segs),
                         "chapters": [{"title": title, "start": start0}]})
        print(f"  align{tno:02d}.json  {dur/60:6.1f}min  {len(segs):4d} seg  {title}", flush=True)
    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as mf:
        json.dump({"book": a.book_title, "tracks": manifest}, mf, ensure_ascii=False, indent=2)
    print(f"\nwrote {len(manifest)} per-track files + manifest.json to {a.out}", flush=True)


if __name__ == "__main__":
    main()
