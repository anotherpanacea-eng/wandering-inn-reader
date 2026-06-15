# Inn Reader

Listen to the Parsneau narration and read the same words in sync, on your phone.
Built for following along with *The Wandering Inn*, but it works for any
audiobook you have the text for.

Two pieces:

1. **`index.html`** — the player. A single file, no install, no server required.
   Open it on your phone, load an audio file and a sync file, and the text
   highlights and scrolls as the audio plays. Tap any line to jump there.
   **Switch between listening and reading freely:** pause, read ahead at your own
   pace (swipe or PageUp/PageDown to turn **pages**), then press play and the
   narration continues from the line your eyes left off on — and the reverse, since
   listening moves the same place-marker. It remembers your position — to the page —
   and offers one-tap **Resume** next time. It also has: **chapter navigation** (a ☰
   menu + inline chapter headers, when the sync file carries chapters), a **page
   counter**, **lock-screen / headphone controls** (play, pause, ±15s, chapter skip,
   now-playing title — so you can pocket the phone), **text size**,
   **keep-screen-on** while playing, and a **sleep timer** (15/30/45 min or
   end-of-chapter). Tap **Aa** for those; everything persists.
2. **`pipeline/`** — a one-time job you run on your Mac (or the GPU PC) to produce
   the sync file. It does **forced alignment**: it takes the audio plus the text
   that already exists and computes the timestamps. It does not transcribe, so you
   read the real words, not a machine's guess.

---

## Try it right now (no setup)

Open `index.html` on your phone or laptop and tap **Try the demo**. It plays the real
**opening line of *The Wandering Inn*** (narrated by Andrea Parsneau — a short
in-limits excerpt) so you can see the read-along: the sentence brightens, each word
glows as it's spoken, the page follows, and tapping a line seeks the audio. Pause and
the reader becomes a book — turn pages, read ahead, then play to continue from where
you read. The "Follow" button toggles auto-scroll; the ☰ menu shows the chapter and
page, and **Aa** has text size, keep-screen-on, and the sleep timer.

To get it onto your iPhone: put this folder in iCloud Drive or Dropbox, open
`index.html` from the Files app in Safari. For your real audio later, use the two
**Load** buttons (audio file + sync `.json`). Everything runs locally on the
device; nothing uploads.

---

## Put it on a phone (free — no GitHub Pro needed)

The player is one static HTML file: no server, no build, nothing to install. A phone
just needs to open it in a browser.

### It's already hosted — just open the link

> ### 📱 [anotherpanacea-eng.github.io/wandering-inn-reader](https://anotherpanacea-eng.github.io/wandering-inn-reader/)

Open that on your phone and **Add to Home Screen** (iOS: **Share → Add to Home
Screen**; Android Chrome: **⋮ → Add to Home screen / Install app**). The web-app
manifest launches it **full-screen, like a native app**. That's the whole setup.

(Hosted free on GitHub Pages because the repo is public — the IP guard guarantees no
copyrighted audio/text is in it.)

### Hosting your own copy (a fork)

If you fork it, any free static host works — no GitHub Pro needed:

