# Roadmap: Inn Reader → generic reader

**Thesis.** The reading *engine* is already general — reflowable text, scroll + CSS-column
paged modes, chapter nav, follow/auto-scroll, resume-to-page, font sizing, lock-screen
controls, per-word glow. Becoming a *generic* reader is mostly an **ingest + library +
comfort** problem, plus an optional **generated-voice** track. `build()` (in `index.html`)
already renders from `segments[].text` + optional `chapters`; audio timings are additive.
So we extend, we don't rebuild.

Each phase produces the existing data contract and is its own `spec → review → … → merge`
(see [`AGENTS.md`](../AGENTS.md)). One GitHub issue per phase tracks the work.

## Guardrails (hold across every phase)

- **Preserve the data contract** — `{title, chapters[], segments[]}`, timings optional.
  EPUB ingest and TTS both feed it; the Parsneau read-along path is untouched.
- **Hold no-deps / local-first / single `index.html`.** Exactly one item (Phase 4b) bends
  "no downloaded assets," and only as an opt-in, documented exception.
- **Read-along-first identity.** Generated voice reuses the existing glow/follow/handoff —
  not a parallel UI.
- **`./check.sh` stays the gate**; docs/specs land first; DOM via `el()`/`clear()`.

## Phases

Current disposition checked 2026-10-04 against main `43558c4`. Phases 0–2 are
landed; the notes below identify their bounded shipped behavior. This is a
repository-state reconciliation, not a new browser or device qualification.

| # | Phase | Issue | Current disposition |
|---|-------|-------|---------------------|
| 0 | Generic path (EPUB/TXT/MD ingest, text-only) | [#28](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/28) | Landed in [train #40](https://github.com/anotherpanacea-eng/wandering-inn-reader/pull/40), `9693ebd` |
| 1 | Library (multi-book, IndexedDB) | [#29](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/29) | Landed in [train #49](https://github.com/anotherpanacea-eng/wandering-inn-reader/pull/49), `43558c4` |
| 2 | Reading comfort (typography & themes) | [#30](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/30) | Landed in [train #42](https://github.com/anotherpanacea-eng/wandering-inn-reader/pull/42), `85408b2` |
| 3 | Wayfinding (search, bookmarks, highlights, progress) | [#31](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/31) | Open; search, annotation and progress contracts/builds are unmerged drafts |
| 4 | AI / generated voice (TTS streaming) | [#32](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/32) | Open; owner decision required before implementation |

### Phase 0 — Generic path: landed

The Load flow accepts TXT, bounded Markdown and EPUB 3 through
`genericDocFromBytes()` into the existing text-only reader projection. EPUB
admission has explicit archive, package and resource bounds; compressed members
require browser raw-deflate support. See [the ingest contract](spec-generic-ingest-phase0.md).
[Feature #36](https://github.com/anotherpanacea-eng/wandering-inn-reader/pull/36)
landed through train #40. Its recorded acceptance includes synthetic packages
and a stripped Gutenberg EPUB 3 in Chromium; the additional macOS receipt
remains absent. This does not establish arbitrary EPUB or all-browser support.
PDF remains out of scope.

### Phase 1 — Library: landed

The IndexedDB saved-book library has a shelf, covers, import/delete and independent
per-book segment resume, including migration of the old single-document position.
[Feature #45](https://github.com/anotherpanacea-eng/wandering-inn-reader/pull/45)
landed through train #49. See [the library contract](library-phase1.md) for storage
refusals and migration behavior. Shelf progress is approximate; source-based
progress and time-left belong to the open Phase 3 work. The saved-book library
does not establish stable annotation identity for separate audio sessions.

### Phase 2 — Reading comfort: landed

Global persisted settings provide serif/sans font choices, an installed-font
Dyslexic option with fallback, line-height, margins/measure, justification and
best-effort hyphenation. Themes include system/light/sepia/dark/OLED, with an
in-app dimmer. Comfort reflow retains the logical reading position in scroll and
paged modes. [Feature #41](https://github.com/anotherpanacea-eng/wandering-inn-reader/pull/41)
and its position-preservation fixes landed through train #42. Font availability and hyphenation
remain device/browser dependent. Recorded comfort and library acceptance uses
Chrome, including phone-width viewports; physical iOS/Safari acceptance remains
unverified.

### Phase 3 — Wayfinding
In-book search (over `segments[].text`); bookmarks; highlights + notes (persisted via
Phase 1's IndexedDB; a highlight is a serialized range over `segments[]`); accurate
progress % / time-left.

### Phase 4 — AI / generated voice  ⚠️ decision held open
Listen to **any** book, including those with no audiobook, reusing the read-along UX.
**Synergy:** TTS emits word-boundary events as it speaks → word-level alignment *for free*,
so glow/follow/handoff work on a generated voice exactly like the Parsneau path; the contract
is unchanged (segments without timings; the voice supplies timing live).

Option space — **nothing selected yet:**

| Tier | What | Deps / Network | Verdict |
|------|------|----------------|---------|
| 4a | Web Speech API (`speechSynthesis`), system/OS voices | none | dep-free baseline; quality is device-dependent |
| 4b | Small **local neural** model (Piper/Kokoro via ONNX-Runtime-Web / WASM-WebGPU), downloaded once, cached in IndexedDB | runtime + model download (opt-in); inference on-device → nothing uploads | the one item that bends "no downloaded assets" |
| 4c | Cloud TTS API | network + **sends text out** | conflicts with "nothing uploads"; at most a user-supplied-key escape hatch |

**Decision status — OPEN.** Operator lean (not a commitment): *offer options depending on
the device* rather than one engine; a future improved **Siri / system voice** may suffice on
Apple; **small voice models with inflection and context-awareness** look potentially very
powerful; cloud (4c) undecided. Revisit before implementing — likely staged (4a baseline, 4b
opt-in upgrade), but **not locked**.

## Sequencing

The original sequence was `0` → `1` → `2`/`3` → `4`. Phases 0–2 are now landed.
Phase 3 still needs normal draft integration and qualification. Phase 4 remains
held at its owner decision; the earlier parallelization option is not execution authority.

## Out of scope

PDF; cloud library sync / accounts (beyond the existing opt-in Dropbox *position* sync);
shipping any third-party book or voice in the repo (the IP guard governs what's committed).
