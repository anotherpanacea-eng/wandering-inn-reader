# Inn Reader

Listen to the Parsneau narration and read the same words in sync, on your phone.
Built for following along with *The Wandering Inn*, but it works for any
audiobook you have the text for.

Two pieces:

1. **`index.html`** — the player. A single file, no install, no server required.
   Open it on your phone, load an audio file and a sync file, and the text
   highlights and scrolls as the audio plays. Tap any line to jump there. It
   remembers your position — and offers one-tap **Resume** next time. It also has:
   **chapter navigation** (a ☰ menu + inline chapter headers, when the sync file
   carries chapters), **lock-screen / headphone controls** (play, pause, ±15s,
   chapter skip, now-playing title — so you can pocket the phone), **text size**,
   **keep-screen-on** while playing, and a **sleep timer** (15/30/45 min or
   end-of-chapter). Tap **Aa** for those; everything persists.
2. **`pipeline/`** — a one-time job you run on your Mac (or the GPU PC) to produce
   the sync file. It does **forced alignment**: it takes the audio plus the text
   that already exists and computes the timestamps. It does not transcribe, so you
   read the real words, not a machine's guess.

---

## Try it right now (no setup)

Open `index.html` on your phone or laptop and tap **Try the demo**. Short original
passage, a placeholder tone track, just to show the read-along behavior: the
current sentence brightens, the current word glows, the page follows, and tapping
a line seeks the audio. The "Follow" button toggles auto-scroll. The demo carries
two chapters, so the ☰ menu and inline chapter headers are live too; tap **Aa** to
try text size, keep-screen-on, and the sleep timer.

To get it onto your iPhone: put this folder in iCloud Drive or Dropbox, open
`index.html` from the Files app in Safari. For your real audio later, use the two
**Load** buttons (audio file + sync `.json`). Everything runs locally on the
device; nothing uploads.

---

## Your files

Your Book 12 lives at:

```
Reading/The Wandering Inn/The Wandering Inn 12 The Witch of Webs.mp3/
    01 - The Witch of Webs ... .mp3   ... through ...   37 - ... .mp3
    The Witch of Webs.jpg
```

37 numbered tracks. The aligner wants one audio file and the matching text, so the
flow is: pick a unit of audio, get its text, align, convert, load.

---

## Prove it on one track first

Don't align 30 hours before you know the loop works. Start with track 01.

**1. Get the text for track 01.** Forced alignment needs the words that are spoken
in that track. Two sources:

- The **web serial** at wanderinginn.com (free, canonical, what you read).
- Your **ebook** (matches the audiobook edit more closely, since both come from
  the published version).

Recent volumes like 12 barely differ between web and audiobook, so either works.
Either paste the matching span into `pipeline/track01.txt` (one sentence per line),
or pull it from the web with the fetcher below.

**2. Align with aeneas** (recommended; see install below):

```bash
cd pipeline
TRACK="../../Reading/The Wandering Inn/The Wandering Inn 12 The Witch of Webs.mp3/01 - The Witch of Webs The Wandering Inn, Book 12.mp3"
python3 -m aeneas.tools.execute_task "$TRACK" track01.txt \
  "task_language=eng|is_text_type=plain|os_task_file_format=json" sync01.json
```

**3. Convert to the player's schema:**

```bash
python3 align.py --sync sync01.json --title "Book 12 — track 01" \
  --audio "01 - The Witch of Webs The Wandering Inn, Book 12.mp3" --out align01.json
# add chapter headers + a jump menu by passing the fetcher's markers:
#   python3 align.py --sync sync01.json --chapters book12.chapters.json ... --out align01.json
```

**4. Load `align01.json` + the track 01 mp3** into the player. If the highlight
tracks the voice, the approach is proven and we scale up.

### Or skip aeneas entirely (one command, word-level)

If you'd rather not fight the aeneas install, `align_torch.py` does the same job on
torchaudio's `MMS_FA` — it takes the audio and the text directly, aligns at the
**word** level (so the per-word glow works), and writes the player JSON in one step:

```bash
cd pipeline
python3 align_torch.py --audio "$TRACK" --text track01.txt \
  --chapters book12.chapters.json --title "Book 12 — track 01" --out align01.json
```

