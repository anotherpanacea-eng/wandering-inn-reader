# Saved local audiobooks: notes during playback

Status: proposed; spec only. Development of the Reader is paused by the owner
(2026-10-13); nothing here is built until the owner resumes it.
Tracking: [Fleet #720](https://github.com/anotherpanacea-eng/fleet-coordination/issues/720).
Scope: the owner's 2026-10-13 ruling on the cut-down version of the 2026-10-11 small
spec card. Spec custody covers this document only. A build needs its own issue and grant.
Verified base: Reader main `5b012ab`.

## 1. Outcome and boundary

While listening to a local audiobook, the reader presses **Note here**. Playback
pauses and an editor opens. Saving it stores a bookmark with an optional label and
note at the start of the current segment, on a saved transcript of that audiobook.
The Notes list shows the book's notes across all tracks and jumps to a note's
segment. Notes survive a reload.

Selection highlights stay a text-mode feature. An audio book accepts bookmark items
only.

Supported: local single audio + alignment JSON, and local folder playlists, paired
exactly as the player pairs them today. Demo, Dropbox/URL playlists and legacy audio
Resume sessions keep playing as before, and say that notes need a saved local
audiobook.

Out of scope: selection highlights in audio mode, timestamp anchors, stricter
package rules than playback uses, segment-time ordering checks, resource bounds on
the user's own files, audio storage, backup/restore of audio notes (the
[backup spec](spec-annotation-backup.md) already excludes them), automatic links to
text-library books, sync, pipeline changes and new test frameworks.

Sources: `index.html` setupMediaSession, loadTrack/prevTrack/nextTrack, the audio
`ended` handler, admitSearchDocument/resetAnnotations, createLibraryStore,
annotationData/annotationChange, closeSheet; [annotations](annotations-phase3.md),
[text library](library-phase1.md).

## 2. Saving a transcript and its identity

Offer **Save audiobook for notes** for a playing or loaded local session. Saving
reads each paired alignment document once and requires `validateDoc` to pass for
every one; a document that fails refuses the save with its track named, because the
player could not play it either. Otherwise save the tracks the player paired, in
playlist order, and show the track count in the confirmation. A saved partial
package is visible (track count) and its notes stay reachable under its own identity.

Identity is `audio-text-v1:` plus lowercase SHA-256 hex of the UTF-8 encoding of
`JSON.stringify(tracks.map(d => d.segments.map(s => s.text)))`. Compute it with Web
Crypto outside any IndexedDB transaction. Titles, filenames, audio bytes, chapter
labels, times and timed words are not part of it, so renames and retiming keep the
notes; editing text, reordering or splitting tracks makes a different book. Two
renditions with the same ordered transcript share notes, deliberately. No fuzzy or
title matching.

If Web Crypto SHA-256 or the existing `newAnnotationInstance` random-byte support
is unavailable, saving for notes is unavailable with a reason; playback and text
books are unaffected.

When the identity already exists, compare the full nested segment text inside the
write transaction; a mismatch refuses and leaves the saved record alone.

A newer book or file selection cancels a save still in progress. A failed save
leaves the library and resume selector as they were, shows "Not saved" with the
reason, and playback continues.

## 3. Storage

Use the existing `books` and `session` stores. Add a version-1 record with `format:"audio"`,
identity, title, `tracks` (the alignment documents, known schema fields only),
importedAt, lastReadAt, position `{track,seg}`, annotationInstance and the existing
version-1 annotations envelope. Text records are unchanged. The flattened source
for anchors is derived in memory: global segment index = sum of earlier tracks'
segment counts + local index.

Extend record validation and the library/annotation adapters for audio records;
the existing helpers assume a text record's `doc.segments`. Reuse their rules on
the flattened source: lifetime UUID and full source checked in the same transaction,
item revisions and monotonic IDs, item/length bounds, and "saved" only on transaction
completion. Items are existing `kind:"bookmark"` items with `start:{seg,offset:0}`,
no `end`, and ordinary id/revision/label/note fields. Empty text cannot hold a bookmark.
Saving the same transcript again preserves its annotations, lifetime, importedAt
and position. Position writes update only the existing bound book, preserving notes;
they cannot recreate a deleted book. A malformed audio record remains removable
without blocking other books.

Saving or opening sets `session/resume` to `{kind:"audio-book",identity}`. Saved
audio books bypass legacy `saveSession`/`saveAudio`, which stores audio blobs and
would overwrite that selector. No audio bytes enter the saved book. Legacy
`{kind:"audio"}` Resume stays as it is and is never converted. Removing the book
deletes its notes and clears a matching selector.

