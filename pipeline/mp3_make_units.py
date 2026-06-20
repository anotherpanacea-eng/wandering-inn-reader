#!/usr/bin/env python3
"""
mp3_make_units.py --boundaries boundaries.json --chapters book.chapters.json
                  --out-units book_units.json --out-trackmap book_track_map.json
                  --out-cutbounds book_cut_bounds.json [--merge "i-j[,k-l]" ...]

Turn find_chapter_boundaries.py output (one START time + seg per web chapter) into the three JSONs the
mp3-multitrack assembly flow needs. This is the mp3 analogue of m4b_make_units.py, but the input is a
boundaries file (continuous-timeline start times) rather than embedded m4b mark indices:
    units      : [{track,title,seg,start,end}]  (seconds)        -> mp3_cut.py, mp3_package.py
    trackmap   : [{title,seg,tracks:[track]}]                    -> align_chapters.py --track-map
    cutbounds  : {total_dur, chapters:[{start,title}]}           -> mp3_cut.py (a boundaries echo)

By default each web chapter becomes one numbered unit. --merge folds a CONSECUTIVE range of web-chapter
indices into ONE alignment unit -- for an interlude split across parts with no real audio boundary (e.g.
a 2-part interlude of continuous narration). The merged unit spans [start of first, end of last], keeps
the FIRST chapter's seg/title (the constituent markers are placed later by mp3_package.py from the
aligned segment times, so a merged unit can still carry >1 chapter marker). Pass --merge "0-1" to merge
web chapters 0 and 1; repeat or comma-separate for several groups, e.g. --merge "0-1,5-7". Ranges must be
ascending, in range, and non-overlapping; fail-loud otherwise.
"""
import argparse, json, sys


def parse_merges(specs, n):
    """Parse --merge specs (each "i-j" or comma-list "i-j,k-l") into a list of (lo, hi) inclusive ranges.
    Validates each range is i<=j, within 0..n-1, and that no two ranges overlap. Fail-loud on any error."""
    ranges = []
    for spec in specs:
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" not in part:
                sys.exit(f"--merge range {part!r} must be 'i-j' (a hyphenated index range)")
            lo_s, hi_s = part.split("-", 1)
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                sys.exit(f"--merge range {part!r} has non-integer indices")
            if lo > hi:
                sys.exit(f"--merge range {part!r} is descending (need i<=j)")
            if not (0 <= lo and hi < n):
                sys.exit(f"--merge range {part!r} out of range 0..{n-1}")
            ranges.append((lo, hi))
    ranges.sort()
    for (lo1, hi1), (lo2, hi2) in zip(ranges, ranges[1:]):
        if lo2 <= hi1:
            sys.exit(f"--merge ranges {lo1}-{hi1} and {lo2}-{hi2} overlap")
    return ranges


