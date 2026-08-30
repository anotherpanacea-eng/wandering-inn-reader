# Phase 0 specification: generic text ingest (TXT, Markdown, EPUB)

Status: DRAFT — implementation must not begin until independent spec review clears it.

Target: issue [#28](https://github.com/anotherpanacea-eng/wandering-inn-reader/issues/28),
Phase 0 of `docs/roadmap.md`.

## 1. Decision summary

Phase 0 adds one local-file entry point to the existing Load screen. It accepts one
`.txt`, `.md`/`.markdown`, or EPUB 3 `.epub` file, converts it entirely in the browser to
the player's existing document contract, and opens that document with the current
reader/pager. It does not create a library and it does not add an audio or TTS path.

The implementation SHALL:

- keep `index.html` self-contained and dependency-free;
- perform no network request while importing or reading a generic document;
- preserve `pipeline/schema.py::validate_doc` and its JavaScript mirror as the document
  contract;
- leave `build()` unchanged; the import boundary ends at a valid `doc` passed to the
  existing renderer;
- retain only the current one-record `session/last` resume behavior, not a collection of
  books;
- treat every imported title, path, TOC label, and prose string as inert text;
- reject an input atomically on a hard error, leaving the current reader/session intact.

The implementation SHALL NOT add a package manager, bundled library, service worker,
server conversion, remote fallback, build step, or a second application file. Test and
synthetic-fixture files MAY be added under `tests/`; shipped application logic remains in
`index.html`.

## 2. Why the projection contains logical `start` and `end`

Issue #28 says text documents have “timings omitted.” The live contract, however,
requires every segment to have finite numeric `start` and `end`, and the unchanged reader
uses those fields to order segments and chapters. Omitting the keys would fail both
`validateDoc()` and `pipeline/schema.py::validate_doc`.

For Phase 0, “timings omitted” therefore means **no audio-derived timing and no word
timings**. The importer SHALL project text location to the existing numeric fields:

```json
{
  "title": "Example",
  "audio": "",
  "chapters": [{"title": "One", "start": 0, "seg": 0}],
  "segments": [
    {"id": 0, "start": 0, "end": 1, "text": "First paragraph."},
    {"id": 1, "start": 1, "end": 2, "text": "Second paragraph."}
  ]
}
```

For segment index `i`, `id = i`, `start = i`, and `end = i + 1`. A chapter mapped to
segment `i` has `seg = i` and `start = segments[i].start`. These are logical coordinates,
not seconds. Generic documents have no `words` key. No new field is added to the shared
document schema.

The load path SHALL carry a separate in-memory mode value (`"audio"` or `"text"`) rather
than infer audio availability from synthetic coordinates. A restored generic session
persists that mode outside `doc`. Existing audio/sync documents continue to use real
seconds and the existing behavior.

## 3. Scope boundary

### 3.1 In Phase 0

- One local file at a time from a new Load control.
- Plain text decoded as specified in section 7.
- A deliberately bounded, safe plain-text projection of Markdown as specified in
  section 8.
- Conforming EPUB 3 ZIP/OCF packages with an OPF package document, linear XHTML spine,
  and EPUB navigation document as specified in sections 9–12.
- Existing scrolling, pagination, page turning, font-size settings, and chapter sheet.
- Text-only position and last-session resume.
- Synthetic fixtures, automated converter/security tests, and a real-browser behavioral
  fixture check.

### 3.2 Explicitly out of Phase 0

- PDF, DOCX, RTF, MOBI/AZW, comic-book archives, DRM, encrypted EPUBs, and remote URLs.
- EPUB 2 NCX-only TOCs, fixed-layout EPUB, media overlays, audio/video, scripting,
  MathML/SVG rendering, CSS, fonts, images, footnote popovers, page-list/landmarks,
  fallback chains, or non-linear auxiliary reading.
- Full CommonMark compliance or rendered Markdown/HTML.
- A multi-book catalog, user-managed deletion, cover grid, search, highlights,
  annotations, export, cloud sync, accounts, or TTS.
- Changing alignment/schema semantics or making a text document playable.
- Persisting original imported bytes. The one last-session record stores only the
  derived document and resume metadata.
- Background import, worker parallelism, streaming a book before validation completes,
  or importing more than one file in one action.

Phase 1 owns the library. Phase 2 owns reading-comfort work beyond proving the current
reader. Later roadmap work must not be pulled into this change merely because an ingest
implementation exposes a convenient seam.

## 4. Existing seams and permitted edits

The implementation is intentionally narrow around the live `index.html` structure.

### 4.1 Load seam

Add a separate file input to `#load` labeled for text/ebook documents with an accept list
equivalent to:

```text
.txt,.md,.markdown,.epub,text/plain,text/markdown,application/epub+zip
```

The extension (case-insensitive) is authoritative because local-file MIME values are
frequently empty or generic. A mismatching nonempty MIME value MAY produce a warning but
does not override a recognized extension. Any other extension is rejected.

Selecting a generic file starts an asynchronous `importGenericFile(file)` operation. A
second selection supersedes the first by generation token; a superseded operation must
not open or persist its result. The Load screen reports one concise progress state and
one terminal error. It does not expose archive internals.

On conversion success, the path keeps the result local, validates the projected document,
and rechecks its generation token. Only then does it commit `doc`, mode, and source
identity, call the unchanged `build()`, show the reader, restore position, and attempt to
save the last session in the ordering specified by sections 4.3 and 13.

### 4.2 Renderer seam

`build()` is the protected seam and SHALL have no diff. It already:

- consumes `doc.segments` and `doc.chapters`;
- inserts chapter headers through `chapBySeg`;
- writes imported strings with `textContent`/`el()` rather than HTML;
- feeds both scroll and paged reading modes.

The existing `buildPages()`, `buildPagesPaged()`, `pagedAnchors()`, font-size setting, and
page-turn layout behavior are reused. A Phase 0 implementation must not fork a second
renderer for generic documents.

### 4.3 Text-mode interaction seam

Text mode has no playable media. The implementation SHALL:

- hide or disable play, rate, seek, ±15-second, sleep-timer, follow-audio, wake-lock,
  Media Session, and audio-duration UI;
- keep page-turn controls/gestures, reader scrolling, display settings, and the chapter
  sheet available;
- make a chapter-row activation navigate to the chapter's segment/page without calling
  `audio.play()` or assigning a logical coordinate to `audio.currentTime`;
- make segment/word taps inert with respect to audio (there are no word spans);
- update `readingSeg` from the current page/scroll anchor and save by segment;
- never enter `tick()`, `seekTo()`, or audio event-driven restore for a generic document.

The mode guards belong in transport, chapter navigation, and persistence entry points;
they are not changes to `build()`.

The following matrix is binding for the live functions/handlers. `mode` is checked at
handler execution time, not only when the handler is installed, because an old audio
event can arrive after a generic document opens.

| Live seam | `mode === "text"` behavior |
|---|---|
| `posKey()` | Return the key derived from generic source identity; never use `doc.title`. |
| `save()` / `restore()` | Store/read only `{seg}`. Never read/write `audio.currentTime`, and never wait for `loadedmetadata`. |
| `flushSave()` plus `pagehide` / hidden `visibilitychange` | Call a forced position save which bypasses the 1,500 ms throttle (either `save(true)` or an equivalent dedicated function). |
| `onTap()` / `seekTo()` | Segment taps do not call `seekTo`; text-mode code must not enter `seekTo()` or `audio.play()`. |
| `renderChapters()` chapter buttons | Navigate directly to the target segment/page, set the reading anchor, and force-save it. |
| `skipChapter()` and previous/next chapter buttons | Navigate to the adjacent chapter segment without reading or changing audio state. |
| document `keydown` handler | Space and unshifted Left/Right do nothing; PageUp/PageDown still turn pages; Shift+Left/Right use text-mode chapter navigation. |
| `setupMediaSession()`, `updateMetadata()`, `updatePosition()` | Do not install/update a Media Session for the generic document. Stale handlers are cleared or made inert. |
| audio `play`, `pause`, `seeked`, `loadedmetadata`, `error`, and `ended` handlers | Return before mutating generic reader, position, chapter, toast, or session state. |
| `renderSettings()` and transport controls | Keep Reading mode and Text size; hide/disable Keep screen on, sleep, play, rate, seek, ±15, and Follow. |

Generic restore ordering is exact: after conversion and `validateDoc` succeed, recheck
the generation token; set `doc`, mode, identity, and the initial text state; call the
unchanged `build()`; show the reader; build visible paged geometry if needed; read and
clamp the stored `{seg}`; navigate without animation to that segment/page; set the
reading/chapter anchors; only then permit position and last-session writes. No initial
`setSeg(0)`, audio event, or throttled save may overwrite the saved segment before this
restore completes.

### 4.4 Contract validation seam

Every successful conversion SHALL pass the existing `validateDoc(doc, source)` before
the UI or IndexedDB state changes. The Python schema remains the source of truth and is
not loosened for generic ingest. Converter-specific validation happens before projection;
schema validation happens after it.

## 5. Common conversion model

Each format adapter returns an intermediate book with:

```text
title: nonblank string
blocks: ordered [{ text, resourcePath?, anchorIds[], kind }]
chapterCandidates: ordered [{ title, resourcePath?, fragment?, blockIndex? }]
warnings: ordered strings
```

This is an implementation concept, not a persisted public contract. `kind` is one of
`prose`, `heading`, `list`, `quote`, or `code`. Imported markup nodes never cross this
boundary.

The common projector SHALL:

1. normalize and trim each block's text as its adapter specifies;
2. discard blank blocks;
3. enforce the post-normalization limits in section 6;
4. remove a heading block only when it is solely the visible label for a chapter candidate
   mapped to that same block **and** a next surviving block exists in the same resource;
   remap that chapter to the next block (this prevents duplicate labels without content
   loss). If no next block exists, retain the heading as prose and discard that chapter
   candidate with a warning;
5. assign contiguous segment IDs and logical coordinates;
6. resolve chapter candidates to surviving segment indexes;
7. discard unresolved chapter candidates with a warning;
8. collapse candidates mapped to the same segment, retaining the first nonblank title
   in source/TOC order;
9. emit chapters in strictly increasing `seg` order; and
10. fail if no nonblank segment remains.

All strings are Unicode strings. The projector does not normalize prose with NFC, NFKC,
case-folding, smart-quote conversion, or language-specific transformations. Path and ID
handling has separate normalization rules because those strings are identifiers.

## 6. Resource ceilings and atomic failure

The following are deliberately conservative Phase 0 ceilings for a single-tab,
dependency-free browser importer. They are not EPUB validity limits and may be revised by
a later measured spec. They bound peak per-resource allocation, decompression work, final
DOM scale, and accidental archive expansion while leaving ample room for the stripped
prose books this phase is intended to prove:

| Limit | Value |
|---|---:|
| Any selected source file | 100 MiB |
| Decoded TXT or Markdown characters | 16,000,000 |
| ZIP central-directory entries | 10,000 |
| One ZIP entry, declared or actual uncompressed | 32 MiB |
| Total declared or actual uncompressed ZIP content | 256 MiB |
| Compression ratio for an entry at least 1 MiB uncompressed | 200:1 |
| One XML/XHTML resource before parsing | 16 MiB |
| Final nonblank segments | 250,000 |
| One final segment | 100,000 Unicode code points |
| All final segment text | 16,000,000 Unicode code points |
| Final chapters | 25,000 |

MiB means 1,048,576 bytes. Limits are checked with overflow-safe arithmetic. Declared
ZIP sizes are checked before decompression; actual streamed output is checked during and
after decompression. A false or absent declaration never disables an actual-output limit.
The total declared size is accumulated as integers only; the importer never reserves or
allocates a buffer of that total size. It decompresses only `mimetype`, container, chosen
OPF, nav, and included linear-spine resources. Unreferenced entries receive structural,
path, flag, range, declared-size, and ratio validation but are never decompressed merely
to prove their CRC. The actual-total ceiling is accumulated over referenced resources as
they stream.

Import is transactional at the application level: parsing and validation occur in local
variables. Until a complete projected document passes `validateDoc`, the implementation
must not replace `doc`, clear the reader, alter the last-session record, or overwrite a
saved position. No partially extracted book is displayable or resumable.

## 7. TXT decoding and segmentation

### 7.1 Encoding

TXT and Markdown adapters read bytes, not `FileReader.readAsText` defaults.

- `EF BB BF` selects UTF-8 and removes the BOM.
- `FF FE` selects UTF-16LE and removes the BOM.
- `FE FF` selects UTF-16BE and removes the BOM.
- With no BOM, strict UTF-8 is required.
- Decoding uses `TextDecoder(label, {fatal: true})`; replacement-character recovery is
  not permitted.
- UTF-32, legacy single-byte encodings, and heuristic charset guessing are out of scope.
- An interior U+0000 after decoding is a hard error.
- CRLF and bare CR become LF.

An empty or whitespace-only decoded file is a hard error. The title is the filename with
the final recognized extension removed and surrounding whitespace trimmed; if that is
blank, use `Untitled text` or `Untitled Markdown`.

### 7.2 TXT blocks

One or more blank lines delimit a paragraph. Within a paragraph, line breaks and adjacent
Unicode whitespace collapse to one ASCII space. Leading/trailing whitespace is removed.
Each nonblank paragraph becomes one `prose` block. TXT creates no chapter candidates.

This intentionally does not guess chapters from capitalization, Roman numerals, form
feeds, or lines beginning with “Chapter.” Such heuristics belong in a later iteration
only if representative inputs justify them.

## 8. Markdown projection

Markdown is converted to safe readable plain text; it is not rendered as HTML. The
accepted Phase 0 structural subset is deterministic:

- ATX headings with one through six leading `#` characters followed by whitespace;
- Setext headings whose following line consists of at least three `=` or `-` characters;
- blank-line-delimited paragraphs;
- list items beginning with `-`, `+`, `*`, or an ASCII-number-and-period marker;
- blockquotes beginning with `>`;
- indented and fenced code blocks using backticks or tildes.

ATX and Setext headings become `heading` blocks and chapter candidates. A heading's
plain label has the structural marker and an optional matching ATX closing marker
removed. It maps to the next surviving content block after the duplicate-label removal
in section 5. Heading level affects neither hierarchy nor the final flat chapter list.

Paragraph continuation lines are joined with one space. Consecutive lines belonging to
one list item or blockquote become one respective block. Code content is retained as one
`code` block with line boundaries converted to a single space; fence delimiters and an
optional fence info string are omitted.

Inline projection for every non-code block uses one nonrecursive, left-to-right scanner.
Consumed input is appended to an output buffer and is never rescanned. At each position,
try these productions in order; if none matches, copy one Unicode code point literally:

1. **Escape:** `\` followed by exactly one ASCII character from
   ``!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~`` emits that character. A terminal backslash or a
   backslash before any other character remains literal.
2. **Code span:** one backtick, one or more characters other than backtick or line break,
   then one backtick emits the interior. A backtick adjacent to another backtick is not a
   delimiter in Phase 0.
3. **Inline image, reference image, inline link, reference link**, in that order, using
   the exact grammar below. Emit only the label/alt bytes. If the current position starts
   with `![` or `[`, but none of those four productions matches, append the remainder of
   that block literally and stop scanning it. This makes nested/unmatched brackets and
   unsupported destinations literal rather than partially reinterpreting a suffix.
4. **Strong, strike, emphasis**, trying `**`, `__`, `~~`, `*`, `_` in that order. An
   opener/closer is a maximal run of exactly that token's character and length; a run of
   any other length is copied as a whole and cannot be reconsidered from its second code
   point. Use the first later eligible matching run as the closer. A pair matches only
   when its interior is nonblank, begins and ends with a non-whitespace code point, and
   contains none of ``\`*~_![]`` or a line break. Emit the interior.

The link/image grammar is deliberately smaller than CommonMark:

```text
LABEL = 1*( any code point except "[", "]", "\\", or line break )
DEST  = *( any code point except whitespace, control, "(", ")", or "\\" )
REF   = LABEL
inline-link     = "[" LABEL "](" DEST ")"
reference-link  = "[" LABEL "][" REF "]"
inline-image    = "![" LABEL "](" DEST ")"
reference-image = "![" LABEL "][" REF "]"
```

`DEST` may be empty; `LABEL` and `REF` may not. Nesting, escaped characters inside these
productions, balanced/nested parentheses, shortcut/collapsed references, multiline forms,
and recursive inline interpretation are unsupported and therefore copied literally. For
example, an emphasis marker emitted from inside a link label is not processed again.
Code blocks bypass this scanner entirely.

Unmatched, partially matched, overlapping, or unsupported Markdown syntax remains
literal text. Raw HTML is never parsed
or inserted: `<script>alert(1)</script>` is displayed as inert literal text. Link and
image destinations are never fetched. YAML front matter, tables, task-list semantics,
footnotes, definitions, autolinks, embedded HTML, and CommonMark edge-case parity are not
Phase 0 features.

## 9. EPUB ZIP/OCF processing

### 9.1 Capability gate

All generic formats require `File.arrayBuffer` (or an equivalent local Blob read),
`TextDecoder`, and basic typed arrays. EPUB XML parsing additionally requires
`DOMParser`. Deflated ZIP entries require successful construction of
`new DecompressionStream("deflate-raw")`.

Capability checks are performed at use time:

- Stored-method EPUB entries can be read without `DecompressionStream`.
- If any required entry uses deflate and `deflate-raw` is unavailable or throws, reject
  with an actionable message: the browser cannot unpack this EPUB; use TXT/Markdown or
  a browser with raw-deflate support.
- There is no network conversion, CDN polyfill, dynamically loaded script, or bundled
  decompression fallback.

TXT/Markdown ingest remains available when EPUB-only capabilities are absent. A missing
optional position-digest capability degrades as described in section 13; it does not
block reading.

### 9.2 ZIP records

The ZIP reader SHALL parse the End of Central Directory (EOCD) and central directory; it
must not scan local-file signatures and hope they are entries.

- Search backward for EOCD only within the legal maximum EOCD-plus-comment tail (65,557
  bytes) and require the comment length to end exactly at the source length.
- Reject multi-disk archives, inconsistent entry counts, central-directory ranges outside
  the source, trailing data, ZIP64 locators/records, or ZIP64 sentinel values.
- Validate every central header, local-header offset, local header, filename/extra range,
  compressed-data range, and non-overlap/bounds relationship before reading data.
- Require the local header's raw filename, compression method, and interpretation flags
  to match its central record. Reject ZIP64 extra-field IDs even when the corresponding
  32-bit field is not a ZIP64 sentinel.
- The central-directory compressed size, uncompressed size, CRC-32, flags, and method are
  authoritative. The only permitted general-purpose flag bits are `0x0808` (bits 3 and
  11) for method 0 and `0x080e` (bits 1, 2, 3, and 11) for method 8; in both cases the
  actual flags must be a subset of that mask. Any non-ASCII filename requires bit 11;
  ASCII filenames may set or clear it. Thus encryption, patched/strong encryption,
  enhanced deflate, and every other interpretation-changing flag fail closed.
- When bit 3 is clear, local CRC-32 and sizes must equal the central values. When bit 3 is
  set, parse exactly one descriptor immediately after compressed data: either 12 bytes
  (`crc32`, `compressed-size`, `uncompressed-size`) or the same fields preceded by the
  four-byte `0x08074b50` signature. Its fields must equal the central values. ZIP64
  descriptors are rejected. With bit 3 set, the three local-header fields must be either
  all zero or all equal to the central values; mixed or different values are rejected.
- For method 0, compressed and uncompressed sizes must match.
- For method 8, feed only that entry's compressed byte range to raw-deflate and consume
  output with a bounded stream reader. Do not use an unbounded `Response(...).arrayBuffer()`.
- Actual output length must equal the central-directory uncompressed size.
- For every referenced entry that is decompressed, compute CRC-32 over actual uncompressed
  bytes and require it to equal the central value.
- Any mismatch is a hard error.

Sort local records by local-header offset. Each record interval begins at its local header
and ends after its filename, extra field, compressed data, and required descriptor. The
first interval begins at byte 0; each interval ends exactly where the next begins, and the
last ends at the central-directory offset. Intervals may not overlap one another or the
central directory, and unexplained gaps are rejected. A descriptor signature occurring
inside compressed data has no special meaning because the data end comes from the central
size.

The OCF `mimetype` record must have its local header at byte 0 (this is the meaning of
“first local entry”), have the exact filename `mimetype`, use method 0, clear bit 3, have
no local extra field, and contain exactly the ASCII bytes `application/epub+zip`.
Directory entries may appear later and are ignored after structural/path validation.

### 9.3 Archive path canonicalization

EPUB ZIP entry names are decoded as strict UTF-8. A name is canonicalized by Unicode NFC
normalization and slash-delimited segment processing. Reject an entry name that:

- is invalid UTF-8, empty (except a directory marker), absolute, drive-letter-prefixed,
  or begins with `//`;
- contains NUL, backslash, an empty interior component, `.` or `..` component;
- resolves outside the archive root; or
- canonicalizes to a name already present (including file/directory collisions).

Trailing `/` marks a directory and is removed only for collision checking. Names are
case-sensitive. The importer never materializes paths on a filesystem.

All central entries are path-validated even if they are not referenced by the book. This
prevents a package with a benign spine and a malicious hidden entry from being accepted.

## 10. EPUB container and OPF

### 10.1 Safe XML parsing

Namespace identity is exact:

| Vocabulary | Namespace URI |
|---|---|
| OCF container | `urn:oasis:names:tc:opendocument:xmlns:container` |
| OPF package | `http://www.idpf.org/2007/opf` |
| XHTML | `http://www.w3.org/1999/xhtml` |
| Dublin Core elements | `http://purl.org/dc/elements/1.1/` |
| EPUB attributes | `http://www.idpf.org/2007/ops` |
| XML attributes (`xml:id`, `xml:base`) | `http://www.w3.org/XML/1998/namespace` |

Prefix spellings never establish identity. Before `DOMParser(..., "application/xml")`,
each resource passes its byte limit and this decoder:

1. UTF-8 BOM selects fatal UTF-8; UTF-16LE/BE BOM selects the matching fatal decoder;
   without a BOM, fatal UTF-8 is the only choice. Remove the BOM.
2. If an XML declaration is present, it must start at the first decoded code point and be
   syntactically accepted by the XML parser. The pre-parser's quote-aware lexical scanner,
   not a regular expression, extracts at most one case-sensitive `encoding` pseudo-
   attribute from the declaration; duplicate attributes or an unterminated declaration
   are hard errors. Normalize only the encoding value's ASCII case. UTF-8 bytes may
   declare only `UTF-8`; UTF-16LE bytes may declare `UTF-16` or `UTF-16LE`; UTF-16BE bytes
   may declare `UTF-16` or `UTF-16BE`. An absent encoding declaration is allowed. Any
   other label, a UTF-16 declaration without a BOM, or any BOM/declaration conflict is a
   hard error.
3. A decoded U+0000 is a hard error.

DOCTYPE handling uses a lexical state machine over decoded code points before DOMParser,
not a regular expression. The scanner consumes ordinary markup, processing instructions,
quoted attribute values, XML comments (`<!--` through the first legal `-->`), and CDATA
sections (`<![CDATA[` through `]]>`), rejecting an unterminated or lexically invalid
construct. A `DOCTYPE` token is recognized ASCII-case-insensitively only after the exact
markup opener `<!` and outside comments, CDATA, processing instructions, and quotes.
Container and OPF documents permit no DOCTYPE. XHTML permits exactly the token sequence
`<!`, `DOCTYPE`, one-or-more XML whitespace characters, `html`, optional XML whitespace,
`>`; `DOCTYPE` and `html` are ASCII-case-insensitive. Any extra token, quote, internal
subset, `SYSTEM`/`PUBLIC` identifier, or second declaration is rejected. This scanner
cannot make content hidden in a comment or CDATA active; DOMParser remains the final
well-formedness check.

A parser error node or a document without the expected namespace URI plus root
`localName` is a hard error.

No parsed node is adopted into the application DOM. The importer reads attributes and
text into plain strings only.

### 10.2 `container.xml`

The package must contain `META-INF/container.xml` whose root is OCF `container`. From its
OCF `rootfiles/rootfile` elements, choose the first in document order whose `media-type`
is `application/oebps-package+xml`. Its `full-path` is resolved using section 10.3 and
must name a present non-directory entry. No supported rootfile is a hard error.

### 10.3 Package-relative hrefs

Every OPF/nav href used for lookup is resolved relative to its containing resource:

1. count literal `#` characters; more than one is a hard error. Split the single optional
   delimiter. An empty path is allowed only when a nonempty fragment is present and then
   resolves to the containing resource; an empty fragment is rejected. Queries are
   rejected;
2. reject a scheme, authority, absolute path, backslash, NUL, or any other empty path;
3. percent-decode each path component as strict UTF-8;
4. reject percent-encoded `/`, `\`, or NUL before decoding;
5. normalize path components and reject traversal above archive root;
6. NFC-normalize and look up the exact case-sensitive canonical archive name.

Fragments are percent-decoded as strict UTF-8 and NFC-normalized for ID lookup. They are
not treated as CSS selectors. External nav links are ignored with a warning; external
manifest/spine resources are a hard error. A nonempty `xml:base` is outside Phase 0 and
causes a hard error rather than ambiguous resolution.

### 10.4 OPF manifest and metadata

The package root must be OPF `package`. Its trimmed `version` is accepted only if it is
the single ASCII digit `3`, or `3`, `.`, and one or more ASCII decimal digits with no
other characters; this requires OPF major version 3. The importer SHALL:

- require unique, nonblank manifest `id` values;
- require every manifest `href` to resolve to one unique archive file;
- reject duplicate canonical manifest resources;
- identify exactly one navigation item by the whitespace-token `nav` in `properties`;
- require the nav item media type `application/xhtml+xml`;
- take the first nonblank Dublin Core `title` in document order as book title, falling
  back to the `.epub` filename stem, then `Untitled EPUB`;
- reject OPF fallback chains, remote resources, and scripted-content properties as
  unsupported rather than execute or follow them.

Unreferenced manifest resources may have unsupported media types because they are never
read. The nav and linear spine resources may not.

### 10.5 Spine semantics

Process `spine/itemref` elements in document order.

- `idref` must resolve to a manifest item.
- Duplicate `idref` values are a hard error.
- `linear="no"` items are skipped with a warning.
- An included item must be `application/xhtml+xml`, local, present, and within limits.
- Fixed-layout package metadata, a fixed-layout item property, or scripted spine content
  is a hard unsupported-format error.
- Page progression direction, rendition CSS, and spread properties do not affect the
  extracted text order.
- At least one included spine resource must yield prose after extraction.

The importer does not silently use manifest order, ZIP order, or nav order as reading
order. OPF linear spine order is authoritative.

## 11. EPUB XHTML text extraction

For each included spine resource, parse XHTML as safe XML and require an XHTML-namespace
`html` root and XHTML `body`. Remove from consideration XHTML `head`, `script`, `style`,
`noscript`,
`template`, `audio`, `video`, `canvas`, `svg`, `math`, `object`, `embed`, and form-control
subtrees. Images and CSS are not loaded; an image's `alt` text is not prose in Phase 0.

Traverse the body in DOM order. Candidate block elements are:

```text
h1 h2 h3 h4 h5 h6 p li blockquote pre dt dd figcaption
```

For a candidate block, collect descendant text while excluding the removed subtrees and
without separately emitting nested candidate blocks. For a container outside a candidate
block, recurse into children and collect otherwise-unclaimed direct text into a prose
block at its document position. `<br>` contributes one space. HTML/XML whitespace,
including nonbreaking space, collapses to one ASCII space; trim the result. A nonblank
heading is a `heading` block; the other named types map to the common kinds in section 5.

Record every nonblank unqualified `id` or XML-namespace `xml:id` on the candidate block or
one of its descendants. If both attributes on one element normalize to the same value,
they are one alias; if they differ, they create two anchors. An anchor maps to the
containing emitted block; an anchor between blocks maps to the next emitted block in that
resource. Reuse of a normalized anchor by any different element in the resource is a hard
error. An anchor with no following block is unresolved.

The extraction is text-only. CSS display state, generated content, ruby positioning,
bidirectional layout, `hidden`, `aria-*`, and visual page breaks do not change reading
order in Phase 0. This limitation is an explicit compatibility boundary, not permission
to execute stylesheet or script content.

## 12. EPUB nav TOC and chapter mapping

In the manifest item marked `nav`, find the first XHTML-namespace `nav` whose exact EPUB-
namespace `type` attribute contains the whitespace token `toc`. No such element is a hard
error;
EPUB 2 NCX fallback is out of scope.

Walk its descendant ordered-list/list-item structure in document order. Each list item
contributes at most one candidate from its first direct `a` element with a nonblank label
and local `href`. A direct `span` label without a link is structural only and does not
create a candidate. Nested lists are still walked. Label whitespace collapses to one
space.

Resolve a candidate href as follows:

- A path not present in the included linear spine is ignored with a warning.
- A path with no fragment maps to that resource's first emitted block.
- A fragment maps through the anchor table in section 11.
- A missing/unresolved fragment is ignored with a warning.

The common projector applies the content-preserving heading rule in section 5, then
deduplicates same-segment candidates and emits the final flat chapter array.

A valid EPUB nav may yield no usable chapter candidates after skipping auxiliary or
broken links. That is a soft condition: reading follows spine order with no chapter
array, and the user sees one warning. The nav document itself is nevertheless mandatory
for the Phase 0 EPUB 3 boundary.

## 13. Resume identity and one-session persistence

Title alone is not a safe resume key: two books may share a title, and a revised file may
reuse one. For generic input, calculate identity from the exact selected source bytes:

```text
generic-v1:<format>:<lowercase SHA-256 hex>
```

Use `crypto.subtle.digest("SHA-256", bytes)` when available. The version prefix covers
the projection rules in this specification; a future incompatible projector increments
the version rather than silently reusing positions.

If SubtleCrypto SHA-256 is unavailable, reading still works. Generate a session-scoped
opaque identity, persist it with the derived `session/last` record, and label the resume
record as non-reimport-stable. Restoring that last record continues to work; reselecting
the original source is treated as a new import. No weak filename/title hash is presented
as content identity.

For a generic document:

- the position key is derived from source identity, never title;
- the saved position is `{seg}` (an integer in range), with no meaningful `t`;
- the current scroll/page anchor is saved on page turns, throttled scroll settling,
  chapter navigation, `pagehide`, and hidden-document flush;
- restore clamps a stale/out-of-range segment to 0 and opens the page containing that
  segment without waiting for audio metadata;
- the IndexedDB key remains exactly `session/last` and its record adds mode, identity,
  format, derived `doc`, and `savedAt`; it does not store the original source bytes;
- importing another generic or audio book replaces only `session/last`, as today.

The Resume button opens a saved generic document immediately. It must not ask for an
audio file. Existing audio records without the new mode field retain their current
reselect-audio fallback, so no destructive IndexedDB migration is required.

Commit and failure ordering is exact:

1. Allocate a monotonically increasing generation token on file selection and recheck it
   after every awaited read, digest, decompression, parse stage, and immediately before
   visible commit. A stale generation exits without touching doc, UI, position, or session.
2. Only a current, fully validated result performs the synchronous doc/mode/identity,
   `build()`, visible-layout, and ordered restore commit defined in section 4.3.
3. After visible restore, enqueue the `session/last` replacement on one serialized write
   queue. Recheck the token immediately before opening/using its read-write transaction.
   Queue order ensures a newer committed import's write is last even if an older write was
   already in flight.
4. Use one IndexedDB `put` transaction and never delete the old record first. Transaction
   abort, quota exhaustion, unavailable IndexedDB, or write error leaves the previous
   `session/last` record intact. The newly opened book remains readable, but the UI emits
   a warning that resume was **not saved**; it must not show a successful-resume claim.
5. A position write is enabled only after the current generation's restore completes and
   uses the new identity key. A failed/stale import never writes either its prospective
   key or the prior book's key.

## 14. Errors and warnings

Hard errors abort the import. They include:

- unsupported extension or encoding, invalid decoded text, empty output, or a resource
  ceiling breach;
- malformed/truncated/inconsistent ZIP records, path hazards, encryption, ZIP64,
  unsupported compression, size/ratio/CRC mismatch, or ZIP-bomb indicators;
- missing/invalid OCF mimetype, container, rootfile, OPF, nav, or linear spine;
- malformed XML, dangerous/unsupported DTD form, ambiguous IDs/resources, invalid href,
  traversal, missing spine reference, unsupported included media, or fixed layout;
- a final projected document that fails the existing schema validator.

Warnings do not change ordering or fabricate content. They include:

- MIME/extension mismatch where the extension is recognized;
- skipped `linear="no"` spine items;
- ignored external or non-linear nav targets and unresolved nav fragments;
- a valid nav with no usable chapters; or
- unavailable stable content digest.

The UI shows the primary failure in plain language plus a short stage label (decode, ZIP,
package, spine, nav, or projection). An archive path included in a message is text-only,
control characters removed, and capped at 160 code points. Developer details may go to
`console.error`, but never source prose, tokens, or full archive listings.

Successful imports with warnings open normally and show one summary toast, for example
“Opened with 3 skipped TOC links.” Warning order is deterministic. No warning may excuse
a security, bounds, ordering, or contract failure.

## 15. Security properties

The implementation is complete only if all of these hold:

- **Local-only:** from the generic input's change event through its commit/error and the
  next animation frame, work causally initiated by that import performs zero `fetch`,
  XHR, beacon, navigation, window-open, dynamic script, external stylesheet, font, media,
  image, or other URL-backed resource request. Existing Dropbox code is outside this
  assertion when idle and not invoked by the import; Phase 0 does not remove it.
- **No active markup:** XHTML and Markdown become strings; only the existing `el()` and
  `textContent` DOM path renders them. No imported string reaches `innerHTML`, `src`,
  `href`, CSS, an event-handler attribute, or executable URL.
- **No filesystem extraction:** ZIP paths are identifiers into an in-memory byte source,
  never write targets.
- **Bounded work:** central claims are checked before allocation, actual decompression is
  streamed and bounded, and final document size is capped.
- **Integrity:** sizes and CRC-32 are checked, not merely trusted.
- **Determinism:** spine order, path resolution, segmentation, chapter resolution, and
  logical coordinates are defined by this specification, not browser layout or ZIP
  enumeration.
- **Atomicity:** a rejected or superseded import cannot replace visible or persisted
  state.

The implementation must continue to satisfy the repository safe-pattern rule: no new
`innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, or dynamic code
execution.

## 16. Fixtures and verification

All committed fixtures SHALL be synthetic/public-domain micro-content and comply with
the repository IP guard (no file over 500 narrative words; no audio). Large/bomb cases
are crafted headers or generated in temporary test storage, not committed payloads.

### 16.1 Required positive fixtures

1. UTF-8 TXT without BOM, mixed newline styles, multiple paragraphs.
2. UTF-8-BOM TXT and UTF-16LE/BE micro-text.
3. Markdown containing ATX and Setext headings, paragraph wrapping, list, quote, fenced
   code, link/image labels, inline markers, raw HTML text, and unsupported literal syntax.
   Its table-driven cases include nested/unmatched brackets, escaped delimiters, adjacent
   and overlapping delimiter runs, nested parentheses, empty destinations, terminal
   backslash, markers inside code, and link labels containing markers; each case asserts
   the exact literal-or-transformed output from section 8.
4. Stored EPUB with exact mimetype, nested OPF directory, two linear XHTML resources,
   heading targets, nested nav TOC, percent-encoded Unicode href/fragment, one
   `linear="no"` resource, and active/remote-looking content which must remain inert.
5. Deflated equivalent of the valid EPUB.

Each positive fixture has a checked-in expected projected JSON document small enough to
inspect. Expected JSON uses the exact logical-coordinate and chapter rules here.

### 16.2 Required negative fixtures/cases

- invalid UTF-8, unsupported BOM, interior NUL, and whitespace-only text;
- missing/false EOCD, EOCD count/range mismatch, trailing data, multi-disk, ZIP64,
  every forbidden flag bit, invalid method-specific flag combination, missing/extra/bad
  descriptor, local/central disagreement, local-record gap/overlap, encrypted entry,
  unsupported method, data range outside source, stored-size mismatch, CRC mismatch, and
  truncated deflate;
- `../`, absolute, backslash, encoded-separator href, root escape, invalid UTF-8 name,
  duplicate canonical name, and file/directory collision;
- declared oversize entry, declared total oversize, high compression ratio, and a stream
  whose actual output exceeds its declaration/limit;
- missing or misplaced/compressed/wrong `mimetype`, missing container/rootfile/OPF/nav,
  wrong namespace, non-3 OPF version, BOM/declaration conflict, unsupported encoding,
  malformed XML, DOCTYPE hidden-case/comment/CDATA boundary cases, dangerous DTD, invalid
  `xml:base`, duplicate manifest ID/resource,
  missing/duplicate spine IDREF, unsupported linear media, fixed layout, missing body,
  same-element ID alias, cross-element duplicate anchor ID, empty-path fragment, multiple
  `#`, broken fragment, terminal heading with no following prose, and spine yielding no prose.

### 16.3 Automated behavioral tests

Add a no-dependency Node test at `tests/test_generic_ingest.mjs`. On every implementation
head, `./check.sh` SHALL require `node` and unconditionally run
`node --test tests/test_generic_ingest.mjs`; missing Node, any skipped generic-ingest case,
or any failure makes the gate fail. Existing unrelated checks may retain their current
optional-Node behavior on pre-implementation/Python-only heads. Follow the existing
`test_paged_anchor.mjs` convention of exercising the shipped implementation from
`index.html`, not a copied algorithm. The implementation
SHALL factor byte parsing, decoding, Markdown projection, path resolution, semantic
assembly, common projection, and position-key logic into pure functions that the test
can extract/import or call with explicit adapters.

Automated tests SHALL assert:

- every positive TXT/Markdown fixture equals its complete expected document;
- stored and deflated ZIP decoding yields exact entry bytes;
- OPF/spine/nav semantic assembly yields the expected complete EPUB document;
- all negative cases fail with the expected stable error category;
- decompression stops at actual-output ceilings;
- superseded/failed imports do not invoke the commit/open callback;
- heading removal never drops a terminal heading; href/anchor alias cases follow sections
  10.3 and 11 exactly;
- same bytes produce the same stable identity and distinct bytes do not share the tested
  identity;
- final docs pass a JavaScript contract validation; and
- generic position restore is segment-based and title collisions do not collide;
- every text-mode seam in the section 4.3 matrix avoids audio state, and lifecycle flush
  persists the latest segment even inside the 1,500 ms throttle window; and
- stale-generation and simulated IndexedDB quota/write failures preserve the prior
  session, order newer commits last, and surface “not saved” rather than success.

Node does not provide a portable browser `DOMParser`. To avoid pretending a parser stub
tests browser XML behavior, split XML handling into (a) a thin DOM-to-plain-model adapter
and (b) pure semantic assembly. Node tests cover (b) exhaustively. An in-browser fixture
harness covers (a) and the end-to-end path below. Raw-deflate decoding is mandatory in the
Mac Node command and in the real-browser harness; lack of `deflate-raw` support is a failed
implementation environment, not a skip. The shipped browser runtime still gives end users
the fail-closed capability message in section 9.1.

Static source-presence assertions are not substitutes for behavioral tests. A test must
fail when the relevant runtime result or security outcome is wrong.

### 16.4 Real-browser acceptance check

Provide a synthetic fixture page or documented local procedure which uses the actual
browser `DOMParser`, `DecompressionStream`, file input, IndexedDB, localStorage, and
reader DOM. On the Mac, run it in at least one current browser with `deflate-raw` support.
Record this separately from `./check.sh` because device/browser UI verification is not a
logic-test substitute.

The check SHALL demonstrate, by observable behavior:

1. load the deflated EPUB from the normal Load control;
2. confirm its title, TOC-derived chapter headers, spine order, and expected prose;
3. with Dropbox idle, instrument `fetch`, XHR open/send, `sendBeacon`, window/navigation
   entry points, URL-bearing DOM mutations, and new resource-timing entries from input
   change through commit plus the next animation frame; confirm a link/script/image in
   source causes no import-attributable navigation, execution, or resource request;
4. switch Scroll/Pages, turn multiple pages, and change font size;
5. navigate from the chapter sheet without starting audio;
6. move to a later page, reload, choose Resume, and return to that page without selecting
   the EPUB or an audio file;
7. import a same-title/different-bytes fixture and confirm its position is independent;
8. attempt one traversal and one bomb-limit fixture and confirm the open book/session is
   unchanged; and
9. load TXT and Markdown through the same control and confirm their expected chapters
   and text projection.

## 17. Acceptance criteria

Phase 0 is complete only when all are true:

- A real, stripped, conforming EPUB 3 passes the defined compatibility boundary and
  renders through the existing reader.
- TXT and the defined Markdown subset import through the same generic-file control.
- Projected output matches the existing document contract, including logical coordinates;
  `pipeline/schema.py` and `validateDoc()` are not weakened.
- `build()` has no diff, and there is no alternate renderer.
- Chapter headers follow Markdown headings or EPUB nav targets; reading order follows the
  OPF linear spine.
- Paging, page turns, scroll mode, and font size work in text mode.
- Generic resume is content-identity/segment based, opens from `session/last` without
  audio, and remains one-book-at-a-time.
- Text mode never starts or seeks audio and does not expose misleading time controls.
- ZIP traversal, ambiguity, encryption, corruption, bombs, malformed package structures,
  and unsupported capabilities fail closed and atomically.
- Imported content cannot become active DOM or trigger network access.
- Required fixture tests pass; real-browser results are recorded separately.
- `./check.sh` and `git diff --check` pass.
- No dependency, package metadata, build step, network fallback, tracked third-party
  narrative content, or unrelated roadmap feature is added.

## 18. Implementation sequence and reviewable slices

This sequence is guidance for one Phase 0 implementation PR; it is not permission to
merge partially safe ingest.

1. Add pure byte/encoding/path/ZIP primitives and their negative tests.
2. Add TXT and Markdown adapters plus complete expected-document tests.
3. Add safe XML adapters, OCF/OPF/spine/nav assembly, XHTML text extraction, and EPUB
   fixtures/tests.
4. Add common projection and validate every output against the existing JS contract.
5. Add the Load control and atomic generation-token orchestration.
6. Add text-mode transport/chapter guards and segment-based persistence without touching
   `build()`.
7. Run `./check.sh`, `git diff --check`, and the real-browser acceptance procedure.

Implementation review should attack the boundary rather than confirm it: mutate a size
claim, CRC, path, href, fragment, spine order, nav target, import generation, same-title
identity, and text-mode transport guard and verify the corresponding behavioral test or
acceptance check fails.

## 19. Six-lens review-driven changes

- **Contract/determinism:** froze a nonrecursive Markdown scanner and preserved terminal
  heading content instead of removing it without a valid remap target.
- **Standards/compatibility:** fixed all OCF/OPF/XHTML/DC/EPUB/XML namespace identities,
  OPF major version, BOM/declaration consistency, lexical DOCTYPE handling, and exact
  empty-path/fragment/anchor-alias rules.
- **Security/adversarial:** fixed method-specific ZIP flags, descriptor and local-record
  ranges, byte-zero `mimetype`, conservative resource ceilings, referenced-only bounded
  decompression, and the import-scoped zero-network assertion.
- **Live integration:** added guards for every current audio/navigation/settings handler,
  a throttle-bypassing lifecycle flush, and restore ordering independent of audio metadata.
- **Persistence/failure:** specified generation checks, serialized last-session writes,
  atomic IndexedDB replacement, quota/write warnings, and preservation of the old session.
- **Verification/build readiness:** made the generic-ingest Node suite and raw-deflate case
  mandatory on implementation heads, retained a real-browser deflate/network harness, and
  added adversarial fixtures for each repaired boundary.
