# Annotation backup and restore

Status: proposed. Fleet #763 (board §9 small spec card, admitted by the owner
2026-10-11). Builds on [Phase 3B annotations](spec-annotations-phase3.md).

## 1. Problem

Bookmarks, highlights and notes live only in one browser's IndexedDB. They are
lost when site data is cleared, when Safari evicts storage for a site that has
not been used recently, when the reader moves to a new device, and when a book
is deleted and imported again (removal deletes its annotations, and the new
record starts empty). Nothing in the app can copy them out.

## 2. Outcome

The library section gets two controls:

- **Back up notes** saves one JSON file holding the annotations of every saved
  book that has any.
- **Restore notes** reads such a file and adds its annotations to matching saved
  books on this device.

That is the whole feature: a file the reader keeps, and a merge that never
deletes. There is no sync, no account, no network request and no automatic
backup.

## 3. Backup file

```json
{
  "format": "inn-reader-annotations",
  "version": 1,
  "books": [
    {
      "identity": "generic-v1:epub:…",
      "source": "sha256 hex of the ordered segment texts",
      "title": "…",
      "items": [
        {"kind": "highlight", "start": {"seg": 3, "offset": 10},
         "end": {"seg": 3, "offset": 42}, "label": "", "note": "…",
         "preview": "…"},
        {"kind": "bookmark", "start": {"seg": 9, "offset": 0},
         "label": "chapter end", "note": "", "preview": "…"}
      ]
    }
  ]
}
```

- One entry per saved book that has at least one annotation and whose
  annotation data and `annotationInstance` read cleanly. Books left out for
  unreadable data are counted in the status line.
- `identity` is the saved book's existing identity.
- `source` is the SHA-256 (via the `crypto.subtle` digest the app already uses
  for identity) of `JSON.stringify(segments.map(s => s.text))`: the ordered
  segment texts with their boundaries. Restore uses it to refuse a book that the
  current ingest splits or projects differently from the backup (§4). If the
  digest is unavailable, backup reports that it cannot run on this browser.
- Items carry kind, points, label and note as stored. They do not carry `id`
  or `revision`: those belong to one book record, and restore assigns new ones.
- `title` and `preview` are only for a person reading the file; restore
  ignores them. `preview` reuses the existing list excerpt (index.html's
  annotation excerpt), so it stays short.
- Backup with no annotated books says "No notes to back up" and saves nothing.
  Otherwise the file is saved through a Blob and an `<a download>` link named
  `inn-reader-notes-YYYY-MM-DD.json`. The status line says the file was offered
  for download; the browser, not the app, confirms where it was saved.

## 4. Restore

Restore first reads and checks the whole file, before any write: valid JSON,
the `format` and `version` above, and every book and item well formed (types,
point shapes, item kind, label and note within the existing bounds). Any
failure rejects the file and changes nothing.

Then, for each book in the file:

- **Before writing**, read the saved book with the same `identity` and hash its
  ordered segment texts. The digest is asynchronous, so it cannot run inside an
  IndexedDB transaction without letting the transaction close.
- **Write** in one readwrite transaction against the record it reads, after
  checking synchronously with the existing `annotationSourceEqual` that the
  record's segment texts still equal the ones just hashed. If they do not, the
  book changed in between and is reported as **not saved**; retry.

The steps:

1. If no saved book has that `identity`, the book is **not on this device**.
   Restore never creates a book. Recovery after delete and reimport therefore
   needs a stable identity, which is the normal case (a SHA-256 of the same
   file bytes). Books saved under a fallback or legacy identity, made when the
   browser lacked the digest, get a new identity on reimport and cannot be
   matched.
2. If the saved book's `source` digest differs from the file's, the book is
   **text changed** and nothing is written to it. This one book-level check
   replaces per-item text comparison, which cannot tell two identical passages
   apart.
Steps 3 to 5 run inside the write transaction, on the record it read.

3. If the book's stored annotations are unreadable, or its `annotationInstance`
   is present but malformed, the book is **unreadable here** and is left
   unchanged. A record that simply lacks an instance keeps the existing
   behavior: it gets one on its next successful open.
4. Merge without losing intentional duplicates. Group items by kind, points,
   label and note. For each group, append only the shortfall: the file's count
   minus the book's count, if positive. Restoring the same file twice therefore
   adds nothing, and two identical notes stay two.
5. Give appended items fresh `id`s from `nextId` and `revision: 1`, validate the
   result with `annotationData`, and keep every other field of the record. If
   the result would pass 1,000 items, the book is **over the limit** and nothing
   is written to it.

Only a completed transaction counts as restored. If a book's transaction fails
(quota, abort), that book is unchanged and is reported as **not saved**; books
already committed stay restored. Retrying is safe because of step 4.

The status line reports what happened, for example "Restored 41 notes in 3
books. Skipped: 1 book not on this device, 1 book whose text changed. Not saved:
1 book (storage error); try again." An open reader refreshes its marks the same
way it does after refocus. Restore changes nothing outside the `annotations`
field.

## 5. Out of scope

Audio-session annotations (they have no saved identity yet), choosing books or
items to back up, replacing or deleting existing annotations, editing the file
in the app, automatic backups, and Dropbox upload. Any of these can be a later
card if this one proves useful.

## 6. Acceptance

Black-box tests in the `tests/test_annotations.mjs` style:

1. Back up two annotated books, delete one, import the same file again, and
   restore: its notes return with new ids on the same text. The other book adds
   nothing.
2. Restore the same file twice: the second run adds nothing. A book holding two
   identical highlights restores to two, not one, and not four.
3. The same identity with a different segment split (for example
   `["Afoo","fooBAR"]` versus `["A","foofooBAR"]`) is skipped as text changed.
4. A book not saved here is reported, and no record is created.
5. A restore that would pass 1,000 items writes nothing to that book.
6. A file whose last book is malformed changes no record.
7. Two books where the second transaction fails: the first stays restored, the
   second is unchanged and reported as not saved, and a retry completes it
   without duplicates.
8. Backup and restore make no network request, and note text appears in no URL,
   log or localStorage key.

Owner-run, once: on the owner's iPhone in Safari (and the installed reader, if
used), back up to Files and restore from Files.
