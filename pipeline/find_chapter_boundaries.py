#!/usr/bin/env python3
"""
find_chapter_boundaries.py --audio-glob "...*.mp3" --text book.txt --chapters book.chapters.json
                           --out boundaries.json [--refine-window 300] [--chunk 20]

For an audiobook whose tracks are uniform TIME-SLICES that STRADDLE chapters (e.g. Book 17: 10 tracks,
14 chapters), locate each web chapter's START time in the continuous (concatenated) audio timeline, so
each chapter can then be cut and per-chapter anchored (reducing the book to the m4b per-chapter flow).

Two stages, both wav2vec2-ASR:
  (1) ANCHOR  -- ASR each track's opening, fuzzy-locate that text in book.txt -> (global_time, seg)
                 anchor pairs. Piecewise-linear interpolation over the anchors estimates the start time
                 of any chapter's first segment (anchored every track, not one global proportional guess).
  (2) REFINE  -- ASR a +/- refine-window window around each chapter's estimated start, in `chunk`-second
                 sub-chunks; the sub-chunk whose transcript best overlaps the chapter's opening words is
                 the start (~chunk-second accuracy; align_book self-corrects from there). Low confidence
                 (max overlap < 0.3) auto-widens the window once, then is flagged.

Run with the GPU interpreter:  py -3.12 find_chapter_boundaries.py ...
"""
import argparse, glob, json, os, re, sys
try:
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
import numpy as np, soundfile as sf, torch, torchaudio

_KEEP = re.compile(r"[^a-z']")
def words_of(text):
    return [w for w in (_KEEP.sub("", x.lower()).strip("'") for x in text.split()) if w]

