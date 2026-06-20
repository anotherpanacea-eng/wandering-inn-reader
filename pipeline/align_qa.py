#!/usr/bin/env python3
"""
align_qa.py -- Alignment QA Workbench (spec: spec-qa-workbench.md).

verify_tracks.py is a good ship/don't-ship CI signal but too SPARSE to localize drift (5 points/track
steps over a 1-2 min wander) and offers NO correction path. This extends it into a workbench:
DENSE drift profiling -> flagged-segment report -> a cheap wps pre-screen -> manual correction -> re-emit.

Three subcommands (ONE process, ONE model load whenever ASR is involved):

    py -3.12 align_qa.py profile   ...   # dense ASR drift profile + flagged segments + viz  [GPU]
    py -3.12 align_qa.py wps-check ...   # flag auto-wps outliers (the wrong-boundary tell)   [NO GPU]
    py -3.12 align_qa.py correct   ...   # apply an operator correction file -> re-emit JSON  [GPU for split_realign]

Scoring REUSES verify_tracks.py verbatim (asr / aligned_window / words_of / overlap) so a QA score
means exactly what the gate means; wps REUSES align_chapters.n_alignable; correct GENERALIZES
book17_split_strat + book17_recombine_strat. See AGENTS.md and the spec for the contract.

GPU/thermal/IP guards (spec sec.7): one model load at a time; run align_chapters.preflight() before any
GPU op; print torch build + cuda.is_available() and FAIL LOUD if CPU-only; plumb --cooldown-every /
--cooldown / --smartctl; cap every prose snippet (--snippet, default 150); keep HTML/JSON/report output
OUT of git (QA scratch, written to a LOCAL non-Dropbox path). model.train(False), never the eval call.
"""
import argparse, glob, json, math, os, re, statistics, subprocess, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8"); sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from schema import validate_doc, SchemaError  # noqa: E402
# Reuse the aligner-side helpers so the math is IDENTICAL to the production pipeline (no forks).
from align_chapters import n_alignable, preflight, slug, ALIGN  # noqa: E402

# ---- scoring primitives, REUSED VERBATIM from verify_tracks.py so QA == the gate -----------------
_KEEP = re.compile(r"[^a-z']")
def norm(w): return _KEEP.sub("", w.lower()).strip("'")
def words_of(text): return [w for w in (norm(x) for x in text.split()) if w]


def track_no(s):
    m = re.search(r"(\d+)", os.path.basename(s))
    return m.group(1) if m else None


def aligned_window(segs, t0, t1):
    """The aligned text covering [t0,t1] (segments whose end>=t0 and start<=t1; nearest if none).
    Identical to verify_tracks.aligned_window."""
    cov = [s for s in segs if s["end"] >= t0 and s["start"] <= t1]
    if not cov:
        cov = [min(segs, key=lambda s: abs((s["start"] + s["end"]) / 2 - t0))]
    return " ".join(s["text"] for s in cov)


def overlap_of(atext, heard):
    """The drift scalar: |aligned ^ heard| / |aligned|. Aligned-word denominator, set overlap.
    Identical meaning to verify_tracks (only the sampling density differs)."""
    aw, hw = set(words_of(atext)), set(words_of(heard))
    return (len(aw & hw) / len(aw)) if aw else 0.0


def rolling_median(vals, k):
    """Centered rolling median over k windows (odd k preferred). Smooths single-window noise so a
    SUSTAINED low run (real drift) separates from an isolated dip (a name, a quiet beat)."""
    if k <= 1:
        return list(vals)
    half = k // 2
    out = []
    for i in range(len(vals)):
        lo = max(0, i - half); hi = min(len(vals), i + half + 1)
        out.append(statistics.median(vals[lo:hi]))
    return out


# ======================================================================================
# profile -- dense ASR drift profiling + flagged-segment report + visualization  [GPU]
# ======================================================================================
def make_asr(a):
    """Load WAV2VEC2_ASR_BASE_960H once and return (asr_fn, device). model.train(False) per the
    safe-pattern lint. FAILS LOUD if the GPU was requested but torch is CPU-only (a CPU sweep of
    300+ windows is unusably slow) -- mirrors verify_tracks' device print + the spec sec.7 rule."""
    import torch, torchaudio
    import soundfile as sf
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"  torch {torch.__version__}  cuda.is_available()={torch.cuda.is_available()}  device={dev}",
          flush=True)
    if dev == "cpu" and not a.allow_cpu:
        sys.exit("FAIL: torch reports CPU-only (no CUDA/ROCm). A dense ASR sweep on CPU is unusably slow. "
                 "Launch with `py -3.12` (ROCm), or pass --allow-cpu to force it anyway.")
    bundle = torchaudio.pipelines.WAV2VEC2_ASR_BASE_960H
    model = bundle.get_model().to(dev); model.train(False)
    labels = bundle.get_labels(); sr = bundle.sample_rate

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
            if i != prev and i != 0:
                out.append(labels[i])
            prev = i
        return "".join(out).replace("|", " ").strip().lower()

    return asr, dev


