#!/usr/bin/env python3
"""run_book_chunked.py - thermal-aware supervisor for align_book.py on a box that HARD-REBOOTS
under sustained load (diagnosed 2026-06-15: thermal trip of the 7800X3D + an NVMe ASIC, NOT the GPU;
all Kernel-Power 41 events had BugcheckCode=0 with no WHEA/storage errors = instant power-off).

What it does:
  * launches align_book.py with IN-PROCESS cooldown pauses (short compute bursts, GPU+CPU idle
    between them) so the box never heat-soaks into a trip;
  * points the crash-DURABLE checkpoint at the COOL system drive (C:), NOT the hot SN850X work
    drive (D:), so checkpoint writes stop feeding the NVMe ASIC we're trying to protect;
  * is IDEMPOTENT: re-run it after a reboot and it RESUMES from the checkpoint (fail loud if the
    checkpoint can't advance);
  * streams the aligner's progress to a log so the long run stays monitorable.

Belt-and-suspenders: divisible (cooldown bursts) + monitorable (live log + % done) + pausable/
resumable (durable ckpt, inured to a mid-run reboot).

LAUNCH WITH THE ROCm INTERPRETER so the child inherits GPU torch (NOT the bare `py` shebang, which
lands on the Store 3.13 CPU torch):
    py -3.12 run_book_chunked.py --audio-glob "...\\*.mp3" --text ... --out ... --checkpoint C:\\...
"""
import argparse, glob, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ALIGN = os.path.join(HERE, "align_book.py")


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def audio_files(glob_pat, skip_first, skip_last):
    fs = sorted(glob.glob(glob_pat), key=natural_key)
    if skip_last:
        fs = fs[:len(fs) - skip_last]
    if skip_first:
        fs = fs[skip_first:]
    return fs


def total_duration(files):
    import soundfile as sf
    return sum(sf.info(f).duration for f in files)


def ckpt_progress(ckpt, total_sec):
    """Best-effort read of audio_pos from the durable checkpoint (or its .bak)."""
    import pickle
    for p in (ckpt, ckpt + ".bak"):
        try:
            with open(p, "rb") as f:
                d = pickle.load(f)
            if isinstance(d, dict) and "audio_pos" in d:
                ap = d["audio_pos"]
                return ap, (100.0 * ap / total_sec if total_sec else 0.0)
        except Exception:
            continue
    return None, 0.0