Reopening shows the saved transcript paused with no audio. Track and note navigation
work offline. **Choose local files to listen** pairs files as in §2 and attaches
playback only if the identity and nested text match this book; otherwise it offers
ordinary unsaved playback. A match keeps notes/lifetime/position and uses the newly
attached alignment timings for seeks, so retiming actually works; a mismatch never
updates the saved book. The UI must not claim the audio itself was verified.
Position `{track,seg}` is approximate and resumes at that segment's start.

## 4. Note here, track changes and navigation

**Note here** (visible only for a saved audio book) pauses playback, cancels
follow/queued autoplay and pending note/search navigation, and invalidates older
track-load completions before capturing the anchor. With audio ready, use
`findSeg(audio.currentTime)` after pausing, not a
scrolled-ahead reading position; without audio use the displayed transcript segment.
While a track is loading, wait for it to finish before offering Note here. Anchor
at `{seg, offset: 0}` on the global flattened source. If its text is empty, use the
nearest earlier non-empty segment in the track, then the nearest later one; a track
with no text refuses with a reason. The captured anchor stays fixed. Play requests,
including headset Play and seek-triggered autoplay, cannot resume playback while
the editor is open;
save, cancel and failure leave playback paused. Repeat submissions are disabled
while a save is pending.

**Unsaved notes and track changes.** While the editor is open for the audio book,
a same-book `loadTrack` request does not run, including Previous restarting the
current track. Guard both entry and async completion, including loads started
before the editor opened. This covers headset/lock-screen Previous/Next
(`setupMediaSession` → `prevTrack`/`nextTrack`), on-screen/keyboard chapter controls
(`skipChapter`), the chapter list, and end-of-track autoplay (`ended`). Instead,
playback stays paused (or stops at the end of the track) and the status line says
"Save or cancel the note to change track." The request is dropped, not queued, so
nothing jumps after the editor closes. Search, chapters and settings cannot replace
the open editor (`openSheet`). Explicit Cancel discards it; Escape/scrim dismissal
(`closeSheet`), leaving for the library, reopening/replacing files (including
`open`, `startBook` and `dbxOpenBook`) require an audio-editor discard confirmation
before changing state. This confirmation is new, not existing behavior. Declining
keeps the draft; these actions are disabled while saving. No reset/admission or stale
navigation completion may clear the editor except successful save or deliberate
discard. A track change after the editor closes behaves as today.

**Notes list.** Lists the book's bookmarks from every track in source order, with
track and segment. Selecting one rereads it, maps its global segment to
track/local segment, pauses, loads that track's transcript if needed, and scrolls
to the segment. Place the chosen source segment directly even when start times
repeat or run backwards. With audio attached it seeks to the segment's start time;
without audio it places the text only. Async completions are tied to the book session and
the track load token, so a stale load or seek cannot move a newer view. An audio
file that fails to load is reported separately from the text navigation, which
still succeeds.

If another tab deletes or replaces the book, show "Not saved" and keep the editor
text for copying; a fresh open is required. Stale writes are not retried.

## 5. Acceptance (for the eventual build)

Build scope: the player, existing Node checks at existing seams, the existing
manual annotation harness, and the annotation/library instructions. Run `./check.sh`
with no skipped mandatory cases. Use invented short text and synthesized
sub-20-second audio only. Phone acceptance is owner-run.

| Behavior | Required result |
| --- | --- |
| Save and reopen | Single-track and two-track local books save; the confirmation shows the track count; notes survive reload; reopening asks for local audio to listen. Demo/remote/legacy sessions explain why notes are unavailable. A failing alignment doc refuses with its track named. |
| Identity | Renames and retiming keep notes; reattached audio seeks using the new timings; edited text, reordered or split tracks get a new book; missing SHA-256/random-byte support leaves playback intact. |
| Note here | Pauses; saves `{seg, offset: 0}` at the current segment (empty-segment fallback); cancel and failed save stay paused; no highlight is offered in audio mode. |
| Open editor | Next/Previous (including same-track restart), chapter controls/list, end-of-track autoplay and a delayed load leave the draft intact with the status message. Play stays paused; sheet switching keeps the editor; dismissal/library/file replacement requires deliberate discard. After save/cancel, Next works and no old request jumps. |
| Notes list | Notes from both tracks list in order; selecting one loads the right track and segment paused, with and without audio, including repeated/nonmonotonic start times; rapid competing selections cannot show a stale view. |
| Storage | Two tabs create distinct IDs; same-transcript save preserves notes/lifetime/position; position writes cannot resurrect a deleted book; stale edit/delete keeps the form; delete then re-save gets a new lifetime; abort/quota failure keeps prior data. Saved audio Resume survives reload without storing audio bytes. |
| Compatibility | Text books, text annotations, backup/restore, unsaved local playback and legacy audio Resume behave as before. |
