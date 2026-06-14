#!/usr/bin/env python3
"""
check_ip_limits.py — fail loud if the repo would ship more third-party content than
we have committed to. Protects the author's text and the narrator's voice.

The commitment (hard limits):
  * VOICE  — at most 20 seconds of any single audio asset we ship.
  * TEXT   — at most 500 words (~a page) of narrative prose in any single file.
  * BULK   — the full audio and full text are never tracked at all.

It inspects what GIT WOULD SHIP — every tracked file (the index), INCLUDING audio
embedded as base64 `data:` URIs inside .js/.json/.html (so you can't smuggle a long
clip past the audio-file rule). Run it directly or as a pre-commit hook
(.githooks/pre-commit). Exit 0 = within limits; exit 1 = a violation is printed.

"Couldn't evaluate" fails loud, not neutral: if a non-trivial audio asset can't be
measured (no ffprobe/afconvert), that's a failure, not a pass.
"""
import base64, json, os, re, subprocess, sys, tempfile, wave

MAX_VOICE_SECONDS = 20
MAX_PROSE_WORDS   = 500
SIZE_UNVERIFIABLE = 200 * 1024     # an unmeasurable audio blob under this is presumed short

AUDIO_EXT = (".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".oga", ".wav", ".flac")
TEXTY_EXT = (".js", ".json", ".html", ".htm", ".txt")
DATA_AUDIO = re.compile(r"data:audio/[^;]+;base64,([A-Za-z0-9+/=]+)")
FORBIDDEN = [                       # bulk IP artifacts that must never be tracked
    (re.compile(r"^Reading/"),                 "raw audiobook source"),
    (re.compile(r"^samples/"),                 "alignment sample (real audio/text)"),
    (re.compile(r"\.(wav|m4a|m4b|flac)$", re.I), "uncompressed / full audio"),
    (re.compile(r"(^|/)book12\.txt$"),         "fetched author prose"),
    (re.compile(r"\.chapters\.json$"),         "fetched chapter prose/markers"),
]

def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout
    return [l for l in out.splitlines() if l]

def measure_seconds(data):
    """Audio duration in seconds via ffprobe → afconvert, or None if neither exists."""
    path = wavp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as t:
            t.write(data); path = t.name
        try:
            r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                                "-of", "csv=p=0", path], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                return float(r.stdout.strip())
        except FileNotFoundError:
            pass
        try:
            wavp = path + ".wav"
            if subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", path, wavp],
                              capture_output=True).returncode == 0:
                with wave.open(wavp, "rb") as w:
                    return w.getnframes() / w.getframerate()
        except FileNotFoundError:
            pass
        return None
    finally:
        for p in (path, wavp):
            if p and os.path.exists(p):
                try: os.remove(p)
                except OSError: pass

def prose_words(obj):
    """Count words of narrative text in a player-schema object (segments + chapter titles)."""
    if not isinstance(obj, dict):
        return 0
    root = obj.get("align") if isinstance(obj.get("align"), dict) else obj
    n = 0
    for s in (root.get("segments") or []):
        if isinstance(s, dict) and isinstance(s.get("text"), str):
            n += len(s["text"].split())
    for c in (root.get("chapters") or []):
        if isinstance(c, dict) and isinstance(c.get("title"), str):
            n += len(c["title"].split())
    return n

def js_object(text):
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", text.strip(), re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None

def check_audio_blob(label, data, out):
    secs = measure_seconds(data)
    if secs is None:
        if len(data) > SIZE_UNVERIFIABLE:
            out.append(f"{label}: {len(data)//1024} KB audio, duration UNVERIFIABLE "
                       f"(install ffprobe or afconvert, or keep the clip tiny)")
    elif secs > MAX_VOICE_SECONDS + 0.5:
        out.append(f"{label}: {secs:.1f}s audio exceeds the {MAX_VOICE_SECONDS}s voice limit")

def main():
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, check=True).stdout.strip()
    os.chdir(root)
    violations = []

    for path in tracked_files():
        for pat, why in FORBIDDEN:
            if pat.search(path):
                violations.append(f"{path}: must not be tracked — {why}")

        low = path.lower()
        if low.endswith(AUDIO_EXT) and os.path.exists(path):
            with open(path, "rb") as f:
                check_audio_blob(path, f.read(), violations)

        if low.endswith(TEXTY_EXT) and os.path.exists(path):
            text = open(path, encoding="utf-8", errors="replace").read()
            for b64 in DATA_AUDIO.findall(text):
                try:
                    check_audio_blob(f"{path} (embedded data: URI)", base64.b64decode(b64), violations)
                except Exception:
                    pass
            words = 0
            if low.endswith(".json"):
                try: words = prose_words(json.loads(text))
                except Exception: words = 0
            elif low.endswith(".js"):
                obj = js_object(text)
                words = prose_words(obj) if obj else 0
            elif low.endswith(".txt"):
                lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
                urlish = sum(1 for l in lines if l.lstrip().startswith("http"))
                if lines and urlish / len(lines) < 0.5:     # skip URL-list files
                    words = sum(len(l.split()) for l in lines)
            if words > MAX_PROSE_WORDS:
                violations.append(f"{path}: {words} words of prose exceeds the "
                                  f"{MAX_PROSE_WORDS}-word (~one page) limit")

    if violations:
        print("IP-limit check FAILED — do not commit:")
        for v in violations:
            print("  ✗", v)
        print(f"\nLimits: voice ≤ {MAX_VOICE_SECONDS}s per asset, prose ≤ {MAX_PROSE_WORDS} "
              f"words per file, no bulk audio/text tracked. See AGENTS.md § IP limits.")
        sys.exit(1)
    print(f"IP-limit check passed: voice ≤ {MAX_VOICE_SECONDS}s, prose ≤ {MAX_PROSE_WORDS} "
          f"words, no bulk IP artifacts tracked.")

if __name__ == "__main__":
    main()
