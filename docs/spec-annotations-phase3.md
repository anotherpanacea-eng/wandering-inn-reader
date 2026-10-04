# Phase3B: saved-library bookmarks, highlights and notes

Status: proposed; independent six-lens review required before build.
Sources: Reader issue31 and docs/roadmap.md Phase3; closed issue29 library.
Fleet task285 reserves this document only. No implementation custody follows.
Tier: COMPLEX (selection geometry, atomic persistence and document lifecycle).
Builder: unassigned; record actual available runtime/model and effort at implementation checkout under current Fleet routing.

## 1. Outcome and boundary

A reader can bookmark a visible source character, highlight selected book text,
add/edit a note, list/navigate/remove annotations, and reload/reopen the saved
book without losing them. Keep the existing single index.html, inert DOM helpers,
local-only IndexedDB library and unchanged admitted document schema.

This increment supports saved text-library books, including saved fallback and
migrated identities. Require mode=text, librarySaved, nonempty sourceIdentity and
a valid current persisted book before writing. An unsaved session or audio
session displays why annotations are unavailable; it must not silently claim
persistence. Audio currently has empty sourceIdentity and a separate session
store: audio-book annotations need a separately reviewed stable-identity/library
increment. No title-based association. Accurate progress/time-left remains a
separate issue31 unit; search remains draft57, not presumed integrated. No TTS,
cloud annotation sync, exports, corpus acquisition or schema changes to segments.

## 2. Verified source and dependency

Fresh main43558c4af2fe57dfa7b80cbb31c0c4aab0c2abba index.html contains
libraryRecordValid, createLibraryStore, idb(version2), queueLibrary,
openSavedBook/openTextDoc, segmentTextNodes, rangeGeometry/rangeColumn and
comfortAnchor. Store transactions reread books and resolve only on completion.
The per-tab promise queue does not serialize other tabs. library.admit builds a
new record, retaining only old importedAt/position; library.remove deletes the
book and matching resume selector. libraryProgress is explicitly approximate.
Existing rangeAtSegmentChar clamps and selects one UTF16 unit: it is not range
validation. No current source-selection or persisted-annotation mechanism exists.

Implementation consumes main's library APIs and text geometry. If implemented
on draft57, explicitly include83736a1b991133ef9ce0a212d73318904b75176a and its
admitted-document generation in the frozen train inventory; do not duplicate its
sheet/navigation ownership. Otherwise implement the same document-generation
invariant locally. A failed import attempt does not change admitted generation;
replacement, successful reopen and deletion do. No dependency on search queries,
match cursors, scores or audio time. Gate wiring from draft58 is a separate
constituent, not present on main.

## 3. Anchors and selection

Coordinates always refer to the current admitted segments[].text, after ingest
projection, never raw EPUB/Markdown, visual pages, DOM paths or timed words.
Point = {seg,offset}, both nonnegative safe integers; seg exists; offset is a
UTF16 boundary in [0,text.length]. Never split a surrogate pair. A bookmark must
point to an actual character (offset<length). A highlight is ordered half-open
{start:Point,end:Point}, may cross segments, and contains at least one source
character. Equal points are invalid. Cross-segment ranges include the suffix,
all intervening segment texts and final prefix, with no invented separators.

Capture the browser Selection before opening an editor; normalize forward and
backward selections. Text and element endpoints must resolve to equivalent
ordered source coordinates. Both endpoints and all selected intervening nodes
must belong to admitted source text in the reader. Chapter/menu/control text,
partly external selections or a DOM/source text mismatch are refused inline,
without silently shortening selection. Cross-segment source-only selections are
required; cross-heading mixed selections receive an explicit unsupported message.
Verify each segment's concatenated source text nodes equals admitted text before
mapping offsets; never accept clamped geometry as evidence of valid coordinates.

Add bookmark with no selection uses the first visible source character at the
reading anchor in the current scroll viewport or column, using fresh geometry.
If no source character can be proved visible, refuse; no guessed segment start.
If a bookmark is added from a selection, use its normalized start. Normalize a
viewport anchor inside a surrogate pair to that scalar's start, then recheck
visibility. Capture generation/identity with anchors and revalidate at submission.
Reopening even the same book invalidates an old selection/editor. A failed import
keeps current anchors valid. Closing an editor never creates an annotation.

## 4. Ordinary persisted records and concurrent edits

Embed optional annotations in the existing books record; no new object store or
DB version is needed. Absent annotations means empty. Proposed format:

```json
{"version":1,"nextId":1,"items":[]}
```

Each item has exactly id (positive safe integer), revision (positive safe integer),
kind (bookmark or highlight), start (Point), end (Point for highlight only),
label (string), note (string). IDs are allocated monotonically from nextId inside
the write transaction. Revision starts1 and increments on edit; safe-integer
exhaustion refuses before write. Do not store duplicated source excerpts, hashes,
CSS colors, HTML, timestamps or per-book identity in each item. Coordinates and
kind are immutable after creation; editing changes label/note only. Duplicate and
overlapping annotations are allowed and separately identified.

Author-proposed resource bounds, not operator rulings: at most1000 items per
book, label at most120 Unicode scalars, note at most8192 UTF16 units. Refuse
oversize creation/edit visibly; never truncate. Count source scalars correctly,
including supplementary characters. Empty label/note is legal. These ordinary
bounds limit DOM/storage work; review may change them before build.

