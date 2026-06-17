#!/usr/bin/env python3
"""
align_book.py — long-form forced alignment for a whole audiobook (many long tracks)
using torchaudio MMS_FA, without OOM. The stock align_torch.py feeds the WHOLE clip
to the model in one pass — O(time^2) attention — so it dies on real >1h tracks.

This version streams the audio and aligns in a SLIDING WINDOW with a text cursor:

  * audio is decoded track-by-track to mono 16 kHz and pushed onto a rolling buffer;
  * each step aligns ~WINDOW_SEC of audio against a GENEROUS slice of upcoming text;
  * words whose end falls in the last SAFETY_SEC of the window are NOT trusted
    (the window may have crammed too-much text into its tail) — they're dropped and
    re-aligned next step, because the buffer only advances to the last *trusted* word.

That makes it self-correcting (a bad words-per-second guess heals each step), linear
in audio length, and bounded in memory. Emission is computed in EMIT_SUBCHUNK_SEC
pieces on the GPU; forced_align runs on CPU (cheap, and dodges ROCm kernel gaps).

Output schema == align.py / align_torch.py (segments[], optional words[], chapters[]).
"""
import argparse, json, re, sys, glob, os, pickle, time, subprocess
from schema import validate_doc

_KEEP = re.compile(r"[^a-z']")
def normalize_word(w): return _KEEP.sub("", w.lower()).strip("'")

def read_sentences(path):
    with open(path, "r", encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]

def build_tokens(sentences):
    toks = []
    for si, sent in enumerate(sentences):
        for w in sent.split():
            toks.append({"seg": si, "w": w, "nw": normalize_word(w)})
    return toks

class AudioStream:
    """Sequential mono-16k reader over an ordered list of audio files, with a rolling
    buffer. Never seeks backward; we only drop buffer that's been committed."""
    def __init__(self, files, target_sr=16000, block_sec=30):
        import torch, torchaudio, soundfile as sf, numpy as np
        self.torch, self.taF, self.sf, self.np = torch, torchaudio.functional, sf, np
        self.files, self.target_sr, self.block_sec = files, target_sr, block_sec
        self.fi = 0; self._fh = None; self._sr = None
        self.buf = np.zeros(0, dtype=np.float32)
        self.buf_start = 0.0          # global seconds at buf[0]
        self.exhausted = False

    def _open_next(self):
        while self.fi < len(self.files):
            try:
                self._fh = self.sf.SoundFile(self.files[self.fi]); self._sr = self._fh.samplerate
                return True
            except Exception as e:
                print(f"  ! skip {os.path.basename(self.files[self.fi])}: {e}", file=sys.stderr)
                self.fi += 1
        return False

    def _pull_block(self):
        """Append one decoded+resampled block to buf. Returns False when fully done."""
        if self._fh is None and not self._open_next():
            self.exhausted = True; return False
        data = self._fh.read(int(self.block_sec * self._sr), dtype="float32", always_2d=True)
        if len(data) == 0:
            self._fh.close(); self._fh = None; self.fi += 1
            return self._pull_block()
        mono = data.mean(axis=1)
        if self._sr != self.target_sr:
            t = self.torch.from_numpy(mono)
            mono = self.taF.resample(t, self._sr, self.target_sr).numpy()
        self.buf = self.np.concatenate([self.buf, mono])
        return True

    def ensure(self, need_sec):
        """Make buf hold >= need_sec seconds (or until audio ends)."""
        need = int(need_sec * self.target_sr)
        while len(self.buf) < need and not self.exhausted:
            self._pull_block()
        return len(self.buf) >= need

    def window(self, win_sec):
        """Return (samples, is_final) for the next win_sec without consuming."""
        self.ensure(win_sec)
        n = int(win_sec * self.target_sr)
        is_final = self.exhausted and len(self.buf) <= n
        return self.buf[:n].copy(), is_final

    def commit(self, sec):
        """Drop `sec` seconds from the front of buf; advance global clock."""
        n = int(sec * self.target_sr)
        n = min(n, len(self.buf))
        self.buf = self.buf[n:]
        self.buf_start += n / self.target_sr

    def fast_forward(self, target_sec):
        """Skip ahead to `target_sec` global seconds (for --resume). Fuzzy mp3 seek is fine;
        the sliding window re-aligns from there."""
        cum = 0.0
        for k, f in enumerate(self.files):
            d = self.sf.info(f).duration
            if cum + d > target_sec:
                self.fi = k
                self._fh = self.sf.SoundFile(f); self._sr = self._fh.samplerate
                self._fh.seek(int((target_sec - cum) * self._sr))
                self.buf = self.np.zeros(0, dtype=self.np.float32)
                self.buf_start = target_sec
                return
            cum += d
        self.exhausted = True

