# Agent workflow

This repo is single-author (`anotherpanacea-eng`) but multi-agent: Claude sessions
and Codex sessions both contribute. This document is the durable, tool-agnostic
record of how they should work here. `CLAUDE.md` is a thin pointer to this file.

**Not part of the SETEC / APODICTIC fleet.** This is a standalone personal tool —
a read-along audiobook player plus a forced-alignment pipeline. There is no
cross-repo dependency, no vendored contract, no weekly-sync bot. Do **not** bolt
the fleet's vendor/lock/drift-gate machinery onto it; the right move here is
restraint (the same instinct that says "don't build a shared library for two
consumers"). The operator's local `Cowork/repo-fleet/` hub documents the fleet,
not this repo, and is invisible to cloud containers.

## The flow

```
spec  →  review  →  write  →  review  →  fix  →  merge
            ▲                    ▲
         codex                 codex
         claude                claude
```

- **Codex 5.5 is the PR review step.** It catches P1/P2 issues Opus misses. Make
  the obviously-needed fixes, then let Codex review. Don't spawn heavy Opus review
  fleets (duplicates the gate) and **don't merge out from under Codex** unless told
  to land. Be explicit about which model produced a finding.
- **Docs-only PRs skip Codex** (README/AGENTS prose, no code/behavior change) —
  merge directly. Codex is a correctness gate; reserve it for code.
- **Merge commits, never squash** — preserves the spec→review→fix trail.
- **Private repo:** GitHub branch protection needs Pro for private repos, so this
  relies on CI-equivalent local checks + discipline, not enforced gates.
- Work from a written contract (chat brief for trivia, a GitHub Issue once it's
  non-trivial), not an unscoped "improve the player."

## Repo layout

- **`index.html`** — the player. One self-contained file: no build step, no
  framework, no dependencies, no network. Keep it that way. Everything runs
  locally on the device; nothing uploads.
- **`pipeline/`** — one-time jobs run on the operator's Mac / GPU PC:
  - `fetch_text.py` — pull chapter prose from wanderinginn.com → sentence-per-line
    `.txt` + `.chapters.json` markers.
  - `align.py` — convert an **aeneas** sync map → player JSON (sentence-level; word
    spans if you pass `--words-json`).
  - `align_torch.py` — **alternative** aligner on torchaudio `MMS_FA`: word-level
    timings, no aeneas/espeak install. Same output schema.
- **`demo/`** — offline sample: `demo-align.json` (source of truth) + `demo-audio.mp3`
  + `demo-data.js` (the embedded bundle the player's "Try the demo" loads).

## The data contract (the load-bearing thing)

The player and **both** aligners agree on one JSON schema. Change it in one place,
change it in all three:

```jsonc
{
  "title":  "string",                 // doc title + lock-screen metadata
  "audio":  "string",                 // filename hint only; the player loads the file the user picks
  "chapters": [                        // optional
    { "title": "string", "start": 12.5, "seg": 3 }   // seg = index into segments[]
  ],
  "segments": [
    { "id": 0, "start": 0.0, "end": 4.0, "text": "string",
      "words": [ { "w": "string", "s": 0.0, "e": 0.27 } ]   // optional → per-word glow
    }
  ]
}
```

Chapter `seg` is the index of the chapter's first sentence among the **non-blank**
lines — which is exactly the segment index, because `fetch_text.py` and `align*.py`
both skip blanks in input order. `align.py` warns loudly and drops a chapter whose
`seg` is out of range (a sentence-count vs audio-fragment-count disagreement);
trust that warning over a clean-looking output.

## Gotchas (this repo)

- **Don't hand-edit `demo/demo-data.js`** (it's a ~119 KB bundle of the align JSON
  plus base64 audio). Edit `demo/demo-align.json`, then re-splice — read
  `demo-data.js`, replace its `align` key with the JSON file, keep `audioDataUri`,
  re-emit `window.DEMO = …;`. (A throwaway Python script does this; the audio never
  needs regenerating.)
- **The pre-commit security hook blocks two patterns.** In the player, build the
  bottom-sheet / word-span DOM with the `el()` / `clear()` helpers, never by
  assigning an HTML string to an element. In `align_torch.py`, put the model in
  inference mode with `model.train(False)`, not the parenthesised eval-mode call.
- **`.gitignore` keeps the audiobook out** — `Reading/`, `*.m4a`/`*.m4b`, and the
  locally-generated `sync*.json` / `align*.json` / `volume*.mp3` working files. The
  web text stays the author's; this is personal read-along, not redistribution.
- **License flags only matter if you ever sell it:** aeneas is AGPL-3.0, and the
  torchaudio `MMS_FA` model is CC-BY-NC. Both fine for personal use.

## Verify before claiming green (no CI)

There is no CI; verification is local:

- `python3 -m py_compile pipeline/*.py` — all three compile.
- A functional check of `align.py` against a synthetic aeneas sync + a chapters
  file (assert chapters map to the right segment starts; assert an out-of-range
  marker is dropped with a warning).
- The **demo is the live render check**: open `index.html`, tap **Try the demo**,
  confirm the highlight tracks, the chapter menu lists chapters, and the settings
  sheet (text size / keep-awake / sleep timer) works. A browser can't be driven
  from a cloud container, so a cloud session should say the render path is
  logic-checked, not that it confirmed the UI on a device.
- Forced alignment against real audio needs the operator's machine (aeneas or torch
  installed) — by definition untested in-session; say so.
</content>
