#!/usr/bin/env python3
"""
align_chapters.py -- per-chapter ANCHORED alignment supervisor.

A single continuous greedy pass over a whole edited audiobook accumulates edit-divergence drift
(the official audiobook trims/reorders vs the web prose) into a large end-of-book lag. Fix: align
each chapter INDEPENDENTLY against its own contiguous audio-track group, so the lag resets to zero
at every chapter boundary. This RELIES on track boundaries falling on chapter boundaries (no track
straddling two chapters) -- verify with probe_track_starts.py, which is how the track map is built.

Each chapter is its own align_book.py subprocess. Belt-and-suspenders:
  * divisible + resumable -- a chapter's output JSON is written only on clean completion, so a
    present + schema-valid file that is NEWER than its inputs (track map, text, audio) means done ->
    skip it. Editing the track map or text (moving a chapter's boundary) re-aligns the affected
    chapter rather than reusing a stale-but-valid JSON; --force re-aligns all;
  * thermal-safe -- the child EXITS before the next starts (natural GPU/CPU breather), align_book's
    own --cooldown-every sheds heat inside long chapters, and we idle --between-cooldown between them;
  * fail-loud preflight (refuse a 2nd concurrent GPU job; abort if the child torch is CPU-only).

Chapter->track correspondence + per-chapter text slices come from the shared track map (--track-map;
see book12_track_map.json) joined with --text. Launch with the ROCm interpreter so children inherit
GPU torch:
    py -3.12 align_chapters.py --audio-glob "...\\*.mp3" --text book12_audiobook.txt --outdir per_chapter
"""
import argparse, glob, json, os, re, subprocess, sys, time

from schema import validate_doc, SchemaError

HERE = os.path.dirname(os.path.abspath(__file__))
ALIGN = os.path.join(HERE, "align_book.py")


_KEEP = re.compile(r"[^a-z']")
def n_alignable(sentences):
    """Count tokens align_book would actually align (letters/apostrophes), to derive a chapter's wps."""
    return sum(1 for s in sentences for w in s.split() if _KEEP.sub("", w.lower()).strip("'"))