def emission_for(model, device, samples, sub_sec, sr, torch):
    """Compute MMS_FA emission for `samples` (1-D np float32) in GPU sub-chunks, concatenated."""
    pieces = []
    step = int(sub_sec * sr)
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        if len(chunk) < sr // 2 and pieces:          # tiny trailing sub-chunk: SKIP -- feeding a <0.5s
            continue                                 # chunk to the model risks a degenerate emission frame
        x = torch.from_numpy(chunk).unsqueeze(0).to(device)
        with torch.inference_mode():
            emi, _ = model(x)
        pieces.append(emi.cpu())
    if not pieces:
        return None
    return torch.cat(pieces, dim=1)                  # (1, F, V) on CPU

def save_ckpt(path, pos_in_align, audio_pos, word_time):
    """Crash-DURABLE resume state. The old version did open->dump->os.replace with NO fsync; on this
    box a hard THERMAL reboot mid-write left a full-size but ALL-ZERO file (data sat in the page
    cache, never flushed) so --resume was impossible. Fix: fsync the temp file's DATA to the SSD
    BEFORE the rename, and demote the previous good checkpoint to .bak so even a torn final rename
    still leaves a usable fallback. No-op if `path` is falsy (the watchdog's save_fn can fire even when the
    run has no --checkpoint). Write to a LOCAL, NON-Dropbox, COOL drive."""
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump({"pos": pos_in_align, "audio_pos": audio_pos, "word_time": word_time},
                    f, protocol=pickle.HIGHEST_PROTOCOL)
        f.flush()
        os.fsync(f.fileno())                  # force DATA to disk before we rename over the target
    if os.path.exists(path):
        try: os.replace(path, path + ".bak")  # keep last good ckpt as fallback
        except OSError: pass
    os.replace(tmp, path)                      # atomic on NTFS; data already durable
    try:                                       # best-effort dir fsync (no-op/!supported on Windows)
        dfd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    except (OSError, ValueError, AttributeError):
        pass

def _valid_ckpt(d):
    return isinstance(d, dict) and {"pos", "audio_pos", "word_time"} <= set(d)

def load_ckpt(path):
    """Load the primary checkpoint; if missing/corrupt/zeroed, fall back to .bak. Returns None when
    neither is usable (caller then starts FRESH and says so — fail loud, don't pretend to resume)."""
    for p in (path, path + ".bak"):
        try:
            with open(p, "rb") as f:
                d = pickle.load(f)
            if _valid_ckpt(d):
                if p != path:
                    print(f"  ! primary checkpoint unusable; RESUMED from fallback {os.path.basename(p)}",
                          file=sys.stderr)
                return d
        except (OSError, EOFError, pickle.UnpicklingError, ValueError) as e:
            print(f"  ! checkpoint {os.path.basename(p)} unreadable ({type(e).__name__}); trying fallback",
                  file=sys.stderr)
    return None

