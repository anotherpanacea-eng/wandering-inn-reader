#!/usr/bin/env python3
"""
align_torch.py — word-level forced alignment with torchaudio's MMS_FA, emitting
the Inn Reader player JSON directly. An alternative to the aeneas path that:

  * needs no espeak / ffmpeg-via-aeneas / `numpy<2` dance — just torch + torchaudio
    (which you already have on the GPU box), and
  * produces WORD-level timings, so the player's per-word glow works, not just the
    sentence highlight aeneas gives.

It does NOT transcribe. You supply the known text (one sentence per line, exactly
what fetch_text.py writes); this aligns those words to the audio and reads back
timestamps. Same contract as align.py, same output schema.

Usage:
  python3 align_torch.py --audio track01.mp3 --text track01.txt \
      --title "Book 12 — track 01" --out align01.json
  # whole-volume text + chapter markers:
  python3 align_torch.py --audio volume12.mp3 --text volume12.txt \
      --chapters volume12.chapters.json --title "Book 12" --out book12.json

Device: auto (cuda -> mps -> cpu); ROCm shows up as "cuda". Override with --device.

Scale note: align ONE TRACK at a time first (the README's prove-it-first loop).
Aligning a whole 30-hour volume in a single pass is memory-heavy — forced_align is
O(frames x tokens). Per-track (<=~1 hour) is the comfortable size; for the full
volume, align tracks individually and concatenate the segment lists, or chunk.

Dependencies:  pip3 install torch torchaudio   (the MMS_FA model is CC-BY-NC —
fine for personal read-along, not for resale; see the README's license note.)
"""
import argparse, json, re, sys
from schema import validate_doc

# MMS_FA is trained on lowercased text over a small Latin charset. Map display
# words to that space for alignment; keep the original spelling for the player.
_KEEP = re.compile(r"[^a-z']")

def normalize_word(w):
    """Lowercase, strip to [a-z']; '' if nothing alignable remains (pure punctuation)."""
    return _KEEP.sub("", w.lower()).strip("'")

def read_sentences(path):
    """Non-blank lines, in order — each is one sentence/segment (matches fetch_text)."""
    with open(path, "r", encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]

def build_tokens(sentences):
    """Every display token, in order, tagged with its sentence and alignable form.

    Returns a list of {"seg", "w" (display), "nw" (uroman-normalized)}. Tokens that
    normalize to empty — standalone numbers like "10", punctuation-only — are NOT sent
    to the aligner, but are KEPT so the player (which rebuilds the active sentence from
    `words`) still shows the full text; they inherit a neighbouring word's timestamp."""
    tokens = []
    for si, sent in enumerate(sentences):
        for tok in sent.split():
            tokens.append({"seg": si, "w": tok, "nw": normalize_word(tok)})
    return tokens

def attach_chapters(segs, markers):
    """Same contract as align.py: map chapter markers' `seg` index onto segment start times."""
    chapters, dropped = [], 0
    for m in markers:
        seg = m.get("seg", m.get("first_line"))
        title = (m.get("title") or "Chapter").strip()
        if seg is None or not (0 <= seg < len(segs)):
            print(f"  ! chapter {title!r}: seg index {seg} out of range "
                  f"(0..{len(segs) - 1}) — dropping", file=sys.stderr)
            dropped += 1
            continue
        chapters.append({"title": title, "start": segs[seg]["start"], "seg": seg})
    if dropped:
        print(f"  ! {dropped} chapter marker(s) dropped; text/audio mismatch likely.",
              file=sys.stderr)
    return chapters

def load_audio(path):
    """Load audio → (waveform[channels, samples] float32 tensor, sample_rate).

    Deliberately avoids torchaudio.load: torchaudio >= 2.11 routes it through
    torchcodec, which needs ffmpeg — defeating this tool's no-ffmpeg goal. We read
    WAV/FLAC/OGG with soundfile (libsndfile, no ffmpeg). For mp3/m4a, convert to WAV
    first — macOS: `afconvert -f WAVE -d LEI16@16000 in.mp3 out.wav` (native), or
    ffmpeg if you have it."""
    import torch
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype="float32", always_2d=True)    # (frames, channels)
        return torch.from_numpy(data.T).contiguous(), sr
    except Exception as e_sf:
        import wave, numpy as np
        try:
            with wave.open(path, "rb") as w:                          # 16-bit PCM WAV fallback
                ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
                raw = w.readframes(n)
            if sw != 2:
                raise RuntimeError(f"fallback handles 16-bit PCM WAV only (got {sw * 8}-bit)")
            a = (np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0).reshape(-1, ch).T
            return torch.from_numpy(a).contiguous(), sr
        except Exception as e_wave:
            try:
                import torchaudio                                     # last resort (needs torchcodec/ffmpeg)
                return torchaudio.load(path)
            except Exception as e_ta:
                sys.exit(f"Could not read audio {path!r}. Convert to WAV first — macOS: "
                         f"afconvert -f WAVE -d LEI16@16000 in.mp3 out.wav\n"
                         f"  soundfile: {e_sf}\n  wave: {e_wave}\n  torchaudio: {e_ta}")

