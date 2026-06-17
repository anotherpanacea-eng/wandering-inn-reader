#!/usr/bin/env python3
"""
recombine_chapters.py -- stitch the per-chapter anchored alignments (each with a timeline LOCAL to
its own track group, starting at 0) into ONE continuous book JSON spanning all tracks in order.

Each chapter aligned independently against its own audio tracks (see align_chapters.py), so the
forced-alignment lag resets to zero at every chapter boundary. To reassemble the book, every
chapter's local times are shifted by that chapter's GLOBAL track offset = cumulative soundfile
duration of all earlier tracks -- the SAME offsets split_tracks.py computes, so a recombined
segment lands back in its own track when split. Segment ids are renumbered globally; chapter
markers carry the global start + global first-seg id.

Chapter<->track correspondence comes from the shared track map (--track-map; see book12_track_map.json).
Per-chapter files are paired to map entries by the zero-padded index in their name (chap07_*.json ->
entry 7), validated to cover every entry exactly once -- NOT by sort position (which would silently
misalign every offset if a chapter were empty/missing).

Output validates against schema.validate_doc before writing. Example:
    py -3.12 recombine_chapters.py --audio-glob "...\\*.mp3" --chapters-dir per_chapter \\
        --out book12_anchored.json --title "The Witch of Webs"
"""
import argparse, glob, json, os, re, sys

from schema import validate_doc

HERE = os.path.dirname(os.path.abspath(__file__))


def track_no(path):
    m = re.search(r"(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else None


def load_track_map(path):
    with open(path, encoding="utf-8") as f:
        tm = json.load(f)
    if not isinstance(tm, list) or not tm:
        sys.exit(f"track map {path} is not a non-empty list")
    for i, e in enumerate(tm):
        if not (isinstance(e, dict) and isinstance(e.get("tracks"), list) and e["tracks"]
                and isinstance(e.get("seg"), int)):
            sys.exit(f"track map entry {i} malformed (need title, int seg, non-empty tracks): {e!r}")
    return tm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--track-map", default=os.path.join(HERE, "book12_track_map.json"),
                    help="JSON list of {title, seg, tracks[]} (one entry per chapter, in book order)")
    ap.add_argument("--audio-glob", required=True, help="glob for the SAME ordered audio tracks used to align")
    ap.add_argument("--chapters-dir", required=True, help="dir of per-chapter JSONs (chapNN_*.json)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="The Witch of Webs")
    a = ap.parse_args()

    import soundfile as sf

    tmap = load_track_map(a.track_map)
    by_no = {}
    for p in glob.glob(a.audio_glob):
        n = track_no(p)
        if n is not None:
            by_no[n] = p
    ordered = [t for e in tmap for t in e["tracks"]]
    for t in ordered:
        if t not in by_no:
            sys.exit(f"--audio-glob matched no file for track {t:02d}")

    # global start (cumulative duration) of every track over the full ordered list
    gstart, cum = {}, 0.0
    for t in ordered:
        gstart[t] = cum
        cum += sf.info(by_no[t]).duration
    print(f"full timeline: {len(ordered)} tracks, {cum/60:.1f} min total", flush=True)

    # pair per-chapter files to map entries by the index in their name -- explicit, not by sort order
    idx_file = {}
    for p in glob.glob(os.path.join(a.chapters_dir, "chap*.json")):
        m = re.search(r"chap(\d+)", os.path.basename(p))
        if not m:
            continue
        i = int(m.group(1))
        if i in idx_file:
            sys.exit(f"two per-chapter files share index {i}: {idx_file[i]} and {p}")
        idx_file[i] = p
    missing = [i for i in range(len(tmap)) if i not in idx_file]
    if missing:
        sys.exit(f"missing per-chapter files for map indices {missing} (expected chapNN_*.json for 0..{len(tmap)-1})")
    extra = [i for i in idx_file if i >= len(tmap)]
    if extra:
        sys.exit(f"per-chapter files index out of map range: {extra} (map has {len(tmap)} entries)")

    segments, chapters, gid = [], [], 0
    for i in range(len(tmap)):
        entry = tmap[i]
        with open(idx_file[i], encoding="utf-8") as f:
            doc = json.load(f)
        off = gstart[entry["tracks"][0]]                  # global offset = first track's cumulative start
        grp_dur = sum(sf.info(by_no[t]).duration for t in entry["tracks"])
        segs = doc.get("segments") or []
        if not segs:
            sys.exit(f"{os.path.basename(idx_file[i])} has NO segments -- aborting (would corrupt offsets)")
        local_end = max(s["end"] for s in segs)
        if local_end > grp_dur + 5:                       # local timeline must fit its own track group
            sys.exit(f"{os.path.basename(idx_file[i])}: local end {local_end/60:.1f}min exceeds its track "
                     f"group {grp_dur/60:.1f}min -- a track likely straddles a chapter boundary (bad map).")
        title = entry.get("title") or doc.get("title") or f"Chapter {i+1}"
        first_gid = gid
        for s in segs:
            words = [{"w": w["w"], "s": round(w["s"] + off, 3), "e": round(w["e"] + off, 3)}
                     for w in s.get("words", [])]
            segments.append({"id": gid, "start": round(s["start"] + off, 3),
                             "end": round(s["end"] + off, 3), "text": s["text"], "words": words})
            gid += 1
        chapters.append({"title": title, "start": round(segs[0]["start"] + off, 3), "seg": first_gid})
        print(f"  [{i:02d}] {title:32s} off {off/60:7.1f}min  {len(segs):4d} seg -> ids {first_gid}..{gid-1}",
              flush=True)

    doc = {"title": a.title, "audio": os.path.basename(by_no[ordered[0]]),
           "segments": segments, "chapters": chapters}
    validate_doc(doc, source=a.out)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {a.out}: {len(segments)} segments, {len(chapters)} chapters, {cum/60:.1f} min.", flush=True)


if __name__ == "__main__":
    main()