Read-modify-write the latest book inside one readwrite transaction for each
create/edit/delete. Revalidate book, admitted ordered source texts, annotation
shape, item IDs/uniqueness/ranges, bounds and selected item revision. No stale
whole-record replacement or library.admit call for annotation mutation. Preserve
position/cover/import metadata and other items. Missing/deleted book refuses;
an annotation operation never recreates it. Edit/delete requires the revision
shown to the editor/list; missing or changed item reports conflict, retains the
unsaved form, and offers Reload annotations. Do not overwrite competing changes.
Independent creations in two tabs survive with distinct IDs. Opening/listing
annotations rereads storage; refocus/reopen refreshes marks. No new polling,
BroadcastChannel or cross-tab locking framework is required.

Only transaction completion means Saved. Abort/error leaves the prior persisted
record and in-view committed annotations intact, preserves submitted label/note
for retry, and reports Not saved. Disable repeat submission while one operation
is pending. UI completion must match identity and admitted generation. Queued
operations canceled by replacement/deletion before their transaction begins do
not write. Once an explicit operation commits for its original book, replacement
must not apply its result to the new book; no false promise of rollback after
commit. Deletion in another tab cannot be undone by a queued stale save.

Malformed annotation data does not invalidate a readable base book. Show an
Annotations unavailable warning, preserve the raw field unchanged, and refuse
annotation mutations; never auto-reset corrupt/unknown-version data. No repair
or clear-all action in this increment. Base-book reading and position writes
remain available and must retain the raw annotation field.

Same-identity import preserves the entire old annotation field, including unknown
or malformed data, when ordered admitted source texts are equal. Compare ordinary
strings, no new digest/cache. If existing annotated source text differs, refuse
replacement of the stored book rather than guessing offsets or erasing notes;
existing import-not-saved behavior can open the new text for this session. A
fallback reimport has a new identity and must not copy old annotations. Removing
a saved book removes its embedded annotations atomically and disables annotation
writing in any surviving session for that book. No separate orphan store.

## 5. UI, marks and navigation

Provide a clearly labeled Annotations entry, Add bookmark, Highlight selection,
and Highlight + note. A labeled editor supports label/note, Save and Cancel.
The annotation list has inert generated excerpt/location, label/note, Navigate,
Edit and Delete controls per item. Delete asks a clear item-specific confirmation.
Do not persist selection merely by opening a sheet. Reuse existing sheet helpers;
accessible heading, labels, status announcement, Escape/Tab containment and focus
return are required. Background reader shortcuts cannot consume editor typing.
Empty/unavailable/conflict/storage-failure states are explicit. No note values in
errors/logs/URLs/localStorage/Dropbox position payloads or outbound requests.

Render source-faithful marks using inert text/mark spans generated from sorted
source interval boundaries, retaining exactly the original concatenated segment
text. Overlapping highlights display the union of marked characters; the list
keeps separate items and notes. No destructive nested markup or source mutation.
Keyboard/list editing is sufficient; tapping a mark may open the relevant list,
but must not silently pick one overlapping annotation. No CSS Highlights API
requirement or framework; labels/excerpts/notes use el()/clear()/textContent.
Reapply committed marks after build/setSeg/relayout paths without invalidating
geometry or altering source offsets. Annotation buttons stay disabled in audio.

Navigate to the item's start after validating its current stored item/range and
current admitted generation. Recompute source character geometry after font,
viewport and mode changes. Reveal the containing column or character in scroll
mode, including later characters in a long segment; no segment-start substitute.
A geometry failure reports inability to navigate without committing a new saved
position. Use existing segment-level position persistence; exact annotation
anchors survive independently. Never autoplay, attach an audio time, or claim
this increment fixes accurate progress. Reading mode and existing search behavior
must continue to work with mark spans and concurrent sheet use.

## 6. Build units, interfaces and acceptance evidence

A later bounded implementation issue consumes this reviewed document and current
library/text geometry; produces working CRUD, range mapping, transient UI,
embedded persistence and behavioral tests in the existing dependency-free player.
Suggested files: index.html, tests/test_annotations.mjs, a manual browser harness,
README.md, and canonical gate wiring only when separately granted. Function names
are implementation choices; no production API/test-only exports are mandated.
No source edits are authorized by this specification grant.

Required proof with invented text and actual IndexedDB/browser geometry:
1. Source-only forward/backward and element/text selections; cross-segment range;
   surrogate boundaries, mixed chapter/outside selection and stale editor refusal.
2. Highlight + note and bookmark survive reload/reopen; two different books and
   same titles remain isolated; saved fallback-identity book works; identical
   stable reimport retains marks; changed projection refuses stored replacement.
3. Overlapping/duplicate marks retain exact source text and separate CRUD. Empty
   notes/labels, bounds and malformed/unknown stored annotations behave as above.
4. Real transaction abort/quota-style failure does not claim Saved or lose input;
   missing book, competing creates, stale edit/delete and delete-versus-save do
   not overwrite or resurrect data. No success based only on mocked callbacks.
5. Scroll and paged navigation reaches later source characters after font/mode/
   viewport reflow; geometry failure does not write a new position. Existing
   reading, search and audio paths remain intact with annotation controls disabled
   where unavailable. Keyboard typing, Escape, Tab and focus return are exercised.
6. No annotation/note leakage to storage outside the books record, logs, URLs,
   existing position sync or network. Full native gate, mandatory no-skip new
   Node behavioral cases, IP/safe-pattern checks and independent exact-head build
   reviews pass. Browser receipts state engine/platform and honest limitations.

Spec delivery needs independent six-lens review and source-anchor verification;
a green current gate verifies the documentation did not break existing checks,
not that annotations work. Claude counter-review/integration clearance remain
owed. No hosted arming, merge, deployment, training, model or paid-run authority.