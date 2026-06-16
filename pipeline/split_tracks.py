#!/usr/bin/env python3
"""
split_tracks.py — cut one continuous book sync (book12_full.json, timeline spanning
all tracks) into per-track player files, each with timestamps LOCAL to its own audio
file so it pairs with the existing mp3 the user already has. No re-encoding, no concat.

A segment/chapter is assigned to the track its START falls in; word/segment times are
shifted by that track's global offset and clamped to the track length. Output names
come from the track's numeric prefix: "02 - ....mp3" -> align02.json.
"""
import argparse, json, os, re, sys
from schema import validate_doc

def track_no(path):
    m = re.match(r"\s*(\d+)", os.path.basename(path))
    return m.group(1) if m else os.path.splitext(os.path.basename(path))[0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", required=True, help="continuous book JSON")
    ap.add_argument("--audio", nargs="+", required=True, help="SAME ordered track list used to align")
    ap.add_argument("--outdir", default="per_track")
    ap.add_argument("--title", default="The Witch of Webs")
    a = ap.parse_args()

    import soundfile as sf
    # cumulative global start time of each track (resampled duration == native duration)
    starts, t = [], 0.0
    for f in a.audio:
        starts.append(t)
        t += sf.info(f).duration
    ends = starts[1:] + [t]

    doc = json.load(open(a.full, encoding="utf-8"))
    segs, chaps = doc["segments"], doc.get("chapters", [])
    os.makedirs(a.outdir, exist_ok=True)

    manifest, content_idx = [], []
    for i, audio in enumerate(a.audio):
        t0, t1 = starts[i], ends[i]
        dur = t1 - t0
        # segments whose start falls in this track
        tsegs = [s for s in segs if t0 <= s["start"] < t1]
        if not tsegs:
            continue
        content_idx.append(i)
        gid0 = tsegs[0]["id"]
        local = []
        for s in tsegs:
            words = [{"w": w["w"],
                      "s": round(min(max(w["s"] - t0, 0.0), dur), 3),
                      "e": round(min(max(w["e"] - t0, 0.0), dur), 3)} for w in s["words"]]
            local.append({"id": s["id"] - gid0,
                          "start": round(min(max(s["start"] - t0, 0.0), dur), 3),
                          "end": round(min(max(s["end"] - t0, 0.0), dur), 3),
                          "text": s["text"], "words": words})
        # chapters starting in this track, reindexed to local segment ids
        gid_to_local = {s["id"]: s["id"] - gid0 for s in tsegs}
        tchaps = []
        for c in chaps:
            if t0 <= c["start"] < t1:
                tchaps.append({"title": c["title"],
                               "start": round(c["start"] - t0, 3),
                               "seg": gid_to_local.get(c["seg"], 0)})
        no = track_no(audio)
        out = {"title": f"{a.title} — track {no}",
               "audio": os.path.basename(audio), "segments": local}
        if tchaps:
            out["chapters"] = tchaps
        validate_doc(out, source=f"align{no}.json")     # consumer-side fail-loud on a bad envelope
        outpath = os.path.join(a.outdir, f"align{no}.json")
        json.dump(out, open(outpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        manifest.append({"track": no, "file": os.path.basename(outpath),
                         "audio": os.path.basename(audio),
                         "minutes": round(dur / 60, 1), "sentences": len(local),
                         "chapters": [{"title": c["title"], "start": c["start"]} for c in tchaps]})
        print(f"  align{no}.json  {dur/60:5.1f}min  {len(local):4d} seg  "
              f"chapters: {', '.join(c['title'] for c in tchaps) or '-'}")

    json.dump({"book": a.title, "tracks": manifest},
              open(os.path.join(a.outdir, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\nWrote {len(manifest)} per-track files + manifest.json to {a.outdir}/")

if __name__ == "__main__":
    main()