def read_drive_temp(smartctl, dev):
    """(composite_C, hotspot_C) for the protected drive via `smartctl -x`, or (None, None) on failure
    (e.g. not elevated). Hotspot = hottest 'Temperature Sensor N'. Metadata read — adds no drive heat."""
    try:
        out = subprocess.run([smartctl, "-x", dev], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return (None, None)
    comp = hot = None
    for ln in out.splitlines():
        m = re.match(r"\s*Temperature:\s+(\d+)\s*Celsius", ln)
        if m:
            comp = float(m.group(1))
        m = re.match(r"\s*Temperature Sensor \d+:\s+(\d+)\s*Celsius", ln)
        if m:
            v = float(m.group(1)); hot = v if hot is None else max(hot, v)
    if hot is None:
        hot = comp
    return (comp, hot)


def thermal_guard(a, save_fn):
    """Drive-temp WATCHDOG. If the protected drive (P5/C:) is too hot, idle until it cools — or abort
    durably if it won't. No-op without --smartctl; warns ONCE and runs open-loop if the probe fails."""
    if not a.smartctl:
        return
    comp, hot = read_drive_temp(a.smartctl, a.smartctl_dev)
    if comp is None:
        if not getattr(thermal_guard, "_warned", False):
            print("  ! WATCHDOG: smartctl read failed (need admin?) — OPEN-LOOP, fixed cooldowns only",
                  file=sys.stderr)
            thermal_guard._warned = True
        return
    if comp < a.temp_pause and hot < a.temp_hot_pause:
        return
    save_fn()
    pause = a.cooldown if a.cooldown else 30.0
    waited = 0.0
    print(f"  WATCHDOG PAUSE: drive {comp:.0f}C comp / {hot:.0f}C hotspot >= "
          f"{a.temp_pause:.0f}/{a.temp_hot_pause:.0f} — idling to cool", file=sys.stderr); sys.stderr.flush()
    while True:
        time.sleep(pause); waited += pause
        comp, hot = read_drive_temp(a.smartctl, a.smartctl_dev)
        if comp is None:
            print("  ! WATCHDOG: lost smartctl mid-pause; continuing open-loop", file=sys.stderr); return
        print(f"    ...cooling {comp:.0f}C / {hot:.0f}C (waited {waited:.0f}s)", file=sys.stderr); sys.stderr.flush()
        if comp <= a.temp_resume:
            print(f"  WATCHDOG RESUME: cooled to {comp:.0f}C / {hot:.0f}C", file=sys.stderr); return
        if waited >= a.temp_max_wait:
            save_fn()
            sys.exit(f"WATCHDOG ABORT: drive stuck hot ({comp:.0f}C/{hot:.0f}C) after {waited:.0f}s — "
                     "checkpoint saved; fix cooling and re-run (it resumes).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", nargs="+", required=True, help="ordered audio files (one book)")
    ap.add_argument("--text", required=True)
    ap.add_argument("--chapters")
    ap.add_argument("--title", default="Untitled")
    ap.add_argument("--out", default="book.json")
    ap.add_argument("--device", default=None)
    ap.add_argument("--window", type=float, default=150.0)
    ap.add_argument("--safety", type=float, default=12.0)
    ap.add_argument("--sub", type=float, default=30.0)
    ap.add_argument("--wps", type=float, default=2.5,
                    help="words/sec the narration ACTUALLY runs at; sets text provisioned per window "
                         "(grab = window*wps*overprovide). OVER-provisioning DRIFTS: forced_align compresses "
                         "the excess and commits it too early (measured ~1.68x text-over-run at the old "
                         "2.9 x 1.5 default; 2.5 x 1.0 tracked 1.00x on real audio). Err LOW -- under-"
                         "provisioning just takes smaller, self-correcting steps; over-provisioning doesn't.")
    ap.add_argument("--overprovide", type=float, default=1.0,
                    help="multiplier on text provisioned per window; keep ~1.0 (see --wps).")
    ap.add_argument("--max-seconds", type=float, default=None, help="debug: stop after N audio sec")
    ap.add_argument("--checkpoint", help="path to a resume checkpoint (LOCAL dir, not Dropbox)")
    ap.add_argument("--resume", action="store_true", help="resume from --checkpoint if present")
    ap.add_argument("--checkpoint-every", type=int, default=15, help="save checkpoint every N steps")
    ap.add_argument("--cooldown-every", type=float, default=0.0,
                    help="THERMAL: pause to cool after this many AUDIO seconds processed (0=off). On this "
                         "box sustained GPU+CPU load hard-reboots it (7800X3D + NVMe ASIC over-temp).")
    ap.add_argument("--cooldown", type=float, default=45.0,
                    help="THERMAL: seconds to idle (GPU+CPU quiescent) at each cooldown pause")
    ap.add_argument("--smartctl", help="path to smartctl.exe; enables the DRIVE-TEMP WATCHDOG (needs admin)")
    ap.add_argument("--smartctl-dev", default="/dev/sdb",
                    help="smartctl device of the drive to protect (P5=C: is /dev/sdb on this box)")
    ap.add_argument("--temp-pause", type=float, default=70.0, help="watchdog: pause when composite temp(C) >= this")
    ap.add_argument("--temp-hot-pause", type=float, default=82.0, help="watchdog: pause when hotspot(C) >= this")
    ap.add_argument("--temp-resume", type=float, default=64.0, help="watchdog: resume once composite cools to <= this")
    ap.add_argument("--temp-max-wait", type=float, default=900.0, help="watchdog: abort if not cooled within N sec")
    ap.add_argument("--temp-poll-every", type=int, default=1, help="watchdog: check drive temp every N steps")
    ap.add_argument("--allow-undercover", action="store_true",
                    help="accept a large audio-coverage gap instead of failing loud (over-fetch is fine; "
                         "use this only when you KNOW the text legitimately stops before the audio)")
    a = ap.parse_args()

    import torch, torchaudio
    dev = a.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"  device={dev}  window={a.window}s safety={a.safety}s sub={a.sub}s", file=sys.stderr)

    sentences = read_sentences(a.text)
    tokens = build_tokens(sentences)
    word_time = [None] * len(tokens)                 # filled: (s,e) ; or 'EMPTY'
    align_idx = [i for i, t in enumerate(tokens) if t["nw"]]   # indices of alignable tokens
    pos_in_align = 0                                  # cursor into align_idx

    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model(with_star=False).to(dev); model.train(False)
    tokenizer = bundle.get_tokenizer(); aligner = bundle.get_aligner()

    stream = AudioStream(a.audio, target_sr=bundle.sample_rate, block_sec=a.sub)
    win, safety, sr = a.window, a.safety, bundle.sample_rate
    grab = int(a.window * a.wps * a.overprovide)      # alignable words per step

    cool_anchor = 0.0                                 # audio-sec at last cooldown / resume / start
    if a.resume and a.checkpoint and (os.path.exists(a.checkpoint) or os.path.exists(a.checkpoint + ".bak")):
        c = load_ckpt(a.checkpoint)
        if c is None:
            print("  ! --resume requested but checkpoint AND .bak are both unusable; starting FRESH",
                  file=sys.stderr)
        else:
            pos_in_align = c["pos"]; word_time = c["word_time"]
            stream.fast_forward(c["audio_pos"]); cool_anchor = c["audio_pos"]
            print(f"  RESUMED from {os.path.basename(a.checkpoint)}: {pos_in_align}/{len(align_idx)} words, "
                  f"audio at {c['audio_pos']/60:.1f}min", file=sys.stderr)
    elif a.checkpoint and (os.path.exists(a.checkpoint) or os.path.exists(a.checkpoint + ".bak")):
        print(f"  ! a checkpoint exists at {a.checkpoint} but --resume was NOT passed -- starting FRESH "
              f"(it will be overwritten, then removed on success). Pass --resume to continue the prior run.",
              file=sys.stderr)

    step = 0
    while pos_in_align < len(align_idx):
        samples, is_final = stream.window(win)
        if len(samples) < sr * 0.5:                   # no audio left
            break
        wstart = stream.buf_start
        wlen = len(samples) / sr
        # collect up to `grab` alignable tokens from the cursor (carry display tokens too)
        a_take = align_idx[pos_in_align: pos_in_align + grab]
        if not a_take:
            break
        words = [tokens[i]["nw"] for i in a_take]
        emission = emission_for(model, dev, samples, a.sub, sr, torch)
        if emission is None or emission.size(1) == 0:    # degenerate tail -> no usable frames, stop
            break
        # CTC needs emission_frames >= target_tokens + repeats. At the END of the audio the buffer can't
        # refill to a full WINDOW, so the emission is SHORT while `grab` words is fixed -> the queued text
        # can tokenize to more tokens than the short tail can hold ("targets length is too long for CTC").
        # Trim the queued words to what fits; the trimmed-off tail stays queued (if the audio is truly
        # ending it's the expected leftover the coverage check reports, not a crash).
        spans, nfit = None, len(words)
        while nfit > 0:
            try:
                spans = aligner(emission[0], tokenizer(words[:nfit]))   # CPU
                break
            except Exception as e:                       # keep the FRAMED fail-loud message for any non-CTC
                msg = str(e)                             # aligner error (don't let a raw traceback escape)
                if "too long for CTC" not in msg:
                    sys.exit(f"aligner failed at step {step}: {e}")
                if nfit <= 1:                            # even ONE word won't fit this tiny tail emission ->
                    nfit = 0; break                      # give up this window (words stay queued; outer break)
                m = re.search(r"log_probs length:\s*(\d+).+?targets length:\s*(\d+).+?repeats:\s*(\d+)", msg)
                if m:
                    F, T, R = (int(x) for x in m.groups())
                    tpw = max(1.0, T / nfit)             # avg CTC tokens per queued word
                    drop = max(1, int((T + R - F) / tpw) + 2)
                else:
                    drop = max(1, nfit - int(nfit * 0.85))
                nfit = max(1, nfit - drop)               # nfit was >= 2, drop >= 1 -> STRICTLY decreases (no hang)
        if spans is None:                                # couldn't fit even one word -> no usable audio here
            break
        if nfit < len(a_take):                           # short tail held only a prefix; re-queue the rest
            print(f"  (step {step}: short tail window held {nfit}/{len(a_take)} queued words; rest re-queued)",
                  file=sys.stderr)
            a_take = a_take[:nfit]
        ratio = len(samples) / emission.size(1) / sr
        accept_thresh = (wstart + wlen) if is_final else (wstart + wlen - safety)

        last_committed_align = -1
        committed_end = wstart
        for k, ti in enumerate(a_take):
            if k >= len(spans): break
            sp = spans[k]
            s = wstart + sp[0].start * ratio
            e = wstart + sp[-1].end * ratio
            if e <= accept_thresh or is_final:
                word_time[ti] = (s, e)
                last_committed_align = pos_in_align + k
                committed_end = e
            else:
                break
        if last_committed_align < 0:                  # nothing trusted (e.g. window too small) -> force one
            if is_final: break
            # accept at least the first word to guarantee progress
            sp = spans[0]; s = wstart + sp[0].start*ratio; e = wstart + sp[-1].end*ratio
            word_time[a_take[0]] = (s, e); last_committed_align = pos_in_align; committed_end = e

        pos_in_align = last_committed_align + 1
        stream.commit(max(0.0, committed_end - wstart))
        step += 1
        if step % 20 == 0 or is_final:
            pct = 100 * pos_in_align / len(align_idx)
            print(f"  step {step:4d}  t={committed_end/60:6.1f}min  text {pct:5.1f}%  "
                  f"({pos_in_align}/{len(align_idx)} words)", file=sys.stderr)
        if a.checkpoint and step % a.checkpoint_every == 0:
            save_ckpt(a.checkpoint, pos_in_align, stream.buf_start, word_time)
        if a.cooldown_every and not is_final and (committed_end - cool_anchor) >= a.cooldown_every:
            if a.checkpoint:                          # durable checkpoint BEFORE we idle
                save_ckpt(a.checkpoint, pos_in_align, stream.buf_start, word_time)
            pct = 100 * pos_in_align / len(align_idx)
            print(f"  COOLDOWN t={committed_end/60:.1f}min ({pct:.1f}%): idling {a.cooldown:.0f}s "
                  f"(GPU+CPU quiescent to shed heat)", file=sys.stderr); sys.stderr.flush()
            time.sleep(a.cooldown)
            cool_anchor = committed_end
        if a.smartctl and step % a.temp_poll_every == 0:
            thermal_guard(a, lambda: save_ckpt(a.checkpoint, pos_in_align, stream.buf_start, word_time))
        if a.max_seconds and committed_end >= a.max_seconds:
            print(f"  (stopped at --max-seconds {a.max_seconds})", file=sys.stderr); break
        if is_final: break

    # ---- assemble segments (group tokens by sentence; fill punctuation-only from neighbours) ----
    seg_tokens = {}
    for i, t in enumerate(tokens):
        seg_tokens.setdefault(t["seg"], []).append(i)
    # Trim TRAILING sentences whose audio never played (audio ran out before the text): otherwise they
    # become zero-width "ghost" segments at the audio end that split_tracks would pile onto the final track.
    last_real_seg = max((tokens[i]["seg"] for i, wt in enumerate(word_time) if isinstance(wt, tuple)),
                        default=-1)
    segments, last_end = [], 0.0
    for si in range(last_real_seg + 1):
        idxs = seg_tokens.get(si, [])
        seed = next((word_time[i][0] for i in idxs if isinstance(word_time[i], tuple)), last_end)
        prev = seed; words_out = []
        for i in idxs:
            wt = word_time[i]
            if isinstance(wt, tuple):
                s, e = wt; prev = e
            else:
                s = e = prev                          # unaligned/punctuation: zero-width at prev
            words_out.append({"w": tokens[i]["w"], "s": round(s, 3), "e": round(e, 3)})
        start = words_out[0]["s"] if words_out else last_end
        end = max((w["e"] for w in words_out), default=last_end)
        last_end = max(last_end, end)
        segments.append({"id": si, "start": round(start, 3), "end": round(end, 3),
                         "text": sentences[si], "words": words_out})

    doc = {"title": a.title, "audio": os.path.basename(a.audio[0]), "segments": segments}
    if a.chapters:
        with open(a.chapters, encoding="utf-8") as _cf:
            markers = json.load(_cf)
        chs = []
        for m in markers:
            seg = m.get("seg", m.get("first_line"))
            if seg is None or not (0 <= seg < len(segments)): continue
            chs.append({"title": (m.get("title") or "Chapter").strip(),
                        "start": segments[seg]["start"], "seg": seg})
        if chs: doc["chapters"] = chs

    validate_doc(doc, source=a.out)        # fail loud on a malformed envelope before writing
    with open(a.out, "w", encoding="utf-8") as _of:
        json.dump(doc, _of, ensure_ascii=False, indent=2)
    if a.checkpoint:                            # completed cleanly; drop EVERY resume file so a later
        for _ck in (a.checkpoint, a.checkpoint + ".bak", a.checkpoint + ".tmp"):   # rerun starts FRESH,
            try: os.remove(_ck)                 # not from a stale .bak (load_ckpt accepts .bak).
            except OSError: pass
    naligned = sum(1 for w in word_time if isinstance(w, tuple))
    print(f"Wrote {a.out}: {len(segments)} sentences, {naligned}/{len(align_idx)} words aligned, "
          f"{len(doc.get('chapters', []))} chapters, {last_end/60:.1f} min.", file=sys.stderr)

    # ---- belt-and-suspenders: never finish silently under-covered (anti-Goodhart fail-loud) ----
    import soundfile as _sf
    total_audio = sum(_sf.info(f).duration for f in a.audio)
    text_exhausted = pos_in_align >= len(align_idx)     # ran out of TEXT (vs audio) ?
    audio_gap = total_audio - last_end
    undercovered = False
    if naligned == 0:
        sys.exit("FAIL: 0 words aligned — check the audio/text inputs.")
    if a.max_seconds:
        pass                                            # debug cap; coverage check N/A
    elif text_exhausted and audio_gap > max(120.0, 0.02 * total_audio):
        print(f"\n*** COVERAGE GAP — FAILING LOUD ***\n"
              f"    text ran out at {last_end/60:.1f} min, but the audio is {total_audio/60:.1f} min "
              f"({audio_gap/60:.1f} min / {100*audio_gap/total_audio:.0f}% UNALIGNED).\n"
              f"    Most likely the fetched text is MISSING CHAPTERS past where it ends.\n"
              f"    Fetch the rest and re-run, or pass --allow-undercover if this is intended.",
              file=sys.stderr)
        undercovered = not a.allow_undercover
    elif not text_exhausted:                            # audio ran out first → leftover text is fine
        unused = len(align_idx) - pos_in_align
        if unused:
            print(f"    note: audio fully covered ({last_end/60:.1f} min); {unused} words of over-fetched "
                  f"text beyond the audiobook's end were unused (expected, not an error).", file=sys.stderr)
    if undercovered:
        sys.exit(3)                                     # distinct nonzero so a caller/cron sees the gap

if __name__ == "__main__":
    main()
