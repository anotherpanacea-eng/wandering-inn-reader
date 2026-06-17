#!/usr/bin/env python3
"""
probe_track_starts.py -- ASR the first ~25s of every audiobook track and print it, to DISCOVER the
chapter->track map that align_chapters.py / recombine_chapters.py consume. An edited audiobook splits
long chapters across several tracks; each chapter's FIRST track carries the narrated "Chapter N" /
"Interlude" announcement, so the openings reveal which track starts each chapter -- WITHOUT trusting
any prior (possibly drifted) forced alignment. Build book##_track_map.json by hand from this output
(join the discovered chapter-start tracks with the web chapter list + sentence offsets).

Run with the GPU interpreter (wav2vec2 ASR):
    py -3.12 probe_track_starts.py --audio-glob "...\\*.mp3" [--out track_openings.json]
"""
import argparse, glob, json, os, re, sys
try:
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def track_no(p):
    m = re.search(r"(\d+)", os.path.basename(p))
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-glob", required=True, help="glob for all audiobook tracks (numbered NN - ...)")
    ap.add_argument("--head-sec", type=float, default=25.0, help="seconds of each track's opening to transcribe")
    ap.add_argument("--out", help="optional JSON file to write [{track, minutes, text}] for map-building")
    a = ap.parse_args()

    import numpy as np, soundfile as sf, torch, torchaudio

    tracks = sorted((p for p in glob.glob(a.audio_glob) if track_no(p) is not None), key=track_no)
    if not tracks:
        sys.exit(f"--audio-glob matched no numbered tracks: {a.audio_glob}")
    print(f"{len(tracks)} tracks", flush=True)

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print("device:", dev, "torch:", torch.__version__, flush=True)
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    model = bundle.get_model().to(dev).eval()
    labels = bundle.get_labels()
    sr = bundle.sample_rate

    def decode(emission):
        ids = emission.argmax(dim=-1)[0].tolist()
        out, prev = [], None
        for i in ids:
            if i != prev and i != 0:          # CTC collapse; 0 == blank
                out.append(labels[i])
            prev = i
        return "".join(out).replace("|", " ").strip().lower()

    rows = []
    for p in tracks:
        no = track_no(p)
        info = sf.info(p)
        n = int(a.head_sec * info.samplerate)
        data, in_sr = sf.read(p, frames=n, start=0, dtype="float32", always_2d=True)
        wav = torch.from_numpy(data.mean(axis=1, keepdims=True).T)   # -> (1, samples) mono
        if in_sr != sr:
            wav = torchaudio.functional.resample(wav, in_sr, sr)
        with torch.inference_mode():
            emission, _ = model(wav.to(dev))
        txt = decode(emission)
        print(f"\n[{no:02d}] ({info.duration/60:5.1f}min)  {txt[:220]}", flush=True)
        rows.append({"track": no, "minutes": round(info.duration / 60, 1), "text": txt})

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"\nwrote {a.out} ({len(rows)} tracks)", flush=True)
    print("\nDONE probe_track_starts.py", flush=True)


if __name__ == "__main__":
    main()
