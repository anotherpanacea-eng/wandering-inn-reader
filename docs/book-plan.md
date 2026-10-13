# Tier-1 book plan and local execution

`python pipeline/process_book.py book.json --plan` describes the existing m4b/live
pipeline as JSON on stdout. It reads only the manifest, creates no book artifacts,
and does not check inputs or executables. `--execute` explicitly runs that one
route through local package preparation. The modes are mutually exclusive and one
is required. Neither mode delivers or shares files.

```json
{
  "book": 1,
  "title": "Invented example",
  "tier": "m4b",
  "audio": {"m4b": "audio/book.m4b"},
  "text": {"source": "live", "toc_from": "1.00", "toc_to": "1.02"}
}
```

Paths resolve against the manifest directory, independently of the launch
working directory. The planner preserves native absolute and UNC paths without
filesystem probing or environment interpolation. URL-shaped and NUL-containing
paths, unknown fields, duplicate JSON keys, nonfinite constants and unsupported
routes fail with exit2 and a named diagnostic. Book is a positive integer, not a
boolean; titles and TOC labels are nonblank strings. Only the fields above and
optional `map` and `ship` objects are supported.

`map` may contain `m4b_starts`, `m4b_end`, and `confirmed` (default false). Starts
and end must be supplied together. Starts are nonempty, strictly increasing
nonnegative integer RAW m4b mark indices; end is an existing raw mark index greater
than the last start (usually the credits mark). It is not the audio duration or
the number of marks. Booleans are not indices. There must be one start per web
chapter. Confirmation requires a supplied map and is a human assertion.
`ship.dropbox_dir` may name a local destination but remains inert.

For a real run, the owner must first clear Code-PC power repair, select eligible
private source/audio and a durable local working directory, and confirm the
configured GPU Python interpreter, one-job posture and thermal/watchdog setup.
This code's synthetic gate does not qualify a processed audiobook, GPU runtime,
phone playback or delivery. It does not authorize inference, downloads or spend.
Keep the manifest and all book artifacts outside Git and outside Dropbox while
caches are active. Execution refuses a working directory inside a Git checkout
(a `.git` entry in it or any parent) or with a Dropbox path component; the owner
remains responsible for choosing an unsynchronized private directory. The existing
20-second audio / 500-word prose repository limits remain unchanged.

From that private manifest directory, use the configured ROCm Python3.12
interpreter to launch `process_book.py book.json --execute`. Children inherit that
interpreter and run sequentially with argument arrays. FFMPEG/FFPROBE resolve from
the existing environment/PATH/Shotcut rules and must be runnable. Before every
model stage, a successful PowerShell process-ID probe must show only this Python
driver, and its interpreter must positively report CUDA/ROCm availability.
Unavailable, nonzero, empty, malformed or competing-process status fails before
model loading. Alignment alone receives child `WI_PREFLIGHT_MAX_PY=2` for its
known driver parent; no unrelated jobs are permitted. No processes are killed.
Alignment retains its existing 1800-second in-chapter pause cadence, 30-second
cooldown and 45-second between-chapter pause. The drive-temperature watchdog stays
opt-in: pass `--smartctl PATH` (and `--smartctl-dev DEVICE`, default `/dev/sdb`) to
`--execute` and they are forwarded to alignment. Without `--smartctl`, alignment runs
with fixed cooldowns only. Confirm the actual device before a real run rather than
assuming a post-swap device.

With `map.confirmed=false`, execution lists the official TOC range, acquires its
complete ordered chapter roster, validates text/marker sentence conservation and
runs the mark probe. It writes ordinary artifacts and `probe.log` beside the
manifest, then returns **gate-a-required**, exit3, before any cut or alignment.
The report lists the acquired chapters and file paths. Review the local transcript,
then add the exact starts/end map and `confirmed=true` to the manifest.

A confirmed continuation requires the already reviewed acquisition artifacts.
It never refetches or reprobes. If you manually replace or change the text,
chapter roster or audio, reset `confirmed=false` and reconfirm the new map before
continuing, even when filenames or mtimes match. This increment does not detect
all external replacements or bind an approval to source hashes. Reacquisition
always ends held at Gate A, regardless of a previously supplied map.

Confirmed execution validates actual chapter marks, produces units and an
explicit track map, cuts NN.wav, runs per-chapter alignment with auto-wps and
CUDA, cuts NN.m4a, packages alignNN.json/manifest.json, and verifies every expected
track at five points. Units/map files retain their mtimes when regenerated contents
are unchanged. Cuts and alignment use their existing input-mtime/schema resume
checks after interruption. No new checkpoint or approval registry is written.
Packaging and final verification run again on every completed invocation.

The driver requires complete, unique, ordered chapter/unit/alignment/audio/package
rosters and schema-valid player documents preserving all source sentences. It
refuses missing, duplicate or unexpected output files and preserves them for owner
inspection rather than deleting them. Child failures stop downstream stages;
partial outputs do not count as prepared packages. The alignment helper retains
its existing coverage-gap cleanup behavior.

Verification writes a fresh local `verify.log`, uses exact overlap, WPS pre-screen
and the unchanged 0.4 overlap / 0.4 fail-fraction thresholds. No fuzzy,
undercoverage or WPS-outlier waiver is supplied. A successful child must report
exactly one final summary conserving five points for every expected track.
Nonzero status, WPS failure, missing/malformed/ambiguous summary or inconsistent
point/FLAG counts is a hard failure (exit2). Any FLAG at exit0 returns
**gate-b-required**, exit3, with flagged track/time/overlap and visibly flagged
chapters, even if the helper prints GATE PASS. There is no old-log or approval
latch. Fix/review boundaries and rerun; a complete zero-FLAG result returns
**prepared-local**, exit0. Every result leaves delivery unperformed.

The unchanged plan lists all stages and gates as descriptions, never executed
commands or verified approvals. Execution adds only this explicit m4b/live route:
other tiers, importers, Wayback, split/merge/map proposals, persisted flagged-result
approvals, generalized resume and copying to a destination remain later increments.