def build_groups(n, ranges):
    """Group web-chapter indices [0..n-1] into units, merging each --merge range into one group.
    Returns a list of index-lists, in order (a merged range is one multi-index group)."""
    merged = {}                                     # first index of a range -> the full index list
    skip = set()
    for lo, hi in ranges:
        merged[lo] = list(range(lo, hi + 1))
        skip.update(range(lo + 1, hi + 1))
    groups = []
    for i in range(n):
        if i in skip:
            continue
        groups.append(merged.get(i, [i]))
    return groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--boundaries", required=True, help="find_chapter_boundaries.py output JSON")
    ap.add_argument("--chapters", required=True, help="book.chapters.json (kept for count/order check)")
    ap.add_argument("--out-units", required=True)
    ap.add_argument("--out-trackmap", required=True)
    ap.add_argument("--out-cutbounds", required=True)
    ap.add_argument("--merge", action="append", default=[],
                    help='consecutive web-chapter index range(s) to merge into one unit, e.g. "0-1" or "0-1,5-7"')
    a = ap.parse_args()

    bnd = json.load(open(a.boundaries, encoding="utf-8"))
    total = bnd["total_dur"]
    ch = bnd["chapters"]                                   # in order, each {seg,title,start}
    web = json.load(open(a.chapters, encoding="utf-8"))
    if len(web) != len(ch):
        sys.exit(f"{a.chapters} has {len(web)} web chapters but {a.boundaries} has {len(ch)} boundary entries")

    # find_chapter_boundaries.py marks an unusable run reliable=false; refuse to build units from one.
    if bnd.get("reliable") is False:
        sys.exit(f"refusing to emit units -- {a.boundaries} is reliable=false: "
                 f"{bnd.get('unreliable_reasons') or 'see boundary-finder log'}")

    # Validate the boundary starts BEFORE emitting any of the three files: they must be numeric,
    # non-negative, strictly ascending, and end before total_dur, with non-decreasing seg order
    # (time forward => text forward). Without this a non-monotonic boundaries file (e.g. starts
    # [10, 5] with total_dur 20) silently writes negative-duration units at exit 0 (Codex P1).
    if not isinstance(total, (int, float)) or total <= 0:
        sys.exit(f"{a.boundaries} total_dur is not a positive number: {total!r}")
    errs, prev_s, prev_seg = [], None, None
    for i, c in enumerate(ch):
        s, seg, title = c.get("start"), c.get("seg"), c.get("title")
        if not isinstance(s, (int, float)):
            errs.append(f"chapter {i} ({title!r}) start is not numeric: {s!r}"); continue
        if s < 0:
            errs.append(f"chapter {i} ({title!r}) start {s} is negative")
        if s >= total:
            errs.append(f"chapter {i} ({title!r}) start {s} >= total_dur {total}")
        if prev_s is not None and s <= prev_s:
            errs.append(f"chapter {i} ({title!r}) start {s} is not after chapter {i-1} start {prev_s} "
                        f"(starts must be strictly ascending)")
        if prev_seg is not None and isinstance(seg, int) and seg < prev_seg:
            errs.append(f"chapter {i} ({title!r}) seg {seg} < chapter {i-1} seg {prev_seg} "
                        f"(seg order must be non-decreasing)")
        prev_s, prev_seg = s, (seg if isinstance(seg, int) else prev_seg)
    if errs:
        sys.exit("refusing to emit units -- invalid boundary starts in %s:\n  %s"
                 % (a.boundaries, "\n  ".join(errs)))

    starts = [c["start"] for c in ch] + [total]

    ranges = parse_merges(a.merge, len(ch))
    groups = build_groups(len(ch), ranges)

    units, tmap, cutb = [], [], []
    for ui, idxs in enumerate(groups):
        track = ui + 1
        first, last = idxs[0], idxs[-1]
        start = ch[first]["start"]
        end = starts[last + 1]
        title = ch[first]["title"]                         # merged unit keeps the first chapter's title
        seg = ch[first]["seg"]
        units.append({"track": track, "title": title, "seg": seg,
                      "start": round(start, 3), "end": round(end, 3)})
        tmap.append({"title": title, "seg": seg, "tracks": [track]})
        cutb.append({"start": round(start, 3), "title": title})

    json.dump(units, open(a.out_units, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(tmap, open(a.out_trackmap, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({"total_dur": total, "chapters": cutb},
              open(a.out_cutbounds, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    for u, idxs in zip(units, groups):
        span = f" (web {idxs[0]}-{idxs[-1]})" if len(idxs) > 1 else ""
        print(f"  [{u['track']:02d}] {u['start']/60:7.1f}-{u['end']/60:7.1f}min ({(u['end']-u['start'])/60:5.1f}min)"
              f"  seg {u['seg']:>6}  {u['title']}{span}", flush=True)
    print(f"\nwrote {a.out_units} + {a.out_trackmap} + {a.out_cutbounds} ({len(units)} units)", flush=True)


if __name__ == "__main__":
    main()
