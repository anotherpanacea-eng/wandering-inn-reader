#!/usr/bin/env python3
"""
fetch_text.py — pull Wandering Inn chapter text from the free web serial and
emit a sentence-per-line file for the forced aligner, plus chapter markers.

Why this works: wanderinginn.com renders the chapter prose server-side inside a
standard WordPress `div.entry-content`. We grab the paragraphs (including the
bracketed [Skill]/[Level] lines, which the narrator reads), normalize, split into
sentences, and write them one per line. aeneas treats each line as a fragment to
place on the audio timeline.

You supply the chapter URLs. Get them from the Table of Contents
(https://wanderinginn.com/table-of-contents/) — every chapter is a link there.
For Book 12 (The Witch of Webs), use the chapters the book page lists.

Usage:
  # one or more chapter URLs, in reading order:
  python3 fetch_text.py --out book12 \
      https://wanderinginn.com/2024/.../8-01/ \
      https://wanderinginn.com/2024/.../8-02/

  # or a file with one URL per line:
  python3 fetch_text.py --out book12 --url-file book12_chapters.txt

Wayback fallback (--wayback):
  Volumes 1-2 were rewritten and the original chapters are genuinely gone (the live
  site returns 404). For those, the prose lives only in the Internet Archive. Pass
  --wayback to fetch each URL's raw-HTML Wayback snapshot into a resumable cache and
  parse from the cache with the SAME extraction logic as the live path:

      python3 fetch_text.py --out book04 --url-file book04_chapters.txt \
          --wayback --wayback-cache _wb_cache/book04 --wayback-timestamp 20180601

  The cache is resumable: each snapshot's HTML is saved and already-cached files are
  skipped, so a re-run resumes through archive.org's burst throttle. Pick a pre-paywall
  timestamp (<=2019): early-volume captures keep the full `div.entry-content` only through
  ~2019; 2020+ snapshots are post-redesign and prose-less. Default 20180601.

Outputs:
  <out>.txt          sentences, one per line, blank line between paragraphs
  <out>.chapters.json  [{title, url, seg, n_sentences}, ...] for chapter markers

The chapter marker's `seg` is the index of the chapter's first sentence among the
*non-blank* lines (i.e. the segment index aeneas/align.py will assign it), NOT the
raw line number — so chapter boundaries survive the blank paragraph-break lines and
map straight onto the player's segments. `align.py --chapters` consumes this.

Dependencies:  pip3 install requests beautifulsoup4
"""
import argparse, json, os, re, sys, time

# A real browser UA + Accept headers: the live chapter pages return 403 to bot-looking User-Agents
# (the site is up — only Volumes 1-2 are genuinely gone, returning 404). Wayback is the fallback for those.
UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---- sentence splitting (pragmatic, not perfect; alignment tolerates rough lines) ----
_ABBR = {"mr", "mrs", "ms", "dr", "st", "lt", "sgt", "capt", "vs", "etc", "no", "vol"}
_SENT = re.compile(r'(?<=[.!?…])["”’\')\]]*\s+(?=[A-Z"“\[‘])')

def split_sentences(text):
    text = text.strip()
    if not text:
        return []
    # keep a bracketed LitRPG block ([Skill ... obtained!]) as its own line
    if text.startswith("[") and text.endswith("]"):
        return [text]
    parts, out = _SENT.split(text), []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # re-glue obvious abbreviation false-splits
        if out and out[-1].rstrip(".").split()[-1].lower() in _ABBR:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out

def clean(s):
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()

def extract_title(soup, url):
    """The real chapter title (WordPress `h1.entry-title`), not the URL slug."""
    el = (soup.select_one("h1.entry-title")
          or soup.select_one(".entry-title")
          or soup.find("h1")
          or soup.find("title"))
    if el is None:
        return title_from_url(url)
    t = clean(el.get_text(" ", strip=True))
    # the <title> tag carries the site suffix ("8.01 – The Wandering Inn"); drop it
    t = re.split(r"\s+[–—|-]\s+(?:The Wandering Inn)\s*$", t)[0].strip()
    return t or title_from_url(url)