def preflight(device):
    """Fail loud BEFORE a multi-hour run if (a) another model job is already running, or (b) the
    child interpreter has CPU-only torch (the shebang->Store-3.13 trap = a ~30x-slower, 19GB-RAM run)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Name 'python*' -ErrorAction SilentlyContinue | Measure-Object).Count"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        n = int(out or "0")
    except Exception:
        n = 0
    if n > 1:  # this supervisor is one of them; >1 means another python is live
        print(f"PREFLIGHT ABORT: {n} python processes already running - refuse to start a 2nd GPU job "
              f"(CLAUDE.md: never two model loads at once). Kill strays first.", file=sys.stderr)
        return False
    probe = subprocess.run(
        [sys.executable, "-c", "import torch,sys; sys.stdout.write(f'{torch.__version__}|{torch.cuda.is_available()}')"],
        capture_output=True, text=True, timeout=180)
    info = probe.stdout.strip()
    print(f"  child interpreter : {sys.executable}")
    print(f"  torch probe       : {info or probe.stderr.strip()[:200]}")
    if device != "cpu" and "|True" not in info:
        print("PREFLIGHT ABORT: child torch reports CUDA/ROCm NOT available. A CPU run would be ~30x "
              "slower and eat ~19GB RAM. Launch the supervisor with  py -3.12 . Aborting.", file=sys.stderr)
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio-glob", required=True)
    ap.add_argument("--skip-first", type=int, default=1, help="drop N leading tracks (default 1 = credits)")
    ap.add_argument("--skip-last", type=int, default=0)
    ap.add_argument("--text", required=True)
    ap.add_argument("--chapters")
    ap.add_argument("--title", default="The Wandering Inn 12: The Witch of Webs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--checkpoint", required=True, help="durable ckpt path on the COOL drive (C:, not D:)")
    ap.add_argument("--log", help="append child output here too (default: <checkpoint>.log)")
    ap.add_argument("--cooldown-every", type=float, default=1200.0)
    ap.add_argument("--cooldown", type=float, default=45.0)
    ap.add_argument("--checkpoint-every", type=int, default=12)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--max-relaunch", type=int, default=4, help="relaunch attempts after a non-clean child exit")
    ap.add_argument("--smartctl", help="path to smartctl.exe to enable the drive-temp watchdog (run elevated)")
    ap.add_argument("--smartctl-dev", default="/dev/sdb", help="smartctl device of the drive to protect")
    a = ap.parse_args()

    log_path = a.log or (a.checkpoint + ".log")
    LF = open(log_path, "a", encoding="utf-8")

    def note(msg):
        line = f"[supervisor {time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        LF.write(line + "\n"); LF.flush()

    files = audio_files(a.audio_glob, a.skip_first, a.skip_last)
    if not files:
        sys.exit("no audio files matched --audio-glob")
    total = total_duration(files)

    note(f"book realign: {len(files)} tracks, {total/3600:.2f}h audio  ->  {os.path.basename(a.out)}")
    note(f"first track: {os.path.basename(files[0])[-16:]}  last: {os.path.basename(files[-1])[-16:]}")
    note(f"checkpoint (COOL drive): {a.checkpoint}")
    note(f"thermal: idle {a.cooldown:.0f}s every {a.cooldown_every/60:.0f}min audio  |  log: {log_path}")
    if not preflight(a.device):
        sys.exit(2)
    if a.smartctl:
        comp = None
        try:
            o = subprocess.run([a.smartctl, "-x", a.smartctl_dev], capture_output=True, text=True, timeout=20).stdout
            for ln in o.splitlines():
                m = re.match(r"\s*Temperature:\s+(\d+)\s*Celsius", ln)
                if m:
                    comp = int(m.group(1))
        except Exception:
            comp = None
        if comp is None:
            note("WATCHDOG OFF: smartctl couldn't read the drive (not elevated?). Run is OPEN-LOOP "
                 "(fixed cooldowns only) — for pre-cooling-fix safety, launch from an Admin PowerShell.")
        else:
            note(f"watchdog ACTIVE: protecting {a.smartctl_dev}, composite now {comp}C")

    ap0, pct0 = ckpt_progress(a.checkpoint, total)
    if ap0 is not None:
        note(f"existing checkpoint found -> RESUMING at {ap0/60:.1f}min ({pct0:.1f}%)")

    child = [sys.executable, ALIGN,
             "--audio", *files,
             "--text", a.text,
             "--out", a.out,
             "--title", a.title,
             "--device", a.device,
             "--checkpoint", a.checkpoint,
             "--checkpoint-every", str(a.checkpoint_every),
             "--cooldown-every", str(a.cooldown_every),
             "--cooldown", str(a.cooldown),
             "--resume"]
    if a.smartctl:
        child += ["--smartctl", a.smartctl, "--smartctl-dev", a.smartctl_dev]
    if a.chapters:
        child += ["--chapters", a.chapters]

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}  # so child progress streams live, not in big blocks
    attempts = 0
    last_ap = ap0 if ap0 is not None else -1.0
    while True:
        note(f"launching aligner{(' (attempt %d)' % (attempts + 1)) if attempts else ''} ...")
        proc = subprocess.Popen(child, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                text=True, bufsize=1, env=env)
        for line in proc.stderr:
            sys.stdout.write(line); sys.stdout.flush()
            LF.write(line); LF.flush()
        proc.wait()
        ec = proc.returncode
        note(f"aligner exited with code {ec}")

        if ec == 0:
            note(f"DONE - clean. Output: {a.out}")
            note("NEXT: split into per-track sync files, then copy the complete set to the Dropbox book-12 folder.")
            return
        if ec == 3:
            note("DONE - but align_book reported a COVERAGE GAP (text ran out before the audio). Output WAS "
                 "written; see the gap size above. Likely the fetched text is short a chapter at the end.")
            return

        # Non-clean exit. A real thermal TRIP kills this supervisor too, so reaching here means the CHILD
        # died but the box survived (transient ROCm error / OOM), OR the user re-ran us after a reboot.
        ap_now, pct_now = ckpt_progress(a.checkpoint, total)
        progressed = ap_now is not None and ap_now > last_ap + 1.0
        note(f"checkpoint now at "
             f"{('%.1fmin (%.1f%%)' % (ap_now/60, pct_now)) if ap_now is not None else 'NONE'}; "
             f"{'progress made' if progressed else 'NO new progress'}")
        attempts += 1
        if attempts > a.max_relaunch:
            sys.exit(f"ABORT: aligner failed {attempts} times (last code {ec}). See {log_path}.")
        if not progressed and attempts >= 2:
            sys.exit("ABORT: aligner exited non-clean with NO forward progress twice - not a transient. "
                     f"See {log_path}.")
        last_ap = ap_now if ap_now is not None else last_ap
        note(f"cooling {a.cooldown:.0f}s, then resuming ...")
        time.sleep(a.cooldown)


if __name__ == "__main__":
    main()