Device is auto-detected (cuda → mps → cpu; ROCm shows up as `cuda`). Align one track
at a time first — a single pass over a whole 30-hour volume is memory-heavy.

---

## Getting the text from the free web serial

`pipeline/fetch_text.py` pulls chapter prose straight from wanderinginn.com. The
prose is server-rendered in a standard WordPress `div.entry-content`, brackets and
all, so a plain fetch gets it cleanly.

```bash
pip3 install requests beautifulsoup4
cd pipeline
python3 fetch_text.py --out book12 --url-file book12_chapters.txt
# or pass URLs directly, in reading order:
python3 fetch_text.py --out book12 https://wanderinginn.com/.../chapter-1/ ...
```

It writes `book12.txt` (sentences, one per line) and `book12.chapters.json` (real
chapter titles, each tagged with the segment index it starts at). Pass that file to
`align.py --chapters` or `align_torch.py --chapters` and the reader gets chapter
headers and a ☰ jump menu.

**Which chapters?** Get the list from the Table of Contents
(every chapter is a server-rendered link) or the book page for *The Witch of Webs*.
One wrinkle worth knowing: the web serial is divided into **Volumes**, while the
audiobook is a published **Book**, and the two don't map one-to-one (published
books subdivide the big web volumes). So confirm exactly which chapters *The Witch
of Webs* covers before fetching. The book page lists them; I can also extract that
list for you in a follow-up.

## Installing aeneas (Mac)

aeneas is the mature audiobook-to-text sync tool. It needs ffmpeg and espeak:

```bash
brew install ffmpeg espeak
pip3 install numpy aeneas
```

If the build complains, `pip3 install "numpy<2"` first, then aeneas. On Apple
Silicon this is the usual snag; ping me and we'll sort it.

---

## Scaling to the whole volume

aeneas handles long audiobooks well. Concatenate the 37 tracks into one file, give
it the whole volume's text (one sentence per line), align once:

```bash
cd "../Reading/The Wandering Inn/The Wandering Inn 12 The Witch of Webs.mp3"
for f in [0-9]*.mp3; do echo "file '$PWD/$f'"; done | sort > /tmp/list.txt
ffmpeg -f concat -safe 0 -i /tmp/list.txt -c copy /tmp/volume12.mp3
```

Then run aeneas on `volume12.mp3` + `volume12.txt`, and `align.py` as above. The
aligner places every sentence on the timeline, so track and chapter boundaries
sort themselves out from the text.

---

## Sentence-level vs word-level

aeneas gives **sentence-level** timing, which is the heart of read-along: the right
line lights up and the page follows. The player also does **word-level** glow when
the data has it. Two ways to get word-level:

- **`align_torch.py`** (above) — torchaudio's `MMS_FA` does it in one step on the
  torch you already have. This is the simplest path to word-level.
- **`align.py --words-json`** — if some other aligner produced a flat word-timestamp
  list, `align.py` packs those words into their containing sentences by time.

---

## Licenses

All open source. The player and these scripts are dependency-free and yours, no
framework, no tracking, everything runs locally. External tools: ffmpeg (LGPL/GPL),
aeneas (AGPL-3.0), espeak (GPL), and for the optional word-level path PyTorch /
torchaudio (BSD). Two copyleft/non-commercial flags matter only if you ever tried
to *sell* this: aeneas is AGPL, and torchaudio's word-level `MMS_FA` model is
CC-BY-NC. For personal read-along, both are fine. The web text stays the author's;
this is for your own use, not redistribution.

## Status

- Player: built and working (demo included). Chapters, lock-screen / headphone
  controls, one-tap resume, text size, keep-screen-on, and sleep timer are in;
  init + the demo render path are logic-checked. A browser can't be driven from a
  cloud session, so the on-device UI is best confirmed by opening the demo.
- `align.py` (aeneas → JSON, now with `--chapters`): built and tested.
- `align_torch.py` (torchaudio `MMS_FA`, word-level, no aeneas): built; the torch
  run itself is untested in-session (needs the GPU box with torch installed).
- `fetch_text.py`: pulls real chapter titles and segment-indexed chapter markers.
- Forced alignment on your actual audio: **not yet run** (needs your machine).
  Untested against your files by definition. Run the track-01 loop and we iterate
  from whatever breaks.
