#!/usr/bin/env python3
"""
m4b_common.py -- shared helpers for the .m4b read-along flow (probe_m4b / m4b_cut / m4b_make_units).

ffmpeg/ffprobe are NOT a hard dependency of this repo -- Book 12 (mp3) decodes fine via soundfile.
They're needed only for .m4b audiobooks, whose AAC libsndfile can't read. Resolution order for each
binary: the FFMPEG / FFPROBE environment variable, then PATH, then the copy Shotcut bundles on Windows
(`C:\\Program Files\\Shotcut\\`). Override with the env var or a script's --ffmpeg/--ffprobe flag.
"""
import json, os, shutil, subprocess, sys


def _resolve(name, env):
    return os.environ.get(env) or shutil.which(name) or rf"C:\Program Files\Shotcut\{name}.exe"


FFMPEG = _resolve("ffmpeg", "FFMPEG")
FFPROBE = _resolve("ffprobe", "FFPROBE")


def require(binpath, env):
    """Fail loud NOW if a needed binary isn't runnable, not with a cryptic error mid-cut."""
    if shutil.which(binpath) or os.path.isfile(binpath):
        return binpath
    sys.exit(f"required binary not found: {binpath!r}. Put ffmpeg/ffprobe on PATH or set the {env} "
             f"env var (on Windows, Shotcut bundles them under C:\\Program Files\\Shotcut\\).")


def ffprobe_chapters(m4b, ffprobe=None):
    """[(start_seconds, title), ...] for every embedded chapter mark, via `ffprobe -show_chapters`.
    Robust to the moov atom living at the file TAIL (a raw `chpl`-box head scan misses those)."""
    ffprobe = require(ffprobe or FFPROBE, "FFPROBE")
    out = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_chapters", m4b],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("ffprobe failed: " + (out.stderr or "")[:300])
    chs = json.loads(out.stdout).get("chapters", [])
    if not chs:
        sys.exit(f"no chapter marks found in {m4b!r}")
    return [(float(c["start_time"]), c.get("tags", {}).get("title", f"id{c['id']}")) for c in chs]
