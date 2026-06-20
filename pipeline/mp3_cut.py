#!/usr/bin/env python3
"""
mp3_cut.py --audio-glob "...*.mp3" --boundaries boundaries.json --align-out DIR --play-out DIR

Cut per-chapter audio from a CONTINUOUS multi-mp3 timeline at the discovered chapter boundaries
(find_chapter_boundaries.py output). This is the mp3 analogue of m4b_cut.py: where that one cuts a
single AAC file by -ss/-t, this stitches a span that may CROSS track edges. The tracks are uniform
time-slices that straddle chapters, so chapter span [start_i, start_{i+1}) often covers the tail of
one mp3 plus the head of the next. We build an ffmpeg `ffconcat` list per chapter (file + inpoint/
outpoint per track portion) so one ffmpeg call stitches the cross-track span. Two outputs from the
SAME boundaries:
    align-out/NN.wav : 16 kHz mono PCM  (for align_chapters.py + verify_tracks.py)
    play-out/NN.mp3  : stream-copy of the original mp3 frames (fast, lossless; ~26 ms frame snap)
Resumable, but NOT blindly: an existing output is reused only if it is newer than BOTH the boundaries
JSON and every source track. Change a boundary (or swap an mp3) and the now-stale cut is remade, not
silently reused -- a present file alone is not "done". --force re-cuts all.

ffmpeg resolves via m4b_common (FFMPEG env or PATH; Shotcut bundles it on Windows), not a hard path.
"""
import argparse, glob, json, os, re, subprocess, sys
from m4b_common import FFMPEG, require


def track_no(p):
    m = re.search(r"(\d+)", os.path.basename(p))
    return int(m.group(1)) if m else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-glob", required=True)
    ap.add_argument("--boundaries", required=True, help="find_chapter_boundaries.py output JSON")
    ap.add_argument("--align-out", required=True, help="dir for per-chapter 16k mono NN.wav")
    ap.add_argument("--play-out", required=True, help="dir for per-chapter stream-copy NN.mp3")
    ap.add_argument("--ffmpeg", default=FFMPEG)
    ap.add_argument("--force", action="store_true", help="re-cut every output, ignoring up-to-date checks")
    a = ap.parse_args()
    ffmpeg = require(a.ffmpeg, "FFMPEG")

    import soundfile as sf
    paths = sorted(glob.glob(a.audio_glob), key=track_no)
    if not paths:
        sys.exit("no audio matched --audio-glob")
    tracks, g = [], 0.0
    for p in paths:
        d = sf.info(p).duration
        tracks.append({"path": os.path.abspath(p), "g0": g, "g1": g + d}); g += d
    total = g

    # an output is stale if the boundaries JSON or any source track is newer than it (re-cut, don't reuse)
    inputs_mtime = max([os.path.getmtime(a.boundaries)] + [os.path.getmtime(p) for p in paths])

    data = json.load(open(a.boundaries, encoding="utf-8"))
    chaps = data["chapters"]
    starts = [c["start"] for c in chaps]
    bounds = starts + [total]
    os.makedirs(a.align_out, exist_ok=True); os.makedirs(a.play_out, exist_ok=True)

    for i, c in enumerate(chaps):
        n = i + 1
        gs, ge = bounds[i], bounds[i + 1]
        # build concat entries (track portions covering [gs,ge))
        entries = []
        for t in tracks:
            if t["g1"] <= gs or t["g0"] >= ge:
                continue
            ls = max(gs, t["g0"]) - t["g0"]
            le = min(ge, t["g1"]) - t["g0"]
            entries.append((t["path"], ls, le))
        if not entries:
            sys.exit(f"chapter {n} ({c['title']}) covers no audio?! span {gs:.1f}-{ge:.1f}")
        listfile = os.path.join(a.align_out, f"_concat_{n:02d}.txt")
        with open(listfile, "w", encoding="utf-8") as lf:
            lf.write("ffconcat version 1.0\n")
            for path, ls, le in entries:
                p2 = path.replace("\\", "/").replace("'", r"'\''")
                lf.write(f"file '{p2}'\ninpoint {ls:.3f}\noutpoint {le:.3f}\n")
        cross = "  (CROSS-TRACK x%d)" % len(entries) if len(entries) > 1 else ""
        print(f"  [{n:02d}] {c['title']:34s} {gs/60:7.1f}-{ge/60:7.1f}min ({(ge-gs)/60:5.1f}min){cross}",
              flush=True)
        wav = os.path.join(a.align_out, f"{n:02d}.wav")
        mp3 = os.path.join(a.play_out, f"{n:02d}.mp3")

        def fresh(f):
            return (not a.force and os.path.exists(f) and os.path.getsize(f) > 1000
                    and os.path.getmtime(f) >= inputs_mtime)

        if not fresh(wav):
            rc = subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
                                 "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav]).returncode
            if rc != 0:
                sys.exit(f"ffmpeg wav failed on {n}")
        if not fresh(mp3):
            # stream-copy the original mp3 frames (fast, lossless, keeps source quality); ~26ms frame snap
            rc = subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
                                 "-c", "copy", mp3]).returncode
            if rc != 0:
                sys.exit(f"ffmpeg mp3 failed on {n}")
        os.remove(listfile)
    print(f"\nDONE mp3_cut.py -> {a.align_out} (wav) + {a.play_out} (mp3)", flush=True)


if __name__ == "__main__":
    main()
