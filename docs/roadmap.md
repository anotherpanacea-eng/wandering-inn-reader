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

| # | Phase | Issue | Depends on | Effort |
|---|-------|-------|-----------|--------|
| 0 | Prove the generic path (EPUB/TXT/MD ingest, text-only) | [#28](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/28) | — | M–L |
| 1 | Library (multi-book, IndexedDB) | [#29](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/29) | #28 | L |
| 2 | Reading comfort (typography & themes) | [#30](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/30) | #28 | M |
| 3 | Wayfinding (search, bookmarks, highlights, progress) | [#31](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/31) | #28, #29 | M–L |
| 4 | AI / generated voice (TTS streaming) — **decision open** | [#32](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/32) | engine; 4b → #29 | 4a M / 4b L |

### Phase 0 — Prove the generic path
A real EPUB renders in the existing engine with **zero engine changes**. In-browser
`EPUB → doc` behind the current Load flow (one book at a time, no library yet); TXT/Markdown
too. Dep-free: `DecompressionStream('deflate-raw')` to unzip, `DOMParser` to walk the OPF
spine + `nav` TOC → the existing `segments[]` (no timings). **Proof:** a stripped EPUB shows
chapters, paging, resume, font sizing — no edits to `build()`. **Out:** PDF (not reflowable;
needs pdf.js — explicit non-goal).

### Phase 1 — Library *(the one architectural shift)*
Keep many books, not one document. **IndexedDB** book store (EPUBs are too big for
`localStorage`); shelf UI (covers, last-read, progress); import/delete; migrate the single
`localStorage` position marker → **per-book**. Everything else in the roadmap is additive;
this is the only structural change. Unlocks per-book annotations (Phase 3) and the optional
local voice model (Phase 4b).

### Phase 2 — Reading comfort
Font family (serif/sans/dyslexic), line-height, margins/measure, justification +
hyphenation; themes (light/sepia/dark/OLED-black) + brightness. Built on the existing
CSS-column foundation. **Parallelizable** — depends only on the engine, so it can run
alongside Phase 1.

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

`0` proves the thesis cheaply → `1` makes it a real library → `2`/`3` make it pleasant and
navigable → `4` adds reach. **Parallelizable:** Phase 2 (comfort) and Phase 4a (Web Speech)
depend only on the engine, so either can run alongside Phase 1.

## Out of scope

PDF; cloud library sync / accounts (beyond the existing opt-in Dropbox *position* sync);
shipping any third-party book or voice in the repo (the IP guard governs what's committed).
