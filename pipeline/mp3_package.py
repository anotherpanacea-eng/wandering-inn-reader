#!/usr/bin/env python3
"""
mp3_package.py --units book_units.json --chapters book.chapters.json --per-chapter DIR --out DIR
               --book-title "Lady of Fire"

Turn the per-chapter alignments (align_chapters.py output: chapNN_*.json) into the player's per-track
files for a multi-track-mp3 (straddling-track) book. This is the mp3 analogue of m4b_package.py, with
ONE difference: a single alignment UNIT may hold more than one web-chapter marker (e.g. a merged 2-part
interlude is one audio unit but two web chapters). We assign each web chapter (book.chapters.json, global
segment indices) to the unit whose segment range contains it, and place its marker at the ALIGNED segment
time inside that unit -- so a second marker lands accurately even though both share one audio unit.
Output: alignNN.json (audio = NN.mp3) + manifest.json. Per-track durations come from the units (end-start).

The per-chapter file for unit index i is matched by its parsed index (chap{i:02d}_*.json), then validated
through the shared player schema before writing.
"""
import argparse, glob, json, os, sys
from schema import validate_doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True)
    ap.add_argument("--chapters", required=True, help="book.chapters.json (all web chapters, global seg)")
    ap.add_argument("--per-chapter", required=True, help="dir of align_chapters.py output (chapNN_*.json)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--book-title", required=True)
    a = ap.parse_args()

    units = json.load(open(a.units, encoding="utf-8"))
    web = json.load(open(a.chapters, encoding="utf-8"))
    unit_segs = [u["seg"] for u in units]
    # assign each web chapter to the unit whose [seg, next_unit_seg) contains its global seg
    per_unit = {i: [] for i in range(len(units))}
    for w in web:
        ui = max(i for i in range(len(units)) if unit_segs[i] <= w["seg"])
        per_unit[ui].append(w)

    os.makedirs(a.out, exist_ok=True)
    manifest = []
    for i, u in enumerate(units):
        tno = u["track"]
        dur = float(u["end"]) - float(u["start"])
        files = sorted(glob.glob(os.path.join(a.per_chapter, f"chap{i:02d}_*.json")))
        if not files:
            sys.exit(f"no per-chapter JSON for unit {i:02d} ({u['title']}) in {a.per_chapter}")
        doc = json.load(open(files[0], encoding="utf-8"))
        segs = doc["segments"]
        markers = []
        for w in per_unit[i]:
            rel = w["seg"] - u["seg"]                       # index within this unit's text
            rel = max(0, min(rel, len(segs) - 1))
            markers.append({"title": w["title"], "start": round(segs[rel]["start"], 3), "seg": rel})
        if not markers:                                     # safety: always at least the unit's own marker
            markers = [{"title": u["title"], "start": round(segs[0]["start"], 3), "seg": 0}]
        markers.sort(key=lambda m: m["seg"])
        audio = f"{tno:02d}.mp3"
        out = {"title": f"{a.book_title} — {u['title']}", "audio": audio,
               "segments": segs, "chapters": markers}
        validate_doc(out, source=f"align{tno:02d}.json")
        json.dump(out, open(os.path.join(a.out, f"align{tno:02d}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        manifest.append({"track": f"{tno:02d}", "file": f"align{tno:02d}.json", "audio": audio,
                         "minutes": round(dur / 60, 1), "sentences": len(segs),
                         "chapters": [{"title": m["title"], "start": m["start"]} for m in markers]})
        mk = " + ".join(m["title"] for m in markers)
        print(f"  align{tno:02d}.json  {dur/60:6.1f}min  {len(segs):4d} seg  [{mk}]", flush=True)
    json.dump({"book": a.book_title, "tracks": manifest},
              open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nwrote {len(manifest)} per-track files + manifest.json to {a.out}", flush=True)


if __name__ == "__main__":
    main()
