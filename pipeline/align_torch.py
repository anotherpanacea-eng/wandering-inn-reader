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
    """Flatten sentences into alignable words, remembering the (sentence, display) origin.

    Returns (norm_words, meta) where meta[i] = {"seg": sentence_index, "w": display_word}
    for the i-th alignable word. Words that normalize to empty are skipped for
    alignment but still rendered (the player tolerates sentences whose word spans
    don't cover every glyph)."""
    norm_words, meta = [], []
    for si, sent in enumerate(sentences):
        for tok in sent.split():
            nw = normalize_word(tok)
            if not nw:
                continue
            norm_words.append(nw)
            meta.append({"seg": si, "w": tok})
    return norm_words, meta

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

    norm_words, meta = build_tokens(sentences)
    if not norm_words:
        sys.exit("No alignable words in the text.")

    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model(with_star=False).to(device)
    model.train(False)                             # inference (eval) mode, no dropout/BN updates
    tokenizer = bundle.get_tokenizer()
    aligner = bundle.get_aligner()

    waveform, sr = torchaudio.load(audio_path)
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

    # build per-segment word lists from the flat aligned spans
    n = len(sentences)
    seg_words = [[] for _ in range(n)]
    for spans, m in zip(token_spans, meta):
        s = round(spans[0].start * ratio, 3)
        e = round(spans[-1].end * ratio, 3)
        seg_words[m["seg"]].append({"w": m["w"], "s": s, "e": e})

    segments, last_end = [], 0.0
    for si, sent in enumerate(sentences):
        ws = seg_words[si]
        if ws:
            start, end = ws[0]["s"], ws[-1]["e"]
        else:                                       # no aligned word (rare): pin to last known time
            start = end = last_end
        last_end = max(last_end, end)
        segments.append({"id": len(segments), "start": round(start, 3),
                         "end": round(end, 3), "text": sent, "words": ws})
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

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    nwords = sum(len(s["words"]) for s in segs)
    dur = segs[-1]["end"] if segs else 0
    print(f"Wrote {a.out}: {len(segs)} sentences, {nwords} word-timings, "
          f"{len(doc.get('chapters', []))} chapters, {dur / 60:.1f} min of timeline.")

if __name__ == "__main__":
    main()