def flagged_runs(records, smoothed, min_overlap):
    """Contiguous FLAGGED windows (smoothed overlap < min_overlap) merged into runs. A run spanning a
    large fraction of a track == the gross-drift / wrong-boundary signature (the Strategists cliff)."""
    runs = []
    cur = None
    for rec, sm in zip(records, smoothed):
        if sm < min_overlap:
            if cur is None:
                cur = {"i_start": rec["i"], "t_start": rec["t0"], "t_end": rec["t1"],
                       "overlaps": [rec["overlap"]], "n_windows": 1}
            else:
                cur["t_end"] = rec["t1"]; cur["overlaps"].append(rec["overlap"]); cur["n_windows"] += 1
        elif cur is not None:
            runs.append(cur); cur = None
    if cur is not None:
        runs.append(cur)
    for r in runs:
        ov = r.pop("overlaps")
        r["min_overlap"] = round(min(ov), 3)
        r["mean_overlap"] = round(sum(ov) / len(ov), 3)
    return runs


def render_ascii(track_label, dur_min, interval, thr, records, smoothed):
    """One row per probe: a bar scaled to overlap, threshold context, '<' on sub-threshold rows.
    cp1252-safe -- ASCII glyphs only (the console gotcha)."""
    lines = []
    lines.append(f"track {track_label}  ({dur_min:.1f}min)   interval {interval:.0f}s  thr {thr:.2f}")
    BAR = 30
    npass = 0
    for rec, sm in zip(records, smoothed):
        ov = rec["overlap"]
        filled = max(0, min(BAR, int(round(ov * BAR))))
        bar = "#" * filled + "-" * (BAR - filled)
        flagged = sm < thr
        npass += 0 if flagged else 1
        tag = "FLAG <" if flagged else "PASS  "
        lines.append(f"  t={rec['t0']/60:7.1f}min  {ov:4.2f}  {bar}  {tag}")
    return lines, npass


def render_html(tracks_viz, thr, snippet):
    """A single static self-contained file: inline <svg> polyline of overlap vs time per track, a
    horizontal threshold line, flagged runs shaded, hover <title> per point. NO JS framework, NO CDN,
    NO innerHTML (we emit a plain string FILE, never assign it to a DOM node). cp1252-safe text."""
    from html import escape
    W, H, PADL, PADR, PADT, PADB = 900, 180, 60, 20, 20, 30
    parts = ['<!doctype html><meta charset="utf-8">',
             '<title>align_qa drift profile</title>',
             '<style>body{font:13px system-ui,Arial,sans-serif;margin:16px;color:#222}'
             'h2{font-size:15px;margin:18px 0 4px}svg{background:#fafafa;border:1px solid #ddd}'
             '.cap{color:#666;margin:0 0 8px}</style>',
             '<h1>Alignment drift profile</h1>',
             f'<p class="cap">overlap = |aligned&cap;heard|/|aligned|; threshold {thr:.2f}; '
             f'snippets capped at {snippet} chars (IP guard).</p>']
    plotW, plotH = W - PADL - PADR, H - PADT - PADB
    for tv in tracks_viz:
        recs = tv["records"]
        dur_min = tv["dur_min"]
        parts.append(f'<h2>track {escape(tv["label"])} &mdash; {dur_min:.1f} min, '
                     f'{tv["npass"]}/{len(recs)} pass</h2>')
        if not recs:
            parts.append('<p class="cap">(no windows)</p>'); continue
        tmax = max((r["t1"] for r in recs), default=1.0) or 1.0

        def X(t): return PADL + plotW * (t / tmax)
        def Y(ov): return PADT + plotH * (1.0 - max(0.0, min(1.0, ov)))
        svg = [f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}">']
        # shade flagged runs
        for run in tv["runs"]:
            x0 = X(run["t_start"]); x1 = X(run["t_end"])
            svg.append(f'<rect x="{x0:.1f}" y="{PADT}" width="{max(1.0,x1-x0):.1f}" '
                       f'height="{plotH}" fill="#f3c0c0" opacity="0.5"/>')
        # threshold line
        yt = Y(thr)
        svg.append(f'<line x1="{PADL}" y1="{yt:.1f}" x2="{PADL+plotW}" y2="{yt:.1f}" '
                   f'stroke="#c33" stroke-dasharray="4 3"/>')
        # axes box
        svg.append(f'<rect x="{PADL}" y="{PADT}" width="{plotW}" height="{plotH}" '
                   f'fill="none" stroke="#ccc"/>')
        pts = " ".join(f"{X(r['t1']):.1f},{Y(r['overlap']):.1f}" for r in recs)
        svg.append(f'<polyline points="{pts}" fill="none" stroke="#2a6" stroke-width="1.5"/>')
        for r in recs:
            al = escape(r["align_text"][:snippet]); he = escape(r["heard_text"][:snippet])
            svg.append(f'<circle cx="{X(r["t1"]):.1f}" cy="{Y(r["overlap"]):.1f}" r="2.4" '
                       f'fill="{"#c33" if r.get("flagged") else "#2a6"}">'
                       f'<title>t={r["t0"]/60:.1f}min  overlap={r["overlap"]:.2f}\n'
                       f'ALIGN: {al}\nAUDIO: {he}</title></circle>')
        svg.append(f'<text x="{PADL}" y="{H-8}" fill="#666">0 min</text>')
        svg.append(f'<text x="{PADL+plotW-44}" y="{H-8}" fill="#666">{tmax/60:.0f} min</text>')
        svg.append(f'<text x="6" y="{Y(1.0)+4:.0f}" fill="#666">1.0</text>')
        svg.append(f'<text x="6" y="{Y(0.0)+4:.0f}" fill="#666">0.0</text>')
        svg.append('</svg>')
        parts.append("".join(svg))
    return "\n".join(parts)


