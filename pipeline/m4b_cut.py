#!/usr/bin/env python3
"""
m4b_cut.py --m4b X.m4b --units units.json --outdir DIR --ext {wav|m4a}

Cut an .m4b into one file per WEB chapter, driven by a units JSON (list of {track,title,start,end}
in seconds, as produced by m4b_make_units.py). Each web chapter is one contiguous [start,end) span --
which may cover more than one audiobook mark when the producer split a long chapter. Two outputs from
the SAME boundaries:
    --ext wav : 16 kHz mono PCM for the aligner (align_chapters.py) and ASR verify (verify_tracks.py),
                because libsndfile can't read AAC -- decode up front.
    --ext m4a : lossless stream-copy for phone playback (cuts snap to AAC frames, ~23 ms).
Resumable: skips an output that already exists and is non-trivial.
"""
import argparse, json, os, subprocess, sys
from m4b_common import FFMPEG, require


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--m4b", required=True)
    ap.add_argument("--units", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--ext", choices=["wav", "m4a"], required=True)
    ap.add_argument("--ffmpeg", default=FFMPEG)
    a = ap.parse_args()
    ffmpeg = require(a.ffmpeg, "FFMPEG")

    units = json.load(open(a.units, encoding="utf-8"))
    os.makedirs(a.outdir, exist_ok=True)
    for u in units:
        n, title, start, end = u["track"], u["title"], float(u["start"]), float(u["end"])
        dur = end - start
        out = os.path.join(a.outdir, f"{n:02d}.{a.ext}")
        if os.path.exists(out) and os.path.getsize(out) > 1000:
            print(f"  [{n:02d}] SKIP {title} (exists)", flush=True); continue
        print(f"  [{n:02d}] {title:28s} {start/60:7.1f}-{end/60:7.1f}min ({dur/60:5.1f}min) -> {n:02d}.{a.ext}",
              flush=True)
        if a.ext == "wav":
            tail = ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
        else:
            tail = ["-map", "0:a", "-c", "copy", "-avoid_negative_ts", "make_zero"]
        cmd = [ffmpeg, "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", a.m4b, "-t", f"{dur:.3f}"] + tail + [out]
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            sys.exit(f"ffmpeg failed on unit {n} ({title}) rc={rc}")
    print(f"DONE m4b_cut.py ({a.ext})", flush=True)


if __name__ == "__main__":
    main()
