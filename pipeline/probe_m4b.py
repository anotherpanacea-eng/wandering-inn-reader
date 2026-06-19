#!/usr/bin/env python3
"""
probe_m4b.py --m4b X.m4b --text book.txt --chapters book.chapters.json

Map an .m4b audiobook's chapter MARKS to the WEB chapters it narrates -- the m4b analog of
probe_track_starts.py (which discovers the mp3-multitrack map). An edited audiobook often splits one
long web chapter across two or three sequential marks, and numbers every mark "Chapter 1, 2, 3..."
regardless of web numbering, so the mark count rarely equals the web-chapter count. This reads the
marks via ffprobe (works whether moov is at head or tail), ASRs the opening ~26s of each non-credits
mark, and prints -- for every mark, by its RAW ffprobe index -- the transcript plus a best-guess web
chapter (highest word overlap with that chapter's opening). You read off which RAW index each web
chapter STARTS at and feed those to m4b_make_units.py (--starts / --end).

Indices are RAW (credits marks are shown, just not transcribed) so they line up 1:1 with the indices
m4b_make_units.py consumes. Run with the GPU interpreter (wav2vec2 ASR):
    py -3.12 probe_m4b.py --m4b "Book 14.m4b" --text book14.txt --chapters book14.chapters.json
"""
import argparse, io, json, re, subprocess, sys
try:
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
import soundfile as sf, torch, torchaudio
from m4b_common import FFMPEG, require, ffprobe_chapters

HEAD_SEC = 26
CREDITS = re.compile(r"credits", re.I)
_KEEP = re.compile(r"[^a-z']")


def words_of(text):
    return [w for w in (_KEEP.sub("", x.lower()).strip("'") for x in text.split()) if w]


def extract_wav(m4b, start_sec, dur, ffmpeg):
    cmd = [ffmpeg, "-v", "error", "-ss", f"{start_sec:.3f}", "-i", m4b, "-t", str(dur),
           "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace")[:200])
    data, sr = sf.read(io.BytesIO(p.stdout), dtype="float32", always_2d=True)
    return data.mean(axis=1), sr


def web_openings(text_path, chap_path, n_words=45):
    lines = [l for l in open(text_path, encoding="utf-8").read().split("\n") if l.strip()]
    chaps = json.load(open(chap_path, encoding="utf-8"))
    out = []
    for c in chaps:
        words = " ".join(lines[c["seg"]:c["seg"] + 6]).split()[:n_words]
        out.append((c["title"], " ".join(words)))
    return out


def best_match(heard, openings):
    """The web chapter whose opening words best overlap this mark's ASR (a hint for --starts)."""
    hw = set(words_of(heard))
    if not hw:
        return ("?", 0.0)
    scored = [(t, len(hw & set(words_of(o))) / len(set(words_of(o)) or {1})) for t, o in openings]
    return max(scored, key=lambda x: x[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--m4b", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--chapters", required=True)
    ap.add_argument("--ffmpeg", default=FFMPEG)
    a = ap.parse_args()
    ffmpeg = require(a.ffmpeg, "FFMPEG")

    marks = ffprobe_chapters(a.m4b)
    openings = web_openings(a.text, a.chapters)
    print(f"{len(marks)} marks; {len(openings)} web chapters\n", flush=True)
    print("=== WEB CHAPTER OPENINGS ===", flush=True)
    for title, opening in openings:
        print(f"  [{title}]  {opening}", flush=True)

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\ndevice: {dev}", flush=True)
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    model = bundle.get_model().to(dev); model.train(False)
    labels = bundle.get_labels(); SR = bundle.sample_rate

    def decode(emi):
        ids = emi.argmax(-1)[0].tolist(); out, prev = [], None
        for i in ids:
            if i != prev and i != 0:
                out.append(labels[i])
            prev = i
        return "".join(out).replace("|", " ").strip().lower()

    print("\n=== MARKS (RAW ffprobe index | start | mark title | best web match | ASR) ===", flush=True)
    for n, (start, title) in enumerate(marks):
        if CREDITS.search(title):
            print(f"\n[{n:02d}] {start/60:7.2f}min  ({title})  <credits -- ASR skipped>", flush=True)
            continue
        mono, sr = extract_wav(a.m4b, start, HEAD_SEC, ffmpeg)
        wav = torch.from_numpy(mono).unsqueeze(0)
        if sr != SR:
            wav = torchaudio.functional.resample(wav, sr, SR)
        with torch.inference_mode():
            emi, _ = model(wav.to(dev))
        heard = decode(emi)
        mt, score = best_match(heard, openings)
        print(f"\n[{n:02d}] {start/60:7.2f}min  ({title})  -> best: [{mt}] ({score:.2f})\n"
              f"     {heard[:200]}", flush=True)

    print("\nDONE probe_m4b.py -- read off the RAW index each web chapter STARTS at -> m4b_make_units.py",
          flush=True)


if __name__ == "__main__":
    main()
