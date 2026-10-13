# Saved local audiobooks: annotations during playback

Status: proposed; independent substantive spec review precedes build.
Tracking: [Fleet #720](https://github.com/anotherpanacea-eng/fleet-coordination/issues/720).
Owner admission: 2026-10-11 capacity-window small spec card.
Verified base: Reader main `23dd094d064f32c40d7720541652aefbaebe7b78`.
Spec custody covers this document only. Implementation needs a separate issue/grant.

## 1. Outcome, baseline and boundary

A reader playing a locally selected audiobook can explicitly save its aligned
transcript as a library book, bookmark visible text, highlight a selection, write
notes, and reopen those annotations after a reload. One saved book owns annotations
across its tracks. Audio files remain user-selected session resources; this increment
saves no new audio Blob, path, URL, handle or permission. The shelf explains:
“Transcript and notes saved on this device; choose local files to listen again.”

Keep the dependency-free single `index.html`, inert DOM helpers, offline annotation
operations and existing player/pipeline JSON schema. Support local single audio +
alignment JSON and local folder playlists. Demo, Dropbox/URL playlists, unsaved
sessions and old audio Resume sessions remain readable/playable but annotation-
unavailable until explicitly admitted from local files. No remote prefetch or
annotation upload, automatic association with text-library books, timestamp-only
notes, cross-track drag selections, library sync, pipeline changes,
TTS, new headless harness or media-storage expansion.

Main's `createLibraryStore` stores text records in IndexedDB `books`, with version1
annotations, lifetime UUIDs, source/revision checks and transaction-completion
success. Audio uses `session/last`; `saveSession` may retain only the active single
track's Blob. `loadTrack` resets `sourceIdentity`, builds only that track's DOM and
invalidates admitted search/annotation generation. Playlist positions use book
titles; standalone audio positions use document titles. Those are not annotation
identities. `setSeg` reconstructs timed words with added spaces; ordinary annotation
rendering currently runs only in text mode. `validateDoc` alone does not establish
monotonic segment times or complete playlist membership.

Sources: `index.html` validateDoc, buildPlaylist/loadTrack/open, saveSession/checkResume,
createLibraryStore, annotationData/annotationChange, bindAnnotations/annotationSelected,
setSeg/setWord and navigateSearchHit; [existing annotations](annotations-phase3.md),
[text-library ownership](library-phase1.md), [Phase3B spec](spec-annotations-phase3.md).
The Phase3B audio exclusion is the reason for this separate increment.

Backup/restore spec [draft #73](https://github.com/anotherpanacea-eng/wandering-inn-reader/pull/73)
(head `82259f5406b40b46a3d9c229a113c57563d0e406`) and implementation
[draft #74](https://github.com/anotherpanacea-eng/wandering-inn-reader/pull/74) are
independent held drafts, absent from this base. This spec builds on main alone;
no dependency or implicit stack. If combined later, no audio-specific backup rule
is needed: the audio book's annotationSession carries its identity and flattened
source, so #74's identity+digest matching applies unchanged. The active m4b
executor owns pipeline/test/check paths separately.

## 2. Explicit local admission and identity

Offer **Save audiobook for notes** for a playable local session. Validate the entire
selected package before any library write. Capture package ownership and `loadEpoch`; a newer
book/file selection cancels an old admission, including queued work before its
transaction starts. Save failure leaves the prior library and selector intact, with
an inline “Not saved” reason; current playback/transcript remain usable.

Single-file admission is one valid alignment document and its selected audio. For a
folder with `manifest.json`, require a readable manifest with a nonempty ordered
tracks array, exactly one selected sync and audio file for each named entry, and no
duplicate track, sync or audio references. Resolve exact filenames; do not use the
player's number fallback, discard missing entries, or replace a malformed manifest
with a smaller annotation book. Unreferenced files do not become tracks. Without a
manifest, use existing numeric pairing order only when each recognized sync/audio
number has one unambiguous partner; missing/duplicate partners refuse saving. Existing
permissive playback may continue, but must explain why that package cannot be saved.

Read/validate every selected alignment doc once for admission. Require validateDoc
success and nondecreasing segment start times (equal starts are allowed). Preserve
track order, segment order and every exact text string, including empty text. Use
known schema fields only in the saved documents; do not retain arbitrary imported
properties. One resource bound: refuse before reading/parsing when the selected JSON
files (manifest plus selected sync files) total more than 256 MiB by File.size; never
shorten the book. Audio bytes are not read for identity or stored by this increment.

Identity is `audio-text-v1:` plus lowercase SHA256 hex of UTF8 encoding of
`JSON.stringify(tracks.map(d => d.segments.map(s => s.text)))`. Compute outside an
IndexedDB transaction using browser Web Crypto. Track/segment boundaries are part
of identity; titles, audio names/bytes, chapter labels, times and timed-word arrays
are not. No whitespace normalization or concatenation. Thus filename/title changes
and retiming of the same partition preserve identity; reordering, splitting tracks
or editing any source text creates a different book. All timed renditions with that
exact ordered transcript share notes intentionally; this identifies a transcript,
not a narrator or audio asset. Text-library identities have a separate namespace.
No title/filename/time matching, fuzzy remapping or user override.

If SHA256 or cryptographic UUID support is unavailable, saving for audio annotations
is unavailable with a reason; ordinary playback and existing text-book capabilities
continue. No weak hash, random fallback audio identity or automatic legacy adoption.
On an existing identity, also compare the complete nested ordered source strings
inside the write transaction; a mismatch refuses without replacing the saved record.
The digest is a compact identity, not permission to trust stored mutable data.

## 3. Library ownership and persistence

Use the existing `books` and `session` stores at database version2. Add a discriminated
version1 audio record: `format:"audio"`, identity, `reimportStable:true`, title,
`tracks` (ordered admitted alignment documents), importedAt, lastReadAt,
position `{track,seg}`, annotationInstance and optional version1 annotations.
Audio has no `doc` or cover. Existing text record shape stays intact. Validate the
union at each storage/UI entry point; derive a flattened segments source in memory
from audio tracks rather than storing another copy of its text. The cumulative
segment count maps each global source segment to exactly one track/local segment.
Malformed persisted rows are visibly unreadable and removable without blocking
healthy books. Older Reader builds may display new
audio rows as unreadable; they must not be needed to manipulate those rows.

Successful save/open transaction sets `session/resume` to
`{kind:"audio-book",identity}`. Keep legacy `{kind:"audio"}` Resume and its last
record unchanged for unbound audio; never migrate it to a book automatically.
Bound audio `saveSession` must not overwrite its selector with legacy audio Resume.
Explicit text/legacy-audio opens still select their existing resume type. Position
writes never change the last-explicit-open selector. Removal clears a matching
text/audio-book selector atomically, removes that book's notes, and never falls back
to legacy state to resurrect it.

Reopen reads the latest valid book transactionally, preserves its lifetime, and
binds annotations after completion. Show its saved track transcript paused, without
an audio source; track navigation and annotation navigation work offline. **Choose
local files to listen** admits a complete local package through §2 and requires its
exact identity and nested source projection to match this entry before attaching
playback. A mismatch offers ordinary unsaved opening or separate explicit save,
never attachment to these notes. Filenames are only pairing hints in the selected
package. User-selected audio pairing is not cryptographically verified against the
transcript, and the UI must not claim such verification.

Same-identity explicit save/reattachment preserves importedAt, annotations, lifetime
and position, while updating validated titles/timing docs and lastReadAt. Fresh
admission after deletion creates a fresh annotationInstance. Reopen/save binds that
UUID to the book session; list refresh cannot adopt a different lifetime. Before
annotation reads/writes compare the latest instance and full nested source in one
transaction. Missing, malformed, changed or deleted records refuse; mutation never
calls admission or recreates a record. Retain the existing item revisions, immutable
coordinates/kind, monotonic IDs, 1000-item/120-scalar-label/8192-note-unit bounds,
strict envelope validation and concurrent-create/edit/delete behavior.

Saved audio position is approximate source place `{track,seg}`, not precise audio
seconds or finished/time-left status. Save only existing records and valid points,
with the expected book lifetime/source. Resume at that track segment's current start
time after reattachment; no title-key position may seed an audio-library admission.
Keep title-key position behavior for unbound legacy sessions. Metadata/position
updates reread and preserve annotations; annotation writes preserve current metadata/
position. Only transaction completion means saved. Abort/quota/denial preserves
committed data and unsaved forms; repeat submissions are disabled while pending.

## 4. Anchors and render/playback lifecycle

Reuse the existing annotation item schema. `{seg,offset}` uses a **global** flattened
source segment and UTF16 boundary; bookmarks point to an actual source character,
highlights are canonical ordered nonempty half-open ranges, and surrogate pairs
cannot split. No timestamps, excerpts, DOM paths or timed-word ordinals are stored
as anchors. A selection may cross multiple source paragraphs in the current track;
chapter/control/mixed selections refuse explicitly. Cross-track selection is
unsupported because only one track is rendered, but the book list covers all tracks.
A bookmark with no selection records the first proved-visible source character,
not the current spoken word or segment-start guess.

On Notes activation, first capture the actual browser selection/visible source
point and track generation, pause playback, cancel follow/queued autoplay, and
stabilize source rendering. Map only when concatenated DOM source text equals exact
segments[].text; otherwise refuse the selection visibly and let the reader select
again while paused. Never map reconstructed `w + " "` text onto the source by guess.
Opening Notes without a usable selection must still allow listing/editing existing
items. Stay paused on close, cancel and failed save. Search navigation and Notes
navigation must cancel each other's pending view changes.

For an annotation-enabled audio session, rendered paragraphs must preserve exact
source text across setSeg, seek, resume, page/layout changes and mark refresh. Preserve
timed word glow only when the ordered timed tokens exactly match all source
non-whitespace tokens and spans preserve the original whitespace; otherwise use
sentence glow for that segment. Never replace punctuation, introduce separators or
invent token offsets. Annotation marks and timed-word spans can nest/split over the
same exact source ranges; both must remain inert DOM, preserve text equality and
recompute geometry after layout. Plain annotation rendering must not be overwritten
by the old sentence-collapse/search-reset paths. Unsaved audio behavior is unchanged.

A successful track change keeps book identity/lifetime but creates a fresh track
view generation, invalidating selections/editors/captures and outstanding UI work.
An editor must not silently disappear: switching books/library while unsaved input
exists requires ordinary explicit discard confirmation; refusal keeps it paused and
editable. While saving, switching is disabled. A failed/stale track load never binds
new annotations or changes the committed track index. If an external tab deletes
or replaces the book, show unavailable/Not saved and retain editable text for copy;
require reopen for a new lifetime. Do not automatically retry a stale write.

Navigate rereads and checks the selected annotation revision/lifetime, maps its
start to track/local segment, pauses, and loads that stored track transcript. With
attached audio, seek to **segment start**, explicitly approximate for an offset
inside the sentence; without audio, place text only. Scroll/page to the exact source
character after pause, seek and track/render completion. Stay paused, suppress legacy
restore/pendingReadSeg/follow from displacing that anchor, and do not claim that
character has a known word timestamp. A later Play resumes ordinary follow. Scope
all async completions to book session and target track generation/load tokens; older
loads, seeks or renders cannot move the newer book/view. A missing/unplayable audio
file reports text navigation success separately from playback failure.

## 5. Build and acceptance contract

Build scope is the player, focused existing Node/manual-browser annotation coverage,
canonical gate wiring only if needed for genuine new behavioral tests, and the
annotation/library user instructions. No pipeline/model/data change or new testing
framework. Coordinate fresh implementation custody against the then-live backup
builder; a docs spec grant conveys no index.html ownership. Run canonical `./check.sh`
with zero skipped mandatory Node cases, IP and safe-pattern guards, and independent
implementation review before a held draft. Browser and native-phone evidence remain
separate; phone acceptance is owner-run, not implied by desktop viewport checks.

Use only invented short text and synthesized sub-20-second audio, never existing
private/book prose or new acquisition. Extend the existing manual annotation harness
for these browser behaviors; a new headless harness is not admitted. Minimum matrix:

| Behavior | Required observable result |
| --- | --- |
| One local single-track book and a two-track manifest book | Explicit save enables notes; shelf/reload reopen preserves them and requires local audio reselection. Unsaved/demo/remote/legacy audio explains unavailability. |
| Stable identity | Renames/retiming keep notes; same title different text, reordered/repartitioned tracks and text-book identity remain separate. Same transcript renditions intentionally share notes. Digest/random support failure leaves ordinary playback intact. |
| Complete admission | Missing/duplicate/ambiguous pairs, malformed manifest or later-track JSON, unsorted segment starts and an over-cap selection refuse without partial saved book or selector mutation. |
| Source anchors | Forward/backward multisegment selection, empty segments and emoji boundaries round-trip. Mixed heading/control and altered timed-token DOM refuse without shortened selection. Bookmark targets proved-visible source text in pages/scroll. |
| Playback and marks | Open Notes while playing pauses; edit/save/cancel/close, then explicit Play. Exact text and overlapping marks survive sentence/word glow, search and relayout. Token mismatch uses sentence glow. |
| Whole-book navigation | List notes from both tracks; navigate to exact source offset paused with/without audio, with approximate segment-start seek. Rapid competing track/book/navigation loads cannot publish stale view or bind wrong notes. |
| Ownership and concurrency | Independent tabs create distinct IDs; stale item edit/delete conflicts retain form; delete/reimport with same identity refuses old lifetime. Same-transcript timing update preserves notes; position save cannot clobber notes or resurrect deletion. |
| Atomic failures | Real IndexedDB request-success then transaction-abort, quota/denial, failed deletion, stale queued admission and unsupported crypto preserve previous data/selector and show no Saved success. Reload verifies durable state. |
| Compatibility | Existing text annotations/library/resume, local unbound playback and legacy audio Resume remain usable. |

Pure Node checks protect identity/partition mapping, strict admission, source
ranges and storage policy at existing seams. Actual browser checks establish
Selection/DOM, IndexedDB rollback/lifetimes, media event races, file reattachment,
keyboard focus and source geometry. Do not count a source grep or mocked success as
browser acceptance. Spec review must resolve identity/ownership/lifecycle findings
before build; no serious owner choice remains presumed beyond the concrete product
defaults above.
