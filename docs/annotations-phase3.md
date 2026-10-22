# Bookmarks, highlights and notes

Open a saved text-library book and choose **Notes**. Select book text before opening Notes to highlight it; **Highlight + note** opens the same editor with the note field focused. **Add bookmark** records the selected start, or the first visible source character when nothing is selected. Give it an optional label and note, then choose **Save**.

The list lets you navigate, edit labels and notes, or delete a specific annotation after confirmation. Overlapping highlights share their visible marking but retain separate notes. Cancel discards the open form. Notes are plain text, including anything that resembles HTML.

Annotations live in the book's existing IndexedDB record on this browser and device. Reloading or reopening the book keeps them. Deleting the saved book removes its annotations. Reimporting identical source text into the same book preserves them; a different source projection cannot replace an annotated saved book. Audio and unsaved books do not offer annotations.

If another tab changes an item, an old edit cannot overwrite it. Your unsaved text remains in the editor: copy it if needed, cancel, reload annotations and open a fresh edit. Deleting and reimporting a book also requires reopening it before an old session can write. Storage failures retain unsaved text and do not report success. Unreadable annotation data is preserved rather than silently cleared.

Limits: 1000 annotations per book, 120 Unicode characters per label, and 8192 UTF-16 units per note. Larger entries are refused rather than shortened. Annotation navigation uses the source text; ordinary saved reading position remains segment-based.

For manual checks, serve the checkout locally and open `tests/annotations_harness.html`. Use only its invented text. Verify persistence, cross-tab edits, overlap, navigation after changing layout, and keyboard focus. Native helper tests do not establish browser or phone qualification.

## Backup and restore

Choose **Export annotations** in Notes to request a JSON download. The file contains your labels, notes, source coordinates, and book identity; keep it somewhere private. Book text, audio, covers and reading position are excluded. Unsaved editor text is excluded. Browser SHA-256 support is required for backup/restore; ordinary notes remain usable without it.

To restore, first import and open the original book, then choose the backup with **Restore annotations from backup**. The preview counts new entries, exact duplicates and conflicts (entries that differ from a saved one at the same spot) and shows the resulting total before anything is saved. Exact duplicates are skipped and new entries are added. Conflicts are skipped unless you tick **Also add … entries that differ at already-annotated spots**, which keeps both. Apply saves the whole batch; Cancel saves nothing. Restore merges entries and never deletes current notes or rewinds edits.

The same original file and format can restore on another device when both book identity and projected source text match. Fallback or legacy identities restore only where that exact saved identity already exists; a fresh fallback import is a different book. Changed source text, unreadable data, unknown formats and files over 64 MiB are refused. Apply rechecks duplicates against the latest saved notes, so restoring the same backup again adds nothing.
