# Bookmarks, highlights and notes

Open a saved text-library book and choose **Notes**. Select book text before opening Notes to highlight it; **Highlight + note** opens the same editor with the note field focused. **Add bookmark** records the selected start, or the first visible source character when nothing is selected. Give it an optional label and note, then choose **Save**.

The list lets you navigate, edit labels and notes, or delete a specific annotation after confirmation. Overlapping highlights share their visible marking but retain separate notes. Cancel discards the open form. Notes are plain text, including anything that resembles HTML.

Annotations live in the book's existing IndexedDB record on this browser and device. Reloading or reopening the book keeps them. Deleting the saved book removes its annotations. Reimporting identical source text into the same book preserves them; a different source projection cannot replace an annotated saved book. Audio and unsaved books do not offer annotations.

If another tab changes an item, an old edit cannot overwrite it. Your unsaved text remains in the editor: copy it if needed, cancel, reload annotations and open a fresh edit. Deleting and reimporting a book also requires reopening it before an old session can write. Storage failures retain unsaved text and do not report success. Unreadable annotation data is preserved rather than silently cleared.

Limits: 1000 annotations per book, 120 Unicode characters per label, and 8192 UTF-16 units per note. Larger entries are refused rather than shortened. Annotation navigation uses the source text; ordinary saved reading position remains segment-based.

For manual checks, serve the checkout locally and open `tests/annotations_harness.html`. Use only its invented text. Verify persistence, cross-tab edits, overlap, navigation after changing layout, and keyboard focus. Native helper tests do not establish browser or phone qualification.