def pick_device(override):
    import torch
    if override:
        return override
    if torch.cuda.is_available():     # ROCm presents as cuda too
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"

def align(audio_path, sentences, device):
    """Run MMS_FA forced alignment. Returns segments[] in the player schema."""
    try:
        import torch, torchaudio
    except ImportError as e:
        sys.exit(f"torch/torchaudio not installed: {e}\n"
                 f"  pip3 install torch torchaudio")

    tokens = build_tokens(sentences)
    norm_words = [t["nw"] for t in tokens if t["nw"]]
    if not norm_words:
        sys.exit("No alignable words in the text.")

    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model(with_star=False).to(device)
    model.train(False)                             # inference (eval) mode, no dropout/BN updates
    tokenizer = bundle.get_tokenizer()
    aligner = bundle.get_aligner()

    waveform, sr = load_audio(audio_path)
    if waveform.size(0) > 1:                       # downmix to mono
        waveform = waveform.mean(dim=0, keepdim=True)
    if sr != bundle.sample_rate:
        waveform = torchaudio.functional.resample(waveform, sr, bundle.sample_rate)
        sr = bundle.sample_rate

    with torch.inference_mode():
        emission, _ = model(waveform.to(device))
        token_spans = aligner(emission[0], tokenizer(norm_words))

    # frames -> seconds: total samples / total emission frames / sample_rate
    ratio = waveform.size(1) / emission.size(1) / sr

    if len(token_spans) != len(norm_words):
        print(f"  ! aligner returned {len(token_spans)} word spans for {len(norm_words)} "
              f"words — alignment is unreliable; inspect the text.", file=sys.stderr)

    # give each ALIGNABLE token its span time; skipped tokens stay None for now
    ai = 0
    for t in tokens:
        if t["nw"] and ai < len(token_spans):
            sp = token_spans[ai]; ai += 1
            t["s"], t["e"] = sp[0].start * ratio, sp[-1].end * ratio
        else:
            t["s"] = t["e"] = None

    # group by sentence; fill skipped tokens from a neighbour so `words` covers the
    # WHOLE text (the player renders the active sentence purely from `words`)
    seg_tokens = [[] for _ in range(len(sentences))]
    for t in tokens:
        seg_tokens[t["seg"]].append(t)

    segments, last_end = [], 0.0
    for si, sent in enumerate(sentences):
        toks = seg_tokens[si]
        seed = next((t["s"] for t in toks if t["s"] is not None), last_end)  # first real time, else timeline
        prev = seed
        words = []
        for t in toks:
            if t["s"] is None:                      # skipped token: inherit the previous end (zero-width)
                s = e = prev
            else:
                s, e = t["s"], t["e"]; prev = e
            words.append({"w": t["w"], "s": round(s, 3), "e": round(e, 3)})
        start = words[0]["s"] if words else last_end
        end = max((w["e"] for w in words), default=last_end)
        last_end = max(last_end, end)
        segments.append({"id": len(segments), "start": round(start, 3),
                         "end": round(end, 3), "text": sent, "words": words})
    return segments

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, help="one track's audio (mp3/m4a/wav)")
    ap.add_argument("--text", required=True, help="sentence-per-line text (fetch_text.py output)")
    ap.add_argument("--chapters", help="fetch_text.py <out>.chapters.json for chapter markers")
    ap.add_argument("--title", default="Untitled")
    ap.add_argument("--out", default="align.json")
    ap.add_argument("--device", choices=["cuda", "mps", "cpu"], help="override device auto-detect")
    a = ap.parse_args()

    sentences = read_sentences(a.text)
    if not sentences:
        sys.exit(f"No sentences in {a.text}.")

    device = pick_device(a.device)
    print(f"  aligning {len(sentences)} sentences on {device} ...", file=sys.stderr)
    segs = align(a.audio, sentences, device)

    doc = {"title": a.title, "audio": a.audio.split("/")[-1], "segments": segs}
    if a.chapters:
        chapters = attach_chapters(segs, json.load(open(a.chapters, encoding="utf-8")))
        if chapters:
            doc["chapters"] = chapters

    validate_doc(doc, source=a.out)        # fail loud on a malformed envelope before writing
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    nwords = sum(len(s["words"]) for s in segs)
    dur = segs[-1]["end"] if segs else 0
    print(f"Wrote {a.out}: {len(segs)} sentences, {nwords} word-timings, "
          f"{len(doc.get('chapters', []))} chapters, {dur / 60:.1f} min of timeline.")

if __name__ == "__main__":
    main()