def cmd_profile(a):
    audio_by_no = {track_no(p): p for p in glob.glob(a.audio_glob) if track_no(p)}
    aligns = {track_no(p): p for p in glob.glob(os.path.join(a.dir, "align*.json")) if track_no(p)}
    # also accept per-CHAPTER outputs (chapNN_*.json) so profile works pre-recombine
    audio_offset = 0
    if not aligns:
        aligns = {track_no(p): p for p in glob.glob(os.path.join(a.dir, "chap*.json")) if track_no(p)}
        # align_chapters.py emits 0-indexed chapNN_*.json, but the matching per-chapter audio is
        # 1-indexed (chap00 <-> 01.wav/01.mp3). Resolve audio at chap_index + 1 instead of equating
        # filename numbers, else every chapter is skipped as "no audio" -> no windows sampled (Codex P2).
        audio_offset = 1
    if not aligns:
        sys.exit(f"no align*.json / chap*.json in {a.dir}")
    avail = sorted(aligns)

    import numpy as np
    if a.tracks:
        tracks = [t.zfill(2) for t in a.tracks]
    else:
        tracks = [avail[int(round(k))].zfill(2)
                  for k in np.linspace(0, len(avail) - 1, min(6, len(avail)))]

    # GPU guard: one model load at a time; refuse a 2nd python process; abort if child torch is CPU-only.
    if not a.no_preflight and not preflight("cuda"):
        sys.exit("PREFLIGHT ABORT (see above). NEVER run profile while an alignment is in flight. "
                 "Pass --no-preflight only if you are certain no other GPU job is running.")
    asr, dev = make_asr(a)

    import soundfile as sf
    all_ascii, report_lines, json_tracks, tracks_viz = [], [], [], []
    total, failed = 0, 0
    win = a.win
    cool_anchor = time.time()

    for tno in tracks:
        key = next((k for k in aligns if k.zfill(2) == tno), None)
        if key is None:
            print(f"\n[{tno}] (no align JSON)"); continue
        want_audio = str(int(tno) + audio_offset).zfill(2)
        anum = next((k for k in audio_by_no if k.zfill(2) == want_audio), None)
        if anum is None:
            extra = f" -> audio {want_audio}" if audio_offset else ""
            print(f"\n[{tno}] (no audio for track{extra})"); continue
        with open(aligns[key], encoding="utf-8") as f:
            doc = json.load(f)
        validate_doc(doc, source=aligns[key])
        segs = doc["segments"]
        apath = audio_by_no[anum]
        dur = sf.info(apath).duration
        title = doc.get("title", "")
        label = f"{tno}  {title}".strip()

        # probe points at 0, interval, 2*interval, ... clamped to dur - win
        t0s = []
        t = 0.0
        last = max(0.0, dur - win)
        while t <= last + 1e-6:
            t0s.append(round(min(t, last), 1)); t += a.interval
        if not t0s:
            t0s = [0.0]
        if a.max_points and len(t0s) > a.max_points:           # GPU budget cap
            idx = np.linspace(0, len(t0s) - 1, a.max_points)
            t0s = [t0s[int(round(j))] for j in idx]

        print(f"\n===== track {label}  ({dur/60:.1f}min, {len(t0s)} windows @ {a.interval:.0f}s) =====",
              flush=True)
        records = []
        for i, t0 in enumerate(t0s):
            t1 = t0 + win
            atext = aligned_window(segs, t0, t1)
            heard = asr(apath, t0)
            ov = overlap_of(atext, heard)
            # Metrics use the FULL window text; the stored snippets are capped at --snippet so EVERY
            # output that carries them (machine --json, report, HTML) honors the IP guard, not only
            # the report/HTML which truncate at render time (Codex P1).
            records.append({"i": i, "t0": t0, "t1": round(t1, 1), "overlap": round(ov, 3),
                            "n_aligned": len(set(words_of(atext))), "n_heard": len(set(words_of(heard))),
                            "align_text": atext[:a.snippet], "heard_text": heard[:a.snippet]})
            # thermal: cooldown after every --cooldown-every wall-seconds of sustained GPU load
            if a.cooldown_every and (time.time() - cool_anchor) >= a.cooldown_every and i < len(t0s) - 1:
                print(f"  COOLDOWN: idling {a.cooldown:.0f}s (GPU quiescent to shed heat)", flush=True)
                time.sleep(a.cooldown); cool_anchor = time.time()

        overlaps = [r["overlap"] for r in records]
        smoothed = rolling_median(overlaps, a.smooth)
        for r, sm in zip(records, smoothed):
            r["smoothed"] = round(sm, 3); r["flagged"] = sm < a.min_overlap
        runs = flagged_runs(records, smoothed, a.min_overlap)

        ascii_lines, npass = render_ascii(label, dur / 60, a.interval, a.min_overlap, records, smoothed)
        all_ascii += ascii_lines
        nfail = len(records) - npass
        total += len(records); failed += nfail
        summary = f"=== track {tno}: {npass}/{len(records)} PASS"
        if runs:
            longest = max(runs, key=lambda r: r["n_windows"])
            summary += (f"; FLAGGED RUN {longest['t_start']/60:.1f}-{longest['t_end']/60:.1f}min "
                        f"(mean {longest['mean_overlap']:.2f}, {longest['n_windows']} win)")
        summary += " ==="
        all_ascii.append(summary)
        if a.viz == "ascii":
            print("\n".join(ascii_lines), flush=True)
            print(summary, flush=True)

        # flagged-segment report: ALIGN vs AUDIO side by side, per window inside each run
        for run in runs:
            report_lines.append(f"FLAG track={tno} t={run['t_start']/60:.1f}-{run['t_end']/60:.1f}min "
                                f"min={run['min_overlap']:.2f} mean={run['mean_overlap']:.2f} "
                                f"n_win={run['n_windows']}")
            for r in records:
                if run["t_start"] <= r["t0"] <= run["t_end"] and r["flagged"]:
                    report_lines.append(f"  t={r['t0']/60:.1f}min overlap={r['overlap']:.2f} "
                                        f"n_aligned={r['n_aligned']} n_heard={r['n_heard']}")
                    report_lines.append(f"    ALIGN: {r['align_text'][:a.snippet]}")
                    report_lines.append(f"    AUDIO: {r['heard_text'][:a.snippet]}")

        # boundary suggestion: the segment at the START of the longest flagged run (the cliff)
        suggest_seg = None
        if runs:
            longest = max(runs, key=lambda r: r["n_windows"])
            cand = [s for s in segs if s["start"] >= longest["t_start"]]
            if cand:
                suggest_seg = min(cand, key=lambda s: s["start"])["id"]

        json_tracks.append({"track": tno, "title": title, "dur_sec": round(dur, 3),
                            "interval": a.interval, "win": win, "min_overlap": a.min_overlap,
                            "n_windows": len(records), "n_pass": npass, "n_fail": nfail,
                            "windows": records, "runs": runs, "suggest_at_seg": suggest_seg})
        tracks_viz.append({"label": label, "dur_min": dur / 60, "records": records, "runs": runs,
                           "npass": npass})

    # flagged-segment report to console + optional file
    if report_lines:
        print("\n----- FLAGGED SEGMENTS (ALIGN text vs AUDIO heard) -----", flush=True)
        print("\n".join(report_lines), flush=True)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines) + ("\n" if report_lines else ""))
        print(f"\nwrote flagged-segment report -> {a.report}", flush=True)

    if a.viz == "html":
        html = render_html(tracks_viz, a.min_overlap, a.snippet)
        out = a.out or "drift_profile.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"wrote HTML drift profile -> {out}", flush=True)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"tracks": json_tracks}, f, ensure_ascii=False, indent=2)
        print(f"wrote machine JSON -> {a.json}", flush=True)

    # gate compatibility: exit nonzero past the book-wide fail fraction (still a CI gate, now localizing)
    frac_fail = failed / total if total else 1.0
    print(f"\n=== {total-failed}/{total} windows PASS, {failed} FLAG ({frac_fail:.0%} fail; "
          f"threshold {a.max_fail_frac:.0%}) ===", flush=True)
    if total == 0:
        sys.exit("no windows sampled")
    if frac_fail > a.max_fail_frac:
        sys.exit(f"GATE FAIL: {frac_fail:.0%} of windows below {a.min_overlap} overlap "
                 f"(> {a.max_fail_frac:.0%}).")
    print("GATE PASS", flush=True)


