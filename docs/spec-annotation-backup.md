# Annotation backup and restore: one saved book

Status: proposed; independent spec review required before implementation.
Tracking: [Fleet task #700](https://github.com/anotherpanacea-eng/fleet-coordination/issues/700).
Owner admission: Fleet capacity-window decision, 2026-10-11, small spec card.
Verified base: Reader main `23dd094d064f32c40d7720541652aefbaebe7b78`.
Custody for this card covers this document only; implementation needs its own grant.

## Outcome and boundary

A reader can download bookmarks, highlights and notes from the currently open
saved text-library book, choose that file later on this or another device, inspect
a restore preview, and explicitly merge selected entries into the same saved book.
The original book must already be imported. No book text, audio, cover, reading
position, settings or library record is exported or restored. No bulk-library
backup, audio annotations, cloud sync, replacement/clear-all mode, fuzzy remapping,
new database store/version, dependency or build step. Keep the single index.html
and local-only behavior. Downloaded notes may contain personal information; the
UI says the file contains notes and should be kept somewhere private.

Sources: `index.html` genericIdentityResult, annotationData/annotationStored,
annotationSourceEqual, createLibraryStore and renderAnnotations;
[shipped annotation contract](annotations-phase3.md),
[annotation spec](spec-annotations-phase3.md), and
[roadmap](roadmap.md). Main already stores version1 annotations inside books,
uses monotonic IDs and revisions, checks canonical UTF16 source coordinates and
book-lifetime annotationInstance, and commits through IndexedDB completion.
Original-file identity alone cannot prove the ingest projection stayed unchanged;
a source digest prevents restoring offsets onto changed projected text without
putting that text in the portable file. This is one computed value, not a registry.

## Portable file and matching

One UTF8 JSON document, downloaded as `inn-reader-annotations.json` with MIME
application/json. Required exact top-level fields:

```json
{
  "format": "inn-reader-annotations",
  "version": 1,
  "bookIdentity": "generic-v1:txt:EXAMPLE",
  "sourceSha256": "64 lowercase hexadecimal characters",
  "annotations": {"version": 1, "nextId": 1, "items": []}
}
```

The displayed identity/digest above are placeholders, not an import fixture.
annotations is the existing exact version1 annotation envelope, validated against
the current source by the shipped annotationData contract. Item fields and limits
remain unchanged: at most1000 items, 120 Unicode scalars per label, 8192 UTF16 units
per note, positive safe IDs/revisions, nextId greater than every ID, canonical
nonempty highlights, bookmarks on real characters, no surrogate splitting or
unknown fields. Do not coerce, normalize, truncate or repair file values. Source
IDs/revisions/nextId preserve the export snapshot; they never replace local ones.

sourceSha256 is lowercase SHA256 hex of UTF8 encoding of JSON.stringify of the
ordered array of every segments[].text string. No joins, whitespace normalization,
chapters, titles or timings enter the digest. Use the browser's existing Web
Crypto capability, outside a live IndexedDB transaction. Digest unavailability
makes backup/restore unavailable with an explanation; ordinary annotations and
reading remain available. No hash package or fallback weak hash.

Import requires exact bookIdentity equality with the open saved record AND exact
sourceSha256 equality with its current admitted ordered text. Never match by title,
filename, annotationInstance, offsets alone or a user override. Stable generic-v1
identities allow the same original bytes/format to match on another device.
Existing ephemeral/legacy identities can be exported, but restoration works only
where that exact identity already exists; explicitly explain this limitation in
preview/export status for non-generic identities or reimportStable===false. A
fresh fallback reimport is a different book. Import never creates a book.

Reject files over64 MiB before reading/parsing; reject UTF8 decoding errors,
invalid JSON, array/null envelopes, unknown format/version/fields, malformed
identity/digest and invalid annotation data. bookIdentity is a nonempty string
of at most1024 UTF16 units; sourceSha256 is exactly64 lowercase hex characters.
Export applies the same bounds and reports a refusal without claiming success.
The64 MiB cap accommodates the shipped1000-item limit even with maximal escaped
notes; this is a proposed resource bound for the reviewer, not a prior owner ruling.
No zip/import archive support. A parse/validation failure cannot mutate storage.

## Export and restore preview

Offer Export annotations and Restore annotations in the existing Notes sheet,
only when its normal saved-book/session/lifetime checks pass and no editor or
annotation operation is pending. Obtain export from a fresh readonly transaction,
validating the latest record, source and bound lifetime; never export the visible
cached list. Absent annotations exports the empty valid envelope. Export is a
coherent completed snapshot; a later edit does not retroactively change its file.
Only say download requested after preparing a valid Blob and activating the
browser download. Do not claim that the operating system saved it. Revoke object
URLs after use. Storage/read/download failure reports failure without changing
annotations. No unsaved editor text enters the backup.

A chosen valid file produces a transient preview with book title/identity, incoming
count and counts of new entries, exact duplicates and conflicts; show the selected
add count and resulting total. No import-time mutation, including lifetime
initialization, is allowed. The book must already have a valid bound lifetime.
Opening/canceling a preview or choosing another file commits nothing. File and
preview live in memory only; errors/logs/URLs/localStorage/network never contain
notes, excerpts or imported JSON. Render everything through inert DOM helpers.

Matching ignores incoming IDs/revisions and compares exact kind, start/end,
label and note. For each content tuple, match incoming occurrences one-to-one
against existing identical occurrences first. These are exact duplicates and are
skipped; excess incoming copies are considered separately, preserving intentional
duplicate multiplicity. Restoring the same file adds nothing once every incoming
content tuple has at least its incoming multiplicity locally. A partial selection
does not meet that guarantee: with two identical incoming entries, skipping one
and adding one leaves one duplicate and one excess new entry on the next preview.
That excess entry is selected by default again and still requires explicit Apply;
the preview exposes it rather than remembering earlier skip choices. Incoming file
order decides matching and allocation;
no sorting or normalization changes strings or anchors.

An unmatched incoming entry with no local item at the same kind/start/end is new
and selected to add by default. If any local item has that same kind/start/end but
different label/note, it is a conflict: display both values and local item ID(s),
with Keep existing (default, skip incoming) or Add a separate copy. If there are
multiple local entries there, display all relevant entries so the choice is
unambiguous. The excess occurrence of an otherwise identical tuple is new unless
another differing local tuple at the anchor makes it a conflict. A new entry may
also be explicitly skipped. No existing item is edited/deleted; incoming absence
is not a deletion. Explain that restore merges entries rather than rewinding edits.

Preview exposes all conflicts and selected entries for inspection, with accessible
labels, status announcements, Escape/Tab containment and focus return through the
existing sheet behavior. Full notes are available as inert plain text. Apply
restore is disabled if the selected additions exceed the per-book bound or local
nextId has insufficient safe-integer space. Never silently import a partial subset
on limit failure. A zero-addition preview reports Nothing to add and needs no write.

## Commit and races

Capture the current session/generation, persisted annotationInstance, ordered source
and a cloned valid annotations envelope at preview. On Apply, queue one ordinary
readwrite transaction through the existing library queue. Reread the current book
inside it and check the session is still current, book still exists, bound lifetime
is unchanged, ordered source matches and the entire annotation envelope is still
semantically equal to the preview snapshot (including nextId, IDs, revisions and
item order). Treat an absent field as the empty envelope. Compare fields/strings;
JSON property ordering is not a semantic change. A normal position/metadata write
alone does not invalidate preview.

Any changed annotation, deleted/reimported book, changed source or replaced open
session refuses the whole restore with no write. Retain chosen input/choices for
inspection, offer Refresh preview, and require a fresh preview plus Apply after
refresh; never silently rebase and apply old choices. Switching books invalidates
preview and cannot apply the old file to the new book. Do not adopt a different
lifetime through a list read. Validate the candidate and bounds again within the
transaction. Add selected entries in file order with fresh local IDs from latest
nextId and revision1; nextId remains monotonic, existing IDs/revisions are untouched.
Never copy portable annotationInstance or call library.admit for restore.

Update only annotations on the reread record, preserving its position, cover,
metadata and other fields. One successful transaction adds all selected entries;
abort/quota/error adds none, preserves previous committed marks and retains the
preview for retry. Repeat Apply is disabled while pending. Report Restored only
on transaction completion. A commit for the original book may complete after UI
replacement; never apply its marks/status to another book or promise rollback of
an already committed operation. Refresh normal committed list/marks after success.
Malformed local data refuses export/preview/apply and remains unchanged; base-book
reading and position writes continue under the shipped behavior.

## Build acceptance

Implementation is a separate bounded task after independent spec review. Suggested
files are index.html, existing annotation tests/harness and user documentation;
reuse existing library/sheet/validation patterns. No mandated test-only API, registry
or permanent verification machinery. Prove these behaviors with invented text:

1. A download preserves bookmark/highlight/note values including Unicode, empty
   text, cross-segment canonical ranges and intentional duplicate multiplicity.
   A second device with the same stable book/source restores them with fresh
   local IDs; exporting empty annotations works. Repeated full restore is a no-op
   once all incoming multiplicities are present. With two identical incoming
   entries, skip one/add one, reopen preview and verify one duplicate plus one
   excess new entry selected by default; Cancel leaves the first restore unchanged,
   and a second explicit Apply adds the remaining occurrence.
2. Wrong book with same title, changed projection of identical source bytes,
   reordered segments, different fallback identity, malformed/unknown/oversize
   file, invalid ranges/surrogates, excessive values and malformed local data
   refuse without writes. Same exact fallback identity can restore locally;
   missing digest capability leaves reading/ordinary annotation CRUD functional.
3. Preview/cancel/file replacement write nothing. Exact duplicates, excess copies,
   new entries, same-anchor conflicts with multiple local candidates and selected
   skips obey the count/choice rules. Keep existing and Add a separate copy retain
   current entries and never overwrite/delete. Capacity and ID exhaustion refuse
   the complete selected batch with no truncation.
4. A two-tab edit/create/delete between preview and Apply, delete/reimport with
   reused IDs, queued book replacement and source change all refuse stale Apply.
   A position-only change remains valid. Refresh requires another explicit Apply.
   Concurrent successful creations survive; restore cannot resurrect a deleted book.
5. Actual browser IndexedDB completion/abort and a forced storage failure prove
   all-or-none writes, metadata preservation, retained preview and honest status;
   helper mocks alone are insufficient. Reload proves persistence and marks, and
   stale-session completion cannot affect another book's UI.
6. Real browser download/file input, inert hostile note/label strings, keyboard
   navigation/focus, scroll/paged marks and the absence of note/JSON leakage into
   network, position-sync payloads, logs, URLs and other persistent stores are
   exercised. Record browser/platform and device limits honestly. No audio/corpus
   fixture or physical iOS qualification is implied.

Run the existing canonical local gate with mandatory no-skip new behavioral cases,
IP/safe-pattern checks, and independent exact-head implementation review. The
spec-only PR runs the existing gate but cannot establish implementation acceptance.
