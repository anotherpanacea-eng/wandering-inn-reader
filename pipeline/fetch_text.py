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

Outputs:
  <out>.txt          sentences, one per line, blank line between paragraphs
  <out>.chapters.json  [{title, url, first_line, n_lines}, ...] for chapter markers

Dependencies:  pip3 install requests beautifulsoup4
"""
import argparse, json, re, sys, time

UA = {"User-Agent": "Mozilla/5.0 (personal read-along fetcher)"}

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

def extract_paragraphs(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    body = (soup.select_one("div.entry-content")
            or soup.select_one("article .entry-content")
            or soup.select_one("article")
            or soup.find("main"))
    if body is None:
        raise RuntimeError("Could not find chapter body (div.entry-content). "
                           "Site markup may have changed — adjust the selector.")
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
    return paras

def fetch(url):
    import requests
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text

def title_from_url(url):
    slug = [s for s in url.rstrip("/").split("/") if s][-1]
    return slug

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--url-file")
    ap.add_argument("--out", default="chapters")
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between requests (be polite)")
    a = ap.parse_args()

    urls = list(a.urls)
    if a.url_file:
        with open(a.url_file) as f:
            urls += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not urls:
        sys.exit("Give me chapter URLs (args or --url-file).")

    lines, markers = [], []
    for i, url in enumerate(urls):
        try:
            paras = extract_paragraphs(fetch(url))
        except Exception as e:
            print(f"  ! {url}: {e}", file=sys.stderr); continue
        first = len(lines)
        sents = []
        for para in paras:
            sents += split_sentences(para)
            sents.append("")            # blank line marks paragraph break
        while sents and sents[-1] == "":
            sents.pop()
        lines += sents + [""]
        markers.append({"title": title_from_url(url), "url": url,
                        "first_line": first, "n_lines": len(sents)})
        print(f"  + {title_from_url(url)}: {len([s for s in sents if s])} sentences")
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