# ======================================================================================
# wps-check -- cheap pre-ASR boundary screen (NO GPU)
# ======================================================================================
def _chapter_index(path):
    m = re.search(r"chap(\d+)", os.path.basename(path))
    return int(m.group(1)) if m else None


def cmd_wps_check(a):
    """Flag the wrong-boundary signature CHEAPLY (no model load): a merged/mis-bounded unit's auto-wps
    comes out ABOVE the book's normal narrator pace because its true audio is longer than the boundary
    implies (text squished into too little audio -> words/sec reads high). Two input modes:

      * --track-map + --text + --audio-glob  : PRE-align slice mode -- wps = n_alignable(text_slice) /
        sum(audio durations for that chapter's tracks). IDENTICAL to align_chapters --auto-wps.
      * --dir (chapNN_*.json) + --track-map + --audio-glob : POST-align mode -- n_alignable from each
        chapter JSON's own segment texts, audio from the track map's tracks. (The dir is being written by
        a running align; whatever's present is fine -- missing chapters are reported, not fatal.)
    """
    import soundfile as sf
    audio_by_no = {}
    for p in glob.glob(a.audio_glob):
        n = track_no(p)
        if n is not None:
            audio_by_no[int(n)] = p
    if not audio_by_no:
        sys.exit(f"--audio-glob matched no numbered audio files: {a.audio_glob}")

    with open(a.track_map, encoding="utf-8") as f:
        tmap = json.load(f)
    if not isinstance(tmap, list) or not tmap:
        sys.exit(f"track map {a.track_map} is not a non-empty list")

    rows = []          # (idx, title, wps, audio_sec, nalign, note)
    missing = []

    if a.dir:
        # POST-align: pair chapter JSONs to map entries by chapNN index (explicit, like recombine)
        idx_file = {}
        for p in glob.glob(os.path.join(a.dir, "chap*.json")):
            ci = _chapter_index(p)
            if ci is not None:
                idx_file[ci] = p
        for i, e in enumerate(tmap):
            tracks = e.get("tracks") or []
            try:
                asec = sum(sf.info(audio_by_no[t]).duration for t in tracks)
            except KeyError as ke:
                rows_note = f"audio missing for track {ke}"
                rows.append((i, e.get("title", f"ch{i}"), None, None, None, rows_note)); continue
            if i not in idx_file:
                missing.append(i); continue
            with open(idx_file[i], encoding="utf-8") as f:
                doc = json.load(f)
            na = n_alignable([s.get("text", "") for s in doc.get("segments", [])])
            wps = round(na / asec, 3) if asec > 0 and na > 0 else None
            rows.append((i, e.get("title", doc.get("title", f"ch{i}")), wps, asec, na, ""))
    else:
        if not a.text:
            sys.exit("post-align --dir not given, so slice mode requires --text (the whole-book prose).")
        with open(a.text, encoding="utf-8") as f:
            sents = [ln.strip() for ln in f if ln.strip()]
        bounds = [e.get("seg") for e in tmap] + [len(sents)]
        if any(b is None for b in bounds[:-1]) or bounds != sorted(b for b in bounds if b is not None):
            sys.exit(f"track-map seg values missing or not ascending: {bounds}")
        for i, e in enumerate(tmap):
            s0, s1 = bounds[i], bounds[i + 1]
            tracks = e.get("tracks") or []
            try:
                asec = sum(sf.info(audio_by_no[t]).duration for t in tracks)
            except KeyError as ke:
                rows.append((i, e.get("title", f"ch{i}"), None, None, None, f"audio missing for track {ke}"))
                continue
            na = n_alignable(sents[s0:s1])
            wps = round(na / asec, 3) if asec > 0 and na > 0 else None
            rows.append((i, e.get("title", f"ch{i}"), wps, asec, na, ""))

    measured = [w for (_, _, w, _, _, _) in rows if w is not None]
    if not measured:
        sys.exit("no chapter produced a usable wps (no overlap of audio + text/JSON present yet?).")
    med = statistics.median(measured)
    mad = statistics.median([abs(w - med) for w in measured]) or 1e-9

    print(f"=== wps-sanity (median {med:.2f} w/s over {len(measured)} units; MAD {mad:.3f}) ===", flush=True)
    if missing:
        print(f"    (note: {len(missing)} chapter JSON(s) not present yet -- skipped: indices {missing})",
              flush=True)
    flagged = []
    # sort by absolute deviation so the worst outliers are obvious
    for (i, title, wps, asec, na, note) in sorted(rows, key=lambda r: (-abs((r[2] or med) - med))):
        if wps is None:
            print(f"  [{i:02d}] {title[:30]:30s}  --     {note}", flush=True); continue
        dev_pct = (wps - med) / med * 100.0
        dev_mad = (wps - med) / mad
        flag = (abs(dev_pct) > a.wps_tol * 100.0) or (a.wps_mad and abs(dev_mad) >= a.wps_mad)
        tag = ""
        if flag:
            squish = "squished -> boundary too early / merged unit" if wps > med else \
                     "too much audio -> boundary too late / over-fetched"
            tag = f"  <-- FLAG ({squish})"
            flagged.append((i, title, wps, dev_pct))
        print(f"  [{i:02d}] {title[:30]:30s}  wps {wps:5.2f}  {dev_pct:+5.0f}%  {dev_mad:+5.1f} MAD{tag}",
              flush=True)

    print(f"\n=== {len(flagged)} unit(s) flagged (tol +-{a.wps_tol*100:.0f}%"
          f"{', %g MAD' % a.wps_mad if a.wps_mad else ''}). "
          f"Run `profile --interval 20` on a flagged track to confirm. ===", flush=True)
    if a.strict and flagged:
        sys.exit(f"WPS GATE FAIL: {len(flagged)} unit(s) deviate beyond tolerance.")


