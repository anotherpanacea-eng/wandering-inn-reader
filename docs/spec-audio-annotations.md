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

If Web Crypto SHA-256 or `crypto.randomUUID` is unavailable, saving for notes is
unavailable with a reason; playback and text books are unaffected.

When the identity already exists, compare the full nested segment text inside the
write transaction; a mismatch refuses and leaves the saved record alone.

A newer book or file selection cancels a save still in progress. A failed save
leaves the library and resume selector as they were, shows "Not saved" with the
reason, and playback continues.

## 3. Storage

Use the existing `books` and `session` stores. Add a record with `format:"audio"`,
identity, title, `tracks` (the alignment documents, known schema fields only),
importedAt, lastReadAt, position `{track,seg}`, annotationInstance and the existing
version-1 annotations envelope. Text records are unchanged. The flattened source
for anchors is derived in memory: global segment index = sum of earlier tracks'
segment counts + local index.

Reuse the existing annotation storage rules unchanged: lifetime UUID checked with
the full source in the same transaction, item revisions and monotonic IDs, the item
and length bounds, and "saved" only on transaction completion. A malformed audio
record is shown as unreadable and removable without blocking other books.

Saving or opening sets `session/resume` to `{kind:"audio-book",identity}`. Legacy
`{kind:"audio"}` Resume stays as it is and is never converted. Removing the book
deletes its notes and clears a matching selector.

Reopening shows the saved transcript paused with no audio. Track and note navigation
work offline. **Choose local files to listen** pairs files as in §2 and attaches
playback only if the identity and nested text match this book; otherwise it offers
ordinary unsaved playback. The UI must not claim the audio itself was verified.
Position `{track,seg}` is approximate and resumes at that segment's start.

## 4. Note here, track changes and navigation

**Note here** (visible only for a saved audio book) pauses playback, cancels
follow/queued autoplay, and opens the editor anchored at the current segment:
bookmark `{seg, offset: 0}` on the global flattened source. If that segment's text
is empty, use the nearest earlier non-empty segment in the track, then the nearest
later one; a track with no text refuses with a reason. The editor stays paused on
save, cancel and failure. Repeat submissions are disabled while a save is pending.

**Unsaved notes and track changes.** While the editor is open for the audio book,
a same-book track change does not run. This covers every `loadTrack` caller in the
session: headset or lock-screen Previous/Next (setupMediaSession), the on-screen
previous/next controls, chapter jumps that cross into another track (jumpChapter
and the chapter list), and end-of-track autoplay from the `ended` handler. Instead,
playback stays paused (or stops at the end of the track) and the status line says
"Save or cancel the note to change track." The request is dropped, not queued, so
nothing jumps after the editor closes. Leaving the book or library with an open
editor uses the existing discard confirmation. A track change after the editor
closes behaves as today.

**Notes list.** Lists the book's bookmarks from every track in source order, with
track and segment. Selecting one rereads it, maps its global segment to
track/local segment, pauses, loads that track's transcript if needed, and scrolls
to the segment. With audio attached it seeks to the segment's start time; without
audio it places the text only. Async completions are tied to the book session and
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
| Identity | Renames and retiming keep notes; edited text, reordered or split tracks get a new book; missing Web Crypto leaves playback intact. |
| Note here | Pauses; saves `{seg, offset: 0}` at the current segment (empty-segment fallback); cancel and failed save stay paused; no highlight is offered in audio mode. |
| Track change with an open note | Headset/lock-screen Next and Previous, on-screen controls, a cross-track chapter jump and end-of-track autoplay each leave the draft intact and the track unchanged, with the status message; after save or cancel, Next works and nothing jumps on its own. |
| Notes list | Notes from both tracks list in order; selecting one loads the right track and segment paused, with and without audio; rapid competing selections cannot show a stale view. |
| Storage | Two tabs create distinct IDs; stale edit/delete keeps the form; delete then re-save gets a new lifetime; transaction abort or quota failure shows no "Saved" and keeps prior data. |
| Compatibility | Text books, text annotations, backup/restore, unsaved local playback and legacy audio Resume behave as before. |