- **Netlify Drop** — drag the project folder onto **[app.netlify.com/drop](https://app.netlify.com/drop)**
  for an instant URL. No account, no config, nothing to connect. Best for sending to a
  friend. (Sign in if you want the link to stick around.)
- **Cloudflare Pages** — dash.cloudflare.com → Pages → *Connect to Git* → pick this
  repo. Build command: **(leave empty)**. Output directory: **`/`**. Free, works with
  **private** repos, redeploys on every push.
- **Netlify / Vercel from Git** — same idea: import the repo, empty build command,
  publish directory `/`. Free tier, private repos fine.
- **Or make the repo public + GitHub Pages** — free for *public* repos, and safe now:
  the IP guard guarantees no copyrighted audio/text ships. Settings → Pages → deploy
  from `main` / root.

Then on the phone, open the URL and **Add to Home Screen** — iOS: **Share → Add to
Home Screen**; Android Chrome: **⋮ → Add to Home screen / Install app**. Thanks to the
web-app manifest it launches **full-screen, like a native app**.

### No hosting at all (open the file locally)

Put the folder in a cloud drive and open `index.html` in the mobile browser:

- **Android** — Files/Drive → open `index.html` in Chrome. Works.
- **iPhone** — iOS Safari is finicky about local `.html` from the Files app; hosting
  (above) is much smoother. If you must: **Files** app → long-press `index.html` →
  **Share** → open in a browser.

### Using it once it's open

1. Tap **Try the demo** to see the read-along immediately, no files.
2. For real listening: **Load audio file** (your `.mp3`/`.m4a`) + **Load sync file**
   (the `.json` from the pipeline) → **Open reader**.

**Everything stays on the device — nothing is uploaded.** Your spot is remembered, and
**Resume** brings you back next time. The phone is **playback only** — making a sync
file (forced alignment) needs a computer (see below). Hand a friend a `.json` and point
them at their own legally-owned audio, and they can read along on their own phone.

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
# 1) decode mp3 → wav. On macOS this is built in (no ffmpeg needed):
afconvert -f WAVE -d LEI16@16000 "$TRACK" track.wav
# 2) align:
python3 align_torch.py --audio track.wav --text track.txt \
  --chapters book12.chapters.json --title "Book 12 — track 02" --out align.json
```

Why the wav step: torchaudio ≥ 2.11 decodes compressed audio through `torchcodec`,
which needs ffmpeg — so `align_torch.py` reads **wav** (via `soundfile`/stdlib, no
ffmpeg) and you pre-convert with `afconvert` (mac) or ffmpeg. Device is auto-detected
(cuda → mps → cpu; ROCm shows up as `cuda`). Align one track at a time — a single
pass over the whole volume is memory-heavy.

> **Proven on your real audio.** This path ran end-to-end against Book 12 track 02:
> `afconvert` decode → `align_torch.py` → 60 sentences / ~1,000 word timings,
> correctly placed (e.g. "Dawn struck the city of Manus…" at 8.4–14.1s). A 6-minute
> sample sync (`samples/`, git-ignored) loads straight into the player. Note the **37
> audio tracks don't line up with the 19 web chapters** — track 01 is a 16-second
> credits clip; chapter text starts in track 02. For clean chapter headers, align the
> whole concatenated volume against `book12.txt`.

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

### Getting the chapter list automatically: `list_chapters.py`

You no longer have to gather URLs by hand. `list_chapters.py` reads the live Table
of Contents and emits a URL list for any **Volume** or any audiobook **Book**, now
or in the future:

```bash
python3 list_chapters.py                          # all Volumes + chapter counts
python3 list_chapters.py --volume 6 --list        # show one Volume's chapters
python3 list_chapters.py --from 6.33 --to 6.47 --out book12_chapters.txt
python3 fetch_text.py --url-file book12_chapters.txt --out book12   # then fetch
```

The web serial is grouped into **Volumes**; a published audiobook **Book** is a
*slice* of a Volume. Because the TOC is in reading order, slicing between the first
and last chapter also pulls in the **Interludes** that sit between numbered chapters
— which a bare "6.33–6.47" number filter would miss. `--from`/`--to` match the URL
slug and are inclusive.

**Which chapters is *The Witch of Webs* (Book 12)?** Resolved: web chapters
**6.33 E → 6.47 E** (Volume 6), which is **19 chapters** once the four interludes
(Numbtongue Pt.1 & Pt.2, Two Rats, Rufelt) are included. That list ships as
[`pipeline/book12_chapters.txt`](pipeline/book12_chapters.txt), ready to fetch.
Book 12 was *rewritten*, but the web pages have since been re-uploaded with the same
rewritten text the audiobook narrates — so aligning the audio against the current
web text matches well.

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

## Can this sync with Audible?

Short answer: **not with the Audible app** — but you can read along with an Audible
book you own by taking the audio out first.

- **No live sync, no API.** Audible exposes no playback position and no API a third
  party can read, and the audio is DRM-locked (`.aax`/`.aaxc`, tied to your account).
  There's no way to make this player *follow* the Audible app as it plays.
- **The workaround is format-shifting.** This player only needs a plain audio file
  plus the matching text. If you own the audiobook, you can export it from Audible to
  a DRM-free file (an `.m4b`) and then it's just another input to the pipeline — align
  it and read along here instead of in the Audible app. Tools people use for this are
  **Libation**, **OpenAudible**, and **audible-cli**. Note the legal nuance: removing
  DRM is for **personal format-shifting of audio you've bought**, a legal gray area
  under the DMCA — fine for your own listening, not for sharing or resale.
- **Bonus: m4b files carry chapter markers.** A decrypted audiobook `.m4b` has
  embedded, time-stamped chapter markers. `ffprobe`/`ffmpeg` can read them straight
  out — so for an Audible book you get the chapter menu *without* scraping a TOC. (A
  small `chapters_from_audio.py` could turn those into the player's chapter format and
  map them onto segments after alignment — ask if you want it.)

For *The Wandering Inn* specifically this is moot: you already have the Parsneau
audio as plain `.mp3` tracks, so just run the pipeline on those.

## Respecting the author's & narrator's IP

This is a personal read-along tool, not a way to redistribute the book. The only
third-party content committed is a **deliberately tiny sample** — the opening line of
*The Wandering Inn* and ~9 seconds of narration, in the demo. Hard limits on anything
committed: **≤ 20 seconds of any audio** and **≤ ~one page (500 words) of text**. The
full text you fetch and any audio you align stay on your machine (git-ignored).
`tools/check_ip_limits.py` enforces this as a pre-commit hook (`git config
core.hooksPath .githooks` to enable) — it even measures audio hidden in base64 `data:`
URIs, and fails the commit if a limit is crossed.

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
  controls, one-tap resume, text size, keep-screen-on, and sleep timer are in.
  **Read↔listen handoff via pages** (paged navigation + a single place-marker shared
  between reading and listening) is in; its page math and handoff/restore logic are
  unit-checked, the render path is logic-checked. A browser can't be driven from a
  cloud session, so the on-device UI is best confirmed by opening the demo.
- `align.py` (aeneas → JSON, now with `--chapters`): built and tested.
- `align_torch.py` (torchaudio `MMS_FA`, word-level, no aeneas): built **and run
  end-to-end on real Book 12 audio** (track 02, CPU) — 60 sentences / ~1,000 word
  timings, correctly placed. Reads wav via `soundfile`/stdlib (no ffmpeg/torchcodec).
- `list_chapters.py`: built and tested against the live TOC — generated
  `pipeline/book12_chapters.txt` (19 chapters incl. interludes).
- `fetch_text.py`: pulls real chapter titles and segment-indexed chapter markers;
  verified end-to-end on a live chapter (6.33 E → 1681 sentences).
- Forced alignment on your actual audio: **not yet run** (needs your machine).
  Untested against your files by definition. Run the track-01 loop and we iterate
  from whatever breaks.