# ======================================================================================
# correct -- apply a declarative correction file -> re-emit per-track JSON
# ======================================================================================
def _renumber(segs):
    for i, s in enumerate(segs):
        s["id"] = i
    return segs


def _backup(path):
    """Demote the prior file to .bak before overwriting (mirrors align_book.save_ckpt). Returns True
    when the prior file is safely preserved (nothing to back up, or it was moved to .bak); returns
    False when a prior file EXISTS but could not be backed up. On failure os.replace leaves the
    original intact, so the caller must refuse to overwrite rather than destroy it (Codex P1)."""
    if not os.path.exists(path):
        return True
    bak = path + ".bak"
    try:
        if os.path.exists(bak):
            os.remove(bak)
        os.replace(path, bak)
        print(f"  kept backup -> {os.path.basename(bak)}", flush=True)
        return True
    except OSError as e:
        print(f"  ! could not write .bak ({e})", file=sys.stderr)
        return False


def _op_offset(doc, op):
    """Pure JSON transform (NO GPU): nudge segments in seg_range by delta_sec (start/end + every word
    s/e). Ideal for a within-chapter wander where re-aligning the whole track is overkill."""
    lo, hi = op["seg_range"]
    delta = float(op["delta_sec"])
    n = 0
    for s in doc["segments"]:
        if lo <= s["id"] <= hi:
            s["start"] = round(max(0.0, s["start"] + delta), 3)
            s["end"] = round(max(0.0, s["end"] + delta), 3)
            for w in s.get("words", []):
                w["s"] = round(max(0.0, w["s"] + delta), 3)
                w["e"] = round(max(0.0, w["e"] + delta), 3)
            n += 1
    # chapter markers whose seg is in range follow their segment's new start
    for c in doc.get("chapters", []):
        if lo <= c.get("seg", -1) <= hi:
            c["start"] = doc["segments"][c["seg"]]["start"]
    print(f"  offset: shifted {n} segments in [{lo},{hi}] by {delta:+.3f}s", flush=True)
    return doc