def slug(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-") or "chapter"


def track_no(path):
    m = re.search(r"(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else None


def load_track_map(path):
    with open(path, encoding="utf-8") as f:
        tm = json.load(f)
    if not isinstance(tm, list) or not tm:
        sys.exit(f"track map {path} is not a non-empty list")
    seen = set()
    for i, e in enumerate(tm):
        if not (isinstance(e, dict) and isinstance(e.get("tracks"), list) and e["tracks"]
                and isinstance(e.get("seg"), int)):
            sys.exit(f"track map entry {i} malformed (need title, int seg, non-empty tracks): {e!r}")
        for t in e["tracks"]:                    # no track may belong to two chapters (else anchoring is wrong)
            if t in seen:
                sys.exit(f"track {t} appears in more than one chapter -- a track straddles a boundary?")
            seen.add(t)
    return tm


def preflight(device):
    """Fail loud BEFORE a long run: refuse a 2nd concurrent GPU job, and abort if the child torch is
    CPU-only (the shebang->Store-3.13 trap = ~30x slower, ~19GB RAM). Mirrors run_book_chunked.py.

    The 'never two model loads' guard counts python* processes. Some setups run KNOWN-SAFE non-GPU
    helper pythons alongside an align -- e.g. a console-less pythonw launcher (so a stray Ctrl-C from
    another console can't kill the run) and a CPU-only temperature watchdog. Set WI_PREFLIGHT_MAX_PY
    to the number of python* you've accounted for (default 1 = the align itself) so those don't trip
    the guard. The torch-probe below still independently proves THIS child is the GPU job."""
    max_py = int(os.environ.get("WI_PREFLIGHT_MAX_PY", "1"))
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Name 'python*' -ErrorAction SilentlyContinue | Measure-Object).Count"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        n = int(out or "0")
    except Exception:
        n = 0
    if n > max_py:
        print(f"PREFLIGHT ABORT: {n} python processes already running (> WI_PREFLIGHT_MAX_PY="
              f"{max_py}) -- refuse a 2nd GPU job (never two model loads at once). Kill strays first.",
              file=sys.stderr)
        return False
    probe = subprocess.run(
        [sys.executable, "-c", "import torch,sys; sys.stdout.write(f'{torch.__version__}|{torch.cuda.is_available()}')"],
        capture_output=True, text=True, timeout=180)
    print(f"  child interpreter : {sys.executable}", flush=True)
    print(f"  torch probe       : {probe.stdout.strip() or probe.stderr.strip()[:200]}", flush=True)
    if device != "cpu" and "|True" not in probe.stdout:
        print("PREFLIGHT ABORT: child torch reports CUDA/ROCm NOT available. Launch with py -3.12.", file=sys.stderr)
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-glob", required=True, help="glob for all audiobook tracks (numbered NN - ...)")
    ap.add_argument("--text", required=True, help="sentence-per-line prose for the WHOLE book")
    ap.add_argument("--track-map", default=os.path.join(HERE, "book12_track_map.json"),
                    help="JSON list of {title, seg, tracks[]} (one per chapter, in book order)")
    ap.add_argument("--outdir", default="per_chapter")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--wps", type=float, default=2.5, help="passed through to align_book (narration words/sec)")
    ap.add_argument("--auto-wps", action="store_true",
                    help="compute EACH chapter's wps = alignable_words / audio_seconds (overrides --wps). A "
                         "chapter narrated faster/slower than --wps drifts badly over its length; the true "
                         "average pace is known exactly, so use it. Needs soundfile-readable audio (wav/mp3).")
    ap.add_argument("--overprovide", type=float, default=1.0, help="passed through to align_book")
    ap.add_argument("--cooldown-every", type=float, default=1800.0, help="align_book in-chapter thermal pause cadence")
    ap.add_argument("--cooldown", type=float, default=30.0, help="align_book in-chapter cooldown seconds")
    ap.add_argument("--between-cooldown", type=float, default=45.0, help="idle seconds between chapters")
    ap.add_argument("--smartctl", help="path to smartctl.exe -> passed to align_book's drive-temp watchdog")
    ap.add_argument("--smartctl-dev", default="/dev/sdb")
    ap.add_argument("--allow-undercover", action="store_true",
                    help="pass through to align_book: accept a chapter whose text legitimately ends before "
                         "its audio (otherwise a >2%% audio-coverage gap fails the run -- a missing text "
                         "slice or wrong track map shouldn't pass as 'done')")
    ap.add_argument("--dry-run", action="store_true", help="print the per-chapter plan and exit (no GPU)")
    ap.add_argument("--force", action="store_true",
                    help="re-align every chapter, ignoring up-to-date checks (otherwise a chapter whose "
                         "output is newer than all its inputs -- track map, text, audio -- is skipped)")
    a = ap.parse_args()

    tmap = load_track_map(a.track_map)
    with open(a.text, encoding="utf-8") as f:
        sents = [ln.strip() for ln in f if ln.strip()]
    by_no = {}
    for p in glob.glob(a.audio_glob):
        n = track_no(p)
        if n is not None:
            by_no[n] = p
    for e in tmap:
        for t in e["tracks"]:
            if t not in by_no:
                sys.exit(f"--audio-glob matched no file for track {t:02d}")

    bounds = [e["seg"] for e in tmap] + [len(sents)]
    if bounds != sorted(bounds):
        sys.exit(f"track-map seg values are not ascending / exceed the text length: {bounds}")
    os.makedirs(a.outdir, exist_ok=True)

    plan = []
    for i, e in enumerate(tmap):
        s0, s1 = bounds[i], bounds[i + 1]
        plan.append((i, e["title"], e["tracks"], s0, s1))

    print(f"=== {len(plan)} chapters, {sum(len(e['tracks']) for e in tmap)} tracks ===", flush=True)
    if a.dry_run:
        for i, title, tracks, s0, s1 in plan:
            print(f"  [{i:02d}] {title:32s} tracks {'+'.join('%02d' % t for t in tracks):14s} "
                  f"sentences {s0}..{s1} ({s1-s0})", flush=True)
        print("(dry run -- no alignment performed)", flush=True)
        return
    if a.device != "cpu" and not preflight(a.device):
        sys.exit(2)

    t_run0 = time.time()
    done, skipped = 0, 0
    # An existing chapter output counts as "done" only if it is NEWER than all of its inputs: the track
    # map + text define this chapter's boundaries/slice, the audio is the source. So editing the track map
    # (a seg bound or track list) or the text RE-ALIGNS the affected chapter instead of silently reusing a
    # stale-but-schema-valid JSON. --force re-aligns regardless.
    base_mtime = max(os.path.getmtime(a.track_map), os.path.getmtime(a.text))
    for i, title, tracks, s0, s1 in plan:
        out = os.path.join(a.outdir, f"chap{i:02d}_{slug(title)}.json")
        txt = os.path.join(a.outdir, f"chap{i:02d}_{slug(title)}.txt")
        audio = [by_no[t] for t in tracks]
        inputs_mtime = max([base_mtime] + [os.path.getmtime(f) for f in audio])
        if not a.force and os.path.exists(out) and os.path.getmtime(out) >= inputs_mtime:
            try:
                with open(out, encoding="utf-8") as f:
                    validate_doc(json.load(f), source=out)
                print(f"[{i+1:02d}/{len(plan)}] SKIP {title} (up-to-date)", flush=True)
                skipped += 1
                continue
            except (SchemaError, ValueError, OSError) as e:
                print(f"[{i+1:02d}/{len(plan)}] re-run {title}: existing output invalid ({e})", flush=True)

        with open(txt, "w", encoding="utf-8") as f:
            f.write("\n".join(sents[s0:s1]))
        wps = a.wps
        if a.auto_wps:
            import soundfile as sf
            asec = sum(sf.info(f).duration for f in audio)
            nw = n_alignable(sents[s0:s1])
            if asec > 0 and nw > 0:
                wps = round(nw / asec, 3)
        print(f"\n[{i+1:02d}/{len(plan)}] ALIGN {title} ({s1-s0} sentences -> tracks "
              f"{'+'.join('%02d' % t for t in tracks)})"
              f"{(' | auto-wps %.3f' % wps) if a.auto_wps else ''}", flush=True)
        cmd = [sys.executable, ALIGN, "--audio", *audio, "--text", txt, "--out", out,
               "--title", title, "--device", a.device,
               "--wps", str(wps), "--overprovide", str(a.overprovide),
               "--cooldown-every", str(a.cooldown_every), "--cooldown", str(a.cooldown)]
        if a.smartctl:
            cmd += ["--smartctl", a.smartctl, "--smartctl-dev", a.smartctl_dev]
        if a.allow_undercover:
            cmd += ["--allow-undercover"]
        t0 = time.time()
        rc = subprocess.run(cmd).returncode
        dt = (time.time() - t0) / 60
        if rc == 0:
            print(f"     OK   {title} in {dt:.1f} min", flush=True); done += 1
        elif rc == 3:
            # COVERAGE GAP: align_book found >2% of THIS chapter's audio has no text aligned to it (a
            # missing text slice or a wrong track map), and it wrote a file BEFORE failing. Do NOT accept
            # it as done -- delete that file so a re-run RE-ATTEMPTS instead of silently skipping a gapped
            # chapter (a present+valid JSON would otherwise be skipped), then fail loud. Pass
            # --allow-undercover only if the text legitimately ends before the audio -> align_book then
            # exits 0 and we never reach here.
            try:
                os.remove(out)
            except OSError:
                pass
            sys.exit(f"     FAIL {title}: align_book COVERAGE GAP (exit 3) -- >2% of the audio has NO text "
                     f"(missing slice / wrong track map?). Removed {os.path.basename(out)}. Fix the inputs, or "
                     f"pass --allow-undercover if the text legitimately ends before the audio. Re-run resumes.")
        else:
            sys.exit(f"     FAIL {title}: align_book exit {rc} -- aborting (fix and re-run; finished chapters skip)")
        time.sleep(a.between_cooldown)

    print(f"\n=== DONE: {done} aligned, {skipped} skipped, {len(plan)} total in {(time.time()-t_run0)/60:.1f} min ===",
          flush=True)


if __name__ == "__main__":
    main()
