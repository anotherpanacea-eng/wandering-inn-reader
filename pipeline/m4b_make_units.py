#!/usr/bin/env python3
"""
m4b_make_units.py --m4b X.m4b --chapters book.chapters.json --starts "1,2,4,..." --end IDX
                  --out-units book_units.json --out-trackmap book_track_map.json

Turn the m4b mark times (from ffprobe) plus a hand-verified "which RAW mark index each web chapter
STARTS at" list (read off probe_m4b.py) into the two JSONs the rest of the flow needs:
    units    : [{track,title,seg,start,end}]  (seconds)  -> m4b_cut.py, m4b_package.py
    trackmap : [{title,seg,tracks:[track]}]              -> align_chapters.py --track-map

--starts has one 0-based mark index per web chapter, SAME count and order as book.chapters.json (a web
chapter that spans several marks just lists the index of its FIRST mark). --end is the mark index where
the last web chapter ends (usually the end-credits mark). Each web chapter becomes one numbered track =
the audio span [mark[starts[i]], mark[starts[i+1]]). seg comes straight from book.chapters.json, so the
text slices align_chapters.py cuts match the chapter boundaries fetch_text.py recorded.
"""
import argparse, json, sys
from m4b_common import FFPROBE, ffprobe_chapters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--m4b", required=True)
    ap.add_argument("--chapters", required=True)
    ap.add_argument("--starts", required=True, help="comma-separated 0-based mark index per web chapter")
    ap.add_argument("--end", type=int, required=True, help="0-based mark index where the last web chapter ends")
    ap.add_argument("--out-units", required=True)
    ap.add_argument("--out-trackmap", required=True)
    ap.add_argument("--ffprobe", default=FFPROBE)
    a = ap.parse_args()

    marks = [s for s, _ in ffprobe_chapters(a.m4b, a.ffprobe)]
    web = json.load(open(a.chapters, encoding="utf-8"))
    starts = [int(x) for x in a.starts.split(",")]
    if len(starts) != len(web):
        sys.exit(f"--starts has {len(starts)} indices but {a.chapters} has {len(web)} web chapters")
    bounds = starts + [a.end]
    if bounds != sorted(bounds):
        sys.exit(f"mark indices must be ascending, got {bounds}")
    if not (0 <= bounds[0] and a.end <= len(marks) - 1):
        sys.exit(f"mark indices out of range 0..{len(marks)-1}: {bounds}")

    units, tmap = [], []
    for i, w in enumerate(web):
        track = i + 1
        s, e = marks[bounds[i]], marks[bounds[i + 1]]
        if e <= s:
            sys.exit(f"chapter {track} ({w['title']}) has non-positive duration (marks {bounds[i]}->{bounds[i+1]})")
        units.append({"track": track, "title": w["title"], "seg": w["seg"],
                      "start": round(s, 3), "end": round(e, 3)})
        tmap.append({"title": w["title"], "seg": w["seg"], "tracks": [track]})
        print(f"  [{track:02d}] marks {bounds[i]:>2}->{bounds[i+1]:>2}  {s/60:7.1f}-{e/60:7.1f}min "
              f"({(e-s)/60:5.1f}min)  seg {w['seg']:>6}  {w['title']}", flush=True)
    json.dump(units, open(a.out_units, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(tmap, open(a.out_trackmap, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nwrote {a.out_units} + {a.out_trackmap} ({len(units)} web chapters)", flush=True)


if __name__ == "__main__":
    main()