def extract(html, url):
    """Return (title, [paragraph, ...]) for one chapter page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    body = (soup.select_one("div.entry-content")
            or soup.select_one("article .entry-content")
            or soup.select_one("article")
            or soup.find("main"))
    if body is None:
        raise RuntimeError("Could not find chapter body (div.entry-content). "
                           "Site markup may have changed — adjust the selector.")
    title = extract_title(soup, url)
    paras = []
    for p in body.find_all("p"):
        t = clean(p.get_text(" ", strip=True))
        # drop site chrome that sometimes lands inside the content area
        if not t:
            continue
        low = t.lower()
        if low.startswith(("previous chapter", "next chapter", "author's note:")) and len(t) < 60:
            continue
        paras.append(t)
    return title, paras

def fetch(url):
    import requests
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text

def title_from_url(url):
    slug = [s for s in url.rstrip("/").split("/") if s][-1]
    return slug

# ---- Wayback Machine fallback (for genuinely-gone Volumes 1-2) -------------------
# The content endpoint `/web/<ts>id_/<url>` self-redirects to the nearest *raw* capture
# (the `id_` suffix strips the archive toolbar so extract() sees the original page). We
# do NOT touch the availability API — it's aggressively 429-throttled. Caching is the
# point: archive.org refuses bursts, so we save each snapshot and resume on a re-run.

def wayback_cache_name(url):
    """Stable per-URL cache filename. Must match the names already on disk so an
    existing cache hits — identical to the scratch wb_cache.py / wb_parse.py mapping."""
    return re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[-90:] + ".html"

def wayback_url(url, timestamp):
    return f"https://web.archive.org/web/{timestamp}id_/{url}"

def wayback_fetch_one(wb_url, tries=5):
    """Fetch one raw-HTML snapshot, long-backing-off through archive.org's throttle.
    Returns the bytes, or None if still refused after `tries` attempts (caller resumes
    on the next run). Uses requests (already the live-path dep)."""
    import requests
    for k in range(tries):
        try:
            r = requests.get(wb_url, headers=UA, timeout=45)
            if r.status_code == 200 and len(r.content) > 5000:
                return r.content
        except Exception:
            pass
        time.sleep(20 * (k + 1))                    # 20,40,60,80s backoff on throttle
    return None

def wayback_build_cache(urls, cache_dir, timestamp, sleep):
    """Populate `cache_dir` with each URL's raw-HTML snapshot, RESUMABLY: skip files
    already cached, long-backoff on connection-refused. Returns the list of URLs still
    missing after this pass (empty == complete)."""
    os.makedirs(cache_dir, exist_ok=True)
    missing = []
    for i, u in enumerate(urls):
        cf = os.path.join(cache_dir, wayback_cache_name(u))
        if os.path.exists(cf) and os.path.getsize(cf) > 5000:
            print(f"  [{i + 1:02d}] cached (skip)  {u}")
            continue
        data = wayback_fetch_one(wayback_url(u, timestamp))
        if data:
            with open(cf, "wb") as f:
                f.write(data)
            print(f"  [{i + 1:02d}] fetched {len(data) // 1024}KB  {u}")
        else:
            missing.append(u)
            print(f"  [{i + 1:02d}] FAILED (throttled)  {u}", file=sys.stderr)
        time.sleep(sleep)
    return missing

def wayback_read(url, cache_dir):
    """Read one cached Wayback snapshot's HTML. Fails loud if it isn't cached."""
    cf = os.path.join(cache_dir, wayback_cache_name(url))
    if not (os.path.exists(cf) and os.path.getsize(cf) > 5000):
        sys.exit(f"MISSING Wayback cache for {url} (expected {cf}) — "
                 f"re-run with --wayback to resume the cache.")
    with open(cf, encoding="utf-8", errors="replace") as f:
        return f.read()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--url-file")
    ap.add_argument("--out", default="chapters")
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between requests (be polite)")
    ap.add_argument("--wayback", action="store_true",
                    help="fetch from the Internet Archive instead of the live site "
                         "(for genuinely-gone Volumes 1-2); resumable cache, parses with extract()")
    ap.add_argument("--wayback-cache", default="_wb_cache",
                    help="directory for the resumable raw-HTML snapshot cache (--wayback)")
    ap.add_argument("--wayback-timestamp", default="20180601",
                    help="YYYYMMDD snapshot to target; use a pre-paywall capture (<=2019), "
                         "since 2020+ TWI captures are post-redesign and prose-less")
    a = ap.parse_args()

    urls = list(a.urls)
    if a.url_file:
        with open(a.url_file) as f:
            urls += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not urls:
        sys.exit("Give me chapter URLs (args or --url-file).")

    # In --wayback mode, populate the resumable cache first and fail loud if any URL is
    # still un-cacheable (so a re-run resumes), then parse every chapter from the cache.
    if a.wayback:
        print(f"Wayback mode: snapshot @ {a.wayback_timestamp}, cache {a.wayback_cache}")
        missing = wayback_build_cache(urls, a.wayback_cache, a.wayback_timestamp, a.sleep)
        if missing:
            print(f"\n{len(missing)} chapter(s) still missing from the cache "
                  f"(archive.org throttled) — re-run to resume:", file=sys.stderr)
            for u in missing:
                print(f"  - {u}", file=sys.stderr)
            sys.exit(1)

    lines, markers = [], []
    seg = 0   # running count of non-blank sentence lines == the segment index align.py assigns
    for i, url in enumerate(urls):
        try:
            html = wayback_read(url, a.wayback_cache) if a.wayback else fetch(url)
            title, paras = extract(html, url)
        except Exception as e:
            print(f"  ! {url}: {e}", file=sys.stderr); continue
        chapter_first_seg = seg
        sents = []
        for para in paras:
            for s in split_sentences(para):
                sents.append(s); seg += 1
            sents.append("")            # blank line marks paragraph break
        while sents and sents[-1] == "":
            sents.pop()
        n_sents = sum(1 for s in sents if s)
        if n_sents == 0:
            print(f"  ! {title}: no sentences extracted — skipping marker", file=sys.stderr)
            continue
        lines += sents + [""]
        markers.append({"title": title, "url": url,
                        "seg": chapter_first_seg, "n_sentences": n_sents})
        print(f"  + {title}: {n_sents} sentences (segments {chapter_first_seg}–{seg - 1})")
        if not a.wayback:               # wayback parses from the local cache — no per-chapter sleep
            time.sleep(a.sleep)

    with open(a.out + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")
    with open(a.out + ".chapters.json", "w", encoding="utf-8") as f:
        json.dump(markers, f, ensure_ascii=False, indent=2)
    total = len([l for l in lines if l])
    print(f"\nWrote {a.out}.txt ({total} sentence lines) and {a.out}.chapters.json "
          f"({len(markers)} chapters).")

if __name__ == "__main__":
    main()