def _op_set_boundary(doc, op):
    """Pure JSON transform (NO GPU): move a chapter marker to a different segment (a resplit only,
    no realign). chapter == marker index; seg == new first-segment id."""
    ci = op["chapter"]; seg = op["seg"]
    chs = doc.setdefault("chapters", [])
    if not (0 <= seg < len(doc["segments"])):
        sys.exit(f"set_boundary: seg {seg} out of range 0..{len(doc['segments'])-1}")
    start = doc["segments"][seg]["start"]
    if 0 <= ci < len(chs):
        chs[ci]["seg"] = seg; chs[ci]["start"] = round(start, 3)
        if "title" in op:
            chs[ci]["title"] = op["title"]
    else:
        chs.append({"title": op.get("title", f"Chapter {ci}"), "start": round(start, 3), "seg": seg})
        chs.sort(key=lambda c: c["seg"])
    print(f"  set_boundary: chapter {ci} -> seg {seg} (start {start/60:.1f}min)", flush=True)
    return doc


def _op_split_realign(doc, op, corr, a):
    """The HEAVY op (GPU): generalize book17_split_strat + book17_recombine_strat.

      1. boundary time B = segments[at_seg].start (Pt1 aligned well, so that time is accurate),
         overridable with at_min.
      2. cut the track wav at B -> two sub-wavs; slice the text at at_seg -> two sub-txts.
      3. run align_chapters.py --auto-wps on the 2-entry sub track-map (SEPARATE wps per part -- this
         is what makes the boundary cliff disappear).
      4. offset Pt2 by Pt1 audio duration, stitch into ONE timeline with two chapter markers, renumber
         ids, re-validate.

    Shells out to align_chapters (one model load per child, child EXITS between parts -> thermal
    breather; inherits checkpoint/resume). Obeys the spec sec.7 GPU guards (preflight up front)."""
    import soundfile as sf
    import numpy as np

    at_seg = op["at_seg"]
    segs = doc["segments"]
    if not (0 <= at_seg < len(segs)):
        sys.exit(f"split_realign: at_seg {at_seg} out of range 0..{len(segs)-1}")
    B = float(op["at_min"]) * 60.0 if op.get("at_min") is not None else float(segs[at_seg]["start"])
    print(f"  split_realign: boundary B = {B/60:.1f}min ({'at_min' if op.get('at_min') else f'seg {at_seg}'})",
          flush=True)

    audio_path = corr["audio"]
    work = a.workdir or os.path.join(os.path.dirname(os.path.abspath(corr["target"])) or ".",
                                     f"_qa_split_{slug(os.path.basename(corr['target']))}")
    os.makedirs(os.path.join(work, "audio"), exist_ok=True)

    # --- 2a. cut the wav at B (sub-wavs named 01.wav / 02.wav so track_no -> 1 / 2) ---
    data, sr = sf.read(audio_path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    nB = int(round(B * sr))
    nB = max(0, min(nB, len(mono)))
    sf.write(os.path.join(work, "audio", "01.wav"), mono[:nB], sr)
    sf.write(os.path.join(work, "audio", "02.wav"), mono[nB:], sr)
    print(f"  Pt1 wav {nB/sr/60:.1f}min  +  Pt2 wav {(len(mono)-nB)/sr/60:.1f}min", flush=True)

    # --- 2b. slice the text at at_seg into one whole-book txt the sub track-map indexes into ---
    sub_txt = os.path.join(work, "parts.txt")
    with open(sub_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(s["text"] for s in segs))   # full track text; map seg 0 / at_seg slice it
    parts = op.get("parts") or [{"title": f"{doc.get('title','Part')} (Pt. 1)"},
                                 {"title": f"{doc.get('title','Part')} (Pt. 2)"}]
    sub_map = [{"title": parts[0].get("title", "Part 1"), "seg": 0, "tracks": [1]},
               {"title": parts[1].get("title", "Part 2"), "seg": at_seg, "tracks": [2]}]
    sub_map_path = os.path.join(work, "track_map.json")
    with open(sub_map_path, "w", encoding="utf-8") as f:
        json.dump(sub_map, f, ensure_ascii=False, indent=2)

    # --- 3. align each part with its OWN auto-wps (the fix) -- shell out to align_chapters ---
    outdir = os.path.join(work, "per_chapter")
    cmd = [sys.executable, os.path.join(HERE, "align_chapters.py"),
           "--audio-glob", os.path.join(work, "audio", "*.wav"),
           "--text", sub_txt, "--track-map", sub_map_path, "--outdir", outdir,
           "--auto-wps", "--device", a.device,
           "--cooldown-every", str(a.cooldown_every or 1800.0), "--cooldown", str(a.cooldown or 30.0)]
    if a.smartctl:
        cmd += ["--smartctl", a.smartctl, "--smartctl-dev", a.smartctl_dev]
    print(f"  -> {' '.join(cmd)}", flush=True)
    if a.dry_run:
        print("  (dry-run: not running align_chapters; would stitch its 2 outputs)", flush=True)
        return doc
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        sys.exit(f"split_realign: align_chapters exited {rc} -- aborting (fix + re-run; finished parts skip).")

    # --- 4. offset Pt2 by Pt1 audio duration + stitch (book17_recombine_strat, parameterized) ---
    p1 = glob.glob(os.path.join(outdir, "chap00_*.json"))
    p2 = glob.glob(os.path.join(outdir, "chap01_*.json"))
    if not p1 or not p2:
        sys.exit(f"split_realign: expected chap00 + chap01 outputs in {outdir}; got {p1+p2}")
    pt1 = json.load(open(p1[0], encoding="utf-8"))
    pt2 = json.load(open(p2[0], encoding="utf-8"))
    pt1_dur = sf.info(os.path.join(work, "audio", "01.wav")).duration

    combined = [dict(s) for s in pt1["segments"]]
    for s in pt2["segments"]:
        t = dict(s)
        t["start"] = round(t["start"] + pt1_dur, 3); t["end"] = round(t["end"] + pt1_dur, 3)
        t["words"] = [{"w": w["w"], "s": round(w["s"] + pt1_dur, 3), "e": round(w["e"] + pt1_dur, 3)}
                      for w in s.get("words", [])]
        combined.append(t)
    _renumber(combined)
    pt2_seg = len(pt1["segments"])
    markers = [{"title": parts[0].get("title", "Part 1"),
                "start": round(combined[0]["start"], 3), "seg": 0},
               {"title": parts[1].get("title", "Part 2"),
                "start": round(combined[pt2_seg]["start"], 3), "seg": pt2_seg}]
    doc["segments"] = combined
    doc["chapters"] = markers
    print(f"  stitched: {len(pt1['segments'])} Pt1 + {len(pt2['segments'])} Pt2 = {len(combined)} segments; "
          f"Pt2 marker @ {markers[1]['start']/60:.1f}min (seg {pt2_seg})", flush=True)
    return doc


def cmd_correct(a):
    with open(a.correction, encoding="utf-8") as f:
        corr = json.load(f)
    for k in ("target", "audio", "ops"):
        if k not in corr:
            sys.exit(f"correction file missing required key '{k}'")
    target = corr["target"]
    with open(target, encoding="utf-8") as f:
        doc = json.load(f)
    validate_doc(doc, source=target)              # consumer-side fail-loud on the input too

    needs_gpu = any(op.get("op") == "split_realign" for op in corr["ops"])
    if needs_gpu and not a.dry_run and not a.no_preflight and not preflight(a.device):
        sys.exit("PREFLIGHT ABORT (see above). split_realign is GPU work -- never two model loads at once.")

    for op in corr["ops"]:
        kind = op.get("op")
        if kind == "split_realign":
            doc = _op_split_realign(doc, op, corr, a)
        elif kind == "offset":
            doc = _op_offset(doc, op)
        elif kind == "set_boundary":
            doc = _op_set_boundary(doc, op)
        else:
            sys.exit(f"unknown op {kind!r} (expected split_realign / offset / set_boundary)")

    _renumber(doc["segments"])
    # fix any chapter marker start drift after id renumbering (seg is an id -> index here, ids are 0..n-1)
    for c in doc.get("chapters", []):
        sg = c.get("seg")
        if isinstance(sg, int) and 0 <= sg < len(doc["segments"]):
            c["start"] = round(doc["segments"][sg]["start"], 3)

    try:
        validate_doc(doc, source=target)          # fail loud BEFORE writing (bad seg range, NaN, ...)
    except SchemaError as e:
        sys.exit(f"REFUSING TO WRITE -- corrected doc fails schema:\n{e}")

    if a.dry_run:
        print(f"\n(dry-run) corrected {target}: {len(doc['segments'])} segments, "
              f"{len(doc.get('chapters', []))} chapters -- NOT written.", flush=True)
        return

    if not _backup(target):
        sys.exit(f"REFUSING TO WRITE -- could not back up {target} to {target}.bak; the original is "
                 f"unchanged. Clear the .bak path (remove it / fix permissions) and re-run.")
    with open(target, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {target}: {len(doc['segments'])} segments, {len(doc.get('chapters', []))} chapters "
          f"(prior kept as {os.path.basename(target)}.bak)", flush=True)

    # optionally patch the manifest entry for this track (mirrors book17_recombine_strat)
    man_path = corr.get("manifest")
    if man_path and os.path.exists(man_path):
        tno = track_no(target)
        man = json.load(open(man_path, encoding="utf-8"))
        for t in man.get("tracks", []):
            if str(t.get("track")) == str(tno):
                t["sentences"] = len(doc["segments"])
                t["chapters"] = [{"title": m["title"], "start": m["start"]}
                                 for m in doc.get("chapters", [])]
        json.dump(man, open(man_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"patched manifest entry for track {tno} in {man_path}", flush=True)

    # close the loop: auto-verify the corrected track with a forensic profile (spec sec.6).
    print("\n----- post-correct verification -----", flush=True)
    print("Re-run a dense profile on JUST the corrected track to confirm the flagged run cleared, e.g.:",
          flush=True)
    print(f"  py -3.12 align_qa.py profile --dir {os.path.dirname(target) or '.'} "
          f"--audio-glob \"<this track's audio>\" --tracks {track_no(target)} --interval 20", flush=True)
    print("(not auto-run here so a single correct() does exactly one model load; run it next.)", flush=True)


# ======================================================================================
def build_parser():
    ap = argparse.ArgumentParser(
        description="Alignment QA workbench: dense drift profiling, a cheap wps boundary screen, "
                    "and declarative correction/re-emit. See spec-qa-workbench.md.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # ---- profile ----
    p = sub.add_parser("profile", help="dense ASR drift profile + flagged segments + viz [GPU]")
    p.add_argument("--dir", required=True, help="per-track (alignNN.json) or per-chapter (chapNN_*.json) dir")
    p.add_argument("--audio-glob", required=True, help="glob for the audiobook tracks (numbered NN ...)")
    p.add_argument("--tracks", nargs="*", help="track numbers to profile (default: spread across the book)")
    p.add_argument("--interval", type=float, default=90.0,
                   help="seconds between probe points (default 90; use 20 for a forensic pass on one track)")
    p.add_argument("--win", type=float, default=8.0, help="seconds of audio transcribed per probe")
    p.add_argument("--max-points", type=int, default=0, help="cap probe points per track for GPU budget (0=off)")
    p.add_argument("--smooth", type=int, default=3, help="rolling-median window to separate drift from noise")
    p.add_argument("--min-overlap", type=float, default=0.4,
                   help="a (smoothed) point is FLAGGED below this (matches the gate)")
    p.add_argument("--max-fail-frac", type=float, default=0.4,
                   help="exit nonzero if more than this fraction of windows FLAG (book-wide gate)")
    p.add_argument("--snippet", type=int, default=150, help="max chars of any prose snippet (IP guard)")
    p.add_argument("--viz", choices=["ascii", "html"], default="ascii", help="drift profile renderer")
    p.add_argument("--out", help="HTML output path (--viz html); default drift_profile.html")
    p.add_argument("--report", help="write the machine-greppable flagged-segment report here (txt)")
    p.add_argument("--json", help="write per-window records + runs here (consumed by correct/tooling)")
    p.add_argument("--cooldown-every", type=float, default=0.0,
                   help="THERMAL: idle --cooldown sec after this many WALL seconds of GPU load (0=off)")
    p.add_argument("--cooldown", type=float, default=30.0, help="THERMAL: seconds to idle at each cooldown")
    p.add_argument("--allow-cpu", action="store_true", help="run even if torch is CPU-only (slow; for tests)")
    p.add_argument("--no-preflight", action="store_true",
                   help="skip the 'no 2nd GPU job' preflight (ONLY if you are certain none is running)")
    p.set_defaults(func=cmd_profile)

    # ---- wps-check ----
    w = sub.add_parser("wps-check", help="flag auto-wps outliers (wrong-boundary tell) [NO GPU]")
    w.add_argument("--track-map", required=True, help="JSON list of {title, seg, tracks[]} (book order)")
    w.add_argument("--audio-glob", required=True, help="glob for the audiobook tracks (numbered NN ...)")
    w.add_argument("--dir", help="POST-align: dir of chapNN_*.json (wps from each chapter's own segments)")
    w.add_argument("--text", help="PRE-align: whole-book sentence-per-line prose (wps from text slices)")
    w.add_argument("--wps-tol", type=float, default=0.15, help="flag |dev| from the book median beyond this frac")
    w.add_argument("--wps-mad", type=float, default=0.0, help="also flag |dev| >= this many MADs (0=off)")
    w.add_argument("--strict", action="store_true", help="exit nonzero if any unit is flagged")
    w.set_defaults(func=cmd_wps_check)

    # ---- correct ----
    c = sub.add_parser("correct", help="apply a declarative correction file -> re-emit per-track JSON")
    c.add_argument("--correction", required=True, help="JSON: {target, audio, manifest?, ops[]}")
    c.add_argument("--device", default="cuda", help="device for a split_realign re-align (GPU)")
    c.add_argument("--workdir", help="scratch dir for split_realign (default: beside the target; LOCAL, not Dropbox)")
    c.add_argument("--cooldown-every", type=float, default=1800.0, help="passed to align_chapters (thermal)")
    c.add_argument("--cooldown", type=float, default=30.0, help="passed to align_chapters (thermal)")
    c.add_argument("--smartctl", help="path to smartctl.exe -> align_chapters drive-temp watchdog")
    c.add_argument("--smartctl-dev", default="/dev/sdb", help="smartctl device of the drive to protect")
    c.add_argument("--dry-run", action="store_true", help="apply + validate in memory, do NOT write / run GPU")
    c.add_argument("--no-preflight", action="store_true", help="skip the GPU preflight (only if sure none running)")
    c.set_defaults(func=cmd_correct)
    return ap


def main():
    ap = build_parser()
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
