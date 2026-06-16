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
    # cumulative global start time + real duration of each track (resampled dur == native dur)
    durs = [sf.info(f).duration for f in a.audio]
    starts, t = [], 0.0
    for d in durs:
        starts.append(t); t += d
    # assignment upper bounds: the LAST track's bound is +inf so trailing segments / a final-window
    # overshoot land in it instead of being silently dropped. (Clamping below still uses the REAL durs.)
    bounds = starts[1:] + [float("inf")]

    with open(a.full, encoding="utf-8") as _ff:
        doc = json.load(_ff)
    segs, chaps = doc["segments"], doc.get("chapters", [])
    os.makedirs(a.outdir, exist_ok=True)

    manifest, content_idx, n_assigned = [], [], 0
    for i, audio in enumerate(a.audio):
        t0, t1, dur = starts[i], bounds[i], durs[i]   # dur = REAL track length (for clamping)
        # segments whose start falls in this track
        tsegs = [s for s in segs if t0 <= s["start"] < t1]
        if not tsegs:
            continue
        content_idx.append(i)
        n_assigned += len(tsegs)
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
                local_start = round(c["start"] - t0, 3)
                if c["seg"] in gid_to_local:                # chapter's seg is in this track
                    seg_local = gid_to_local[c["seg"]]
                elif local:                                 # seg landed elsewhere -> nearest local seg by time
                    seg_local = min(range(len(local)), key=lambda j: abs(local[j]["start"] - local_start))
                else:
                    seg_local = 0
                tchaps.append({"title": c["title"], "start": local_start, "seg": seg_local})
        no = track_no(audio)
        out = {"title": f"{a.title} — track {no}",
               "audio": os.path.basename(audio), "segments": local}
        if tchaps:
            out["chapters"] = tchaps
        validate_doc(out, source=f"align{no}.json")     # consumer-side fail-loud on a bad envelope
        outpath = os.path.join(a.outdir, f"align{no}.json")
        with open(outpath, "w", encoding="utf-8") as _of:
            json.dump(out, _of, ensure_ascii=False, indent=2)
        manifest.append({"track": no, "file": os.path.basename(outpath),
                         "audio": os.path.basename(audio),
                         "minutes": round(dur / 60, 1), "sentences": len(local),
                         "chapters": [{"title": c["title"], "start": c["start"]} for c in tchaps]})
        print(f"  align{no}.json  {dur/60:5.1f}min  {len(local):4d} seg  "
              f"chapters: {', '.join(c['title'] for c in tchaps) or '-'}")

    with open(os.path.join(a.outdir, "manifest.json"), "w", encoding="utf-8") as _mf:
        json.dump({"book": a.title, "tracks": manifest}, _mf, ensure_ascii=False, indent=2)
    print(f"\nWrote {len(manifest)} per-track files + manifest.json to {a.outdir}/")
    # reconciliation: every input segment must land in exactly one track -- guard against silent loss
    if n_assigned != len(segs):
        sys.exit(f"FAIL: {len(segs) - n_assigned} of {len(segs)} segments were not assigned to any track "
                 f"(start times outside the audio timeline?) -- they'd be MISSING from the per-track files.")

if __name__ == "__main__":
    main()
