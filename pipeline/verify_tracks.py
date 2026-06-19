#!/usr/bin/env python3
"""
verify_tracks.py -- ASR-vs-alignment GATE on the final per-track files. Forced alignment maps GIVEN
text onto audio; it never checks the audio actually SAYS that text, so on an edited audiobook it can
look plausible (monotonic) while being content-wrong. This transcribes a short window of the real
audio at sampled points and scores word-overlap against the aligned text covering that window: a low
score means the read-along words don't track the narration there. Prints each point for eyeballing AND
exits nonzero if too many points fail -- so it can gate a ship in CI/scripts, not just inform a human.

Run with the GPU interpreter (wav2vec2 ASR):
    py -3.12 verify_tracks.py --dir per_track_final --audio-glob "...\\*.mp3" [--tracks 02 21 35] [--points 5]
"""
import argparse, glob, json, os, re, sys
try:
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from schema import validate_doc

_KEEP = re.compile(r"[^a-z']")
def norm(w): return _KEEP.sub("", w.lower()).strip("'")
def words_of(text): return [w for w in (norm(x) for x in text.split()) if w]

def track_no(s):
    m = re.search(r"(\d+)", os.path.basename(s))
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="per-track output dir (alignNN.json)")
    ap.add_argument("--audio-glob", required=True, help="glob for the audiobook tracks (numbered NN - ...)")
    ap.add_argument("--tracks", nargs="*", help="track numbers to sample (default: a spread across the book)")
    ap.add_argument("--points", type=int, default=5, help="probe points per track")
    ap.add_argument("--win", type=float, default=8.0, help="seconds of audio transcribed per point")
    ap.add_argument("--min-overlap", type=float, default=0.4,
                    help="a point PASSES if >= this fraction of the aligned-window words appear in the ASR")
    ap.add_argument("--max-fail-frac", type=float, default=0.4,
                    help="exit nonzero if more than this fraction of sampled points FAIL")
    a = ap.parse_args()

    import numpy as np, soundfile as sf, torch, torchaudio

    audio_by_no = {track_no(p): p for p in glob.glob(a.audio_glob) if track_no(p)}
    aligns = {track_no(p): p for p in glob.glob(os.path.join(a.dir, "align*.json")) if track_no(p)}
    if not aligns:
        sys.exit(f"no alignNN.json in {a.dir}")
    avail = sorted(aligns)
    tracks = a.tracks or [avail[int(round(k))] for k in np.linspace(0, len(avail) - 1, min(6, len(avail)))]
    tracks = [t.zfill(2) for t in tracks]

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    model = bundle.get_model().to(dev).train(False); labels = bundle.get_labels(); sr = bundle.sample_rate
    print(f"device {dev}; sampling tracks {tracks}; pass>= {a.min_overlap} overlap", flush=True)

    def asr(path, t0):
        info = sf.info(path); n = int(a.win * info.samplerate); start = int(t0 * info.samplerate)
        data, in_sr = sf.read(path, frames=n, start=start, dtype="float32", always_2d=True)
        wav = torch.from_numpy(data.mean(axis=1, keepdims=True).T)
        if in_sr != sr:
            wav = torchaudio.functional.resample(wav, in_sr, sr)
        with torch.inference_mode():
            emi, _ = model(wav.to(dev))
        ids = emi.argmax(-1)[0].tolist(); out, prev = [], None
        for i in ids:
            if i != prev and i != 0: out.append(labels[i])
            prev = i
        return "".join(out).replace("|", " ").strip().lower()

    def aligned_window(segs, t0, t1):
        cov = [s for s in segs if s["end"] >= t0 and s["start"] <= t1]
        if not cov:
            cov = [min(segs, key=lambda s: abs((s["start"] + s["end"]) / 2 - t0))]
        return " ".join(s["text"] for s in cov)

    total, failed = 0, 0
    for tno in tracks:
        if tno not in aligns or tno not in audio_by_no:
            print(f"\n[{tno}] (no align/audio)"); continue
        with open(aligns[tno], encoding="utf-8") as f:
            doc = json.load(f)
        validate_doc(doc, source=aligns[tno])            # consumer-side fail-loud on a bad per-track file
        segs = doc["segments"]; ap_path = audio_by_no[tno]; dur = sf.info(ap_path).duration
        last = max(s["end"] for s in segs)
        chs = ", ".join(c["title"] for c in doc.get("chapters", [])) or "-"
        print(f"\n===== track {tno}  ({dur/60:.1f}min audio, text to {last/60:.1f}min)  chapters: {chs} =====",
              flush=True)
        for frac in np.linspace(0.02, 0.96, a.points):
            t0 = round(min(frac * dur, dur - a.win), 1)
            atext = aligned_window(segs, t0, t0 + a.win)
            heard = asr(ap_path, t0)
            aw, hw = set(words_of(atext)), set(words_of(heard))
            overlap = (len(aw & hw) / len(aw)) if aw else 0.0
            ok = overlap >= a.min_overlap
            total += 1; failed += 0 if ok else 1
            print(f"\n  t={t0/60:5.1f}min  overlap={overlap:.2f}  {'PASS' if ok else 'FLAG'}", flush=True)
            print(f"    ALIGN: {atext[:150]}", flush=True)
            print(f"    AUDIO: {heard[:150]}", flush=True)

    frac_fail = failed / total if total else 1.0
    print(f"\n=== {total-failed}/{total} points PASS, {failed} FLAG ({frac_fail:.0%} fail; "
          f"threshold {a.max_fail_frac:.0%}) ===", flush=True)
    if total == 0:
        sys.exit("no points sampled")
    if frac_fail > a.max_fail_frac:
        sys.exit(f"GATE FAIL: {frac_fail:.0%} of points below {a.min_overlap} overlap (> {a.max_fail_frac:.0%}).")
    print("GATE PASS", flush=True)


if __name__ == "__main__":
    main()