def track_no(p):
    m = re.search(r"(\d+)", os.path.basename(p))
    return int(m.group(1)) if m else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-glob", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--chapters", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--refine-window", type=float, default=300.0, help="+/- seconds ASR'd around each estimate")
    ap.add_argument("--chunk", type=float, default=20.0, help="refine sub-chunk seconds (= start accuracy)")
    ap.add_argument("--anchor-sec", type=float, default=30.0)
    a = ap.parse_args()

    SR = 16000
    paths = sorted(glob.glob(a.audio_glob), key=track_no)
    if not paths:
        sys.exit("no audio matched --audio-glob")
    tracks, g = [], 0.0
    for p in paths:
        d = sf.info(p).duration
        tracks.append({"path": p, "dur": d, "g0": g}); g += d
    total_dur = g
    print(f"{len(tracks)} tracks, {total_dur/60:.1f} min total", flush=True)

    segs = [l for l in open(a.text, encoding="utf-8").read().split("\n") if l.strip()]
    chaps = json.load(open(a.chapters, encoding="utf-8"))
    total_segs = len(segs)
    seg_words = [words_of(s) for s in segs]                      # per-segment normalized words
    print(f"{total_segs} segments, {len(chaps)} chapters", flush=True)

    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = bundle.get_model().to(dev); model.train(False)
    labels = bundle.get_labels()
    print(f"device {dev}", flush=True)

    def read_global(g0, dur):
        """Concatenated 16k-mono samples for the global span [g0, g0+dur), crossing track edges."""
        g1 = g0 + dur
        out = []
        for t in tracks:
            ts, te = t["g0"], t["g0"] + t["dur"]
            if te <= g0 or ts >= g1:
                continue
            a0, b0 = max(g0, ts) - ts, min(g1, te) - ts
            sr = sf.info(t["path"]).samplerate
            data, _ = sf.read(t["path"], start=int(a0 * sr), frames=int((b0 - a0) * sr),
                              dtype="float32", always_2d=True)
            mono = data.mean(axis=1)
            if sr != SR:
                mono = torchaudio.functional.resample(torch.from_numpy(mono)[None], sr, SR)[0].numpy()
            out.append(mono)
        return np.concatenate(out) if out else np.zeros(0, np.float32)

    def asr(samples):
        if samples.size < SR // 2:
            return ""
        wav = torch.from_numpy(samples)[None].to(dev)
        with torch.inference_mode():
            emi, _ = model(wav)
        ids = emi.argmax(-1)[0].tolist(); o, prev = [], None
        for i in ids:
            if i != prev and i != 0: o.append(labels[i])
            prev = i
        return "".join(o).replace("|", " ").strip().lower()

    def locate_seg(asr_words, est_seg, margin=3000):
        """Find the segment index whose following ~len words best overlap asr_words (search near est_seg)."""
        target = set(asr_words[:60])
        if not target:
            return est_seg, 0.0
        lo, hi = max(0, est_seg - margin), min(total_segs, est_seg + margin)
        best_i, best = lo, -1.0
        for s in range(lo, hi):
            win = []
            j = s
            while len(win) < 60 and j < total_segs:
                win += seg_words[j]; j += 1
            if not win:
                continue
            ov = len(target & set(win)) / len(target)
            if ov > best:
                best, best_i = ov, s
        return best_i, best

    # ---- (1) ANCHOR ----
    print("\n=== ANCHOR (track openings -> seg) ===", flush=True)
    anchors = []                                              # (global_time, seg)
    for t in tracks:
        est = int(round(t["g0"] / total_dur * total_segs))
        heard = asr(read_global(t["g0"], a.anchor_sec))
        seg_i, conf = locate_seg(words_of(heard), est)
        anchors.append((t["g0"], seg_i))
        print(f"  track @{t['g0']/60:7.1f}min  est_seg {est:6d} -> seg {seg_i:6d} (ov {conf:.2f})", flush=True)
    anchors.append((total_dur, total_segs))
    anchors = sorted(set(anchors))

    def interp_time(seg):
        for i in range(len(anchors) - 1):
            (g0, s0), (g1, s1) = anchors[i], anchors[i + 1]
            if s0 <= seg <= s1:
                if s1 == s0:
                    return g0
                return g0 + (g1 - g0) * (seg - s0) / (s1 - s0)
        return total_dur * seg / total_segs

    # ---- (2) REFINE ----
    print("\n=== REFINE (per-chapter opening localization) ===", flush=True)
    results = []
    for c in chaps:
        seg_c, title = c["seg"], c["title"]
        opening = set(words_of(" ".join(segs[seg_c:seg_c + 6]))[:40])
        est = interp_time(seg_c)

        def search(W):
            g0 = max(0.0, est - W)
            samples = read_global(g0, min(2 * W, total_dur - g0))
            n = int(a.chunk * SR)
            best_t, best_ov = g0, -1.0
            for k in range(0, max(1, samples.size - n // 2), n):
                heard = set(words_of(asr(samples[k:k + n])))
                ov = (len(opening & heard) / len(opening)) if opening else 0.0
                if ov > best_ov:
                    best_ov, best_t = ov, g0 + k / SR
            return best_t, best_ov

        t_ref, ov = search(a.refine_window)
        if ov < 0.3:                                          # widen once on low confidence
            t2, ov2 = search(a.refine_window * 2.2)
            if ov2 > ov:
                t_ref, ov = t2, ov2
        flag = "" if ov >= 0.3 else "  <-- LOW CONFIDENCE, verify"
        results.append({"seg": seg_c, "title": title, "start": round(t_ref, 2),
                        "est": round(est, 2), "overlap": round(ov, 2)})
        print(f"  [{title:34s}] est {est/60:7.1f}min -> start {t_ref/60:7.1f}min  (ov {ov:.2f}){flag}",
              flush=True)

    # monotonicity check
    starts = [r["start"] for r in results]
    if starts != sorted(starts):
        print("\n!! WARNING: chapter starts are NOT monotonic -- a refine landed wrong; inspect above.",
              flush=True)
    json.dump({"total_dur": round(total_dur, 2), "tracks": [t["path"] for t in tracks],
               "chapters": results}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nwrote {a.out} ({len(results)} chapter boundaries)", flush=True)


if __name__ == "__main__":
    main()
