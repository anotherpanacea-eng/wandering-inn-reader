#!/usr/bin/env python3
"""
test_align.py — the data-contract functional check AGENTS.md §"Verify before
claiming green" calls for: run align.py's pure transforms against a synthetic
aeneas sync map + chapter markers and assert the player schema comes out right.

No aeneas/torch needed — to_segments/attach_words/attach_chapters are pure Python;
only GENERATING a sync map needs a real aligner. Plain stdlib asserts (the repo has
no pytest); run directly: `python3 tests/test_align.py`. Exit 0 = pass.
"""
import contextlib, io, os, sys

try:                                  # cp1252 Windows console can't encode the check glyph
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pipeline"))

import align                       # noqa: E402
from schema import validate_doc, SchemaError   # noqa: E402


def test_to_segments_skips_unusable():
    sync = {"fragments": [
        {"id": "f1", "lines": ["First sentence."], "begin": "0.0", "end": "2.0"},
        {"id": "f2", "lines": ["   "],             "begin": "2.0", "end": "3.0"},   # blank → skipped
        {"id": "f3", "lines": ["Second one."],     "begin": "2.0", "end": "4.0"},
        {"id": "f4", "lines": ["No times."]},                                       # no begin/end → skipped
        {"id": "f5", "lines": ["Third here."],     "begin": "4.0", "end": "6.5"},
    ]}
    segs = align.to_segments(sync)
    assert [s["id"] for s in segs] == [0, 1, 2], segs          # ids re-numbered contiguously
    assert [s["text"] for s in segs] == ["First sentence.", "Second one.", "Third here."]
    assert segs[0]["start"] == 0.0 and segs[2]["end"] == 6.5
    return segs


def test_attach_words_packs_by_time(segs):
    words = [
        {"w": "First",     "s": 0.0, "e": 0.5},
        {"w": "sentence.", "s": 0.5, "e": 2.0},
        {"w": "Second",    "s": 2.0, "e": 3.0},
        {"w": "one.",      "s": 3.0, "e": 4.0},
        {"w": "Third",     "s": 4.0, "e": 6.5},
    ]
    align.attach_words(segs, words)
    assert [w["w"] for w in segs[0]["words"]] == ["First", "sentence."], segs[0]["words"]
    assert [w["w"] for w in segs[1]["words"]] == ["Second", "one."], segs[1]["words"]
    assert [w["w"] for w in segs[2]["words"]] == ["Third"], segs[2]["words"]


def test_attach_chapters_maps_and_drops(segs):
    markers = [
        {"title": "Chapter One", "seg": 0},
        {"title": "Chapter Two", "seg": 2},
        {"title": "Out Of Range", "seg": 99},      # past the end → dropped with a loud warning
    ]
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        chapters = align.attach_chapters(segs, markers)
    warn = buf.getvalue()
    assert [c["title"] for c in chapters] == ["Chapter One", "Chapter Two"], chapters
    assert chapters[0]["seg"] == 0 and chapters[0]["start"] == segs[0]["start"]
    assert chapters[1]["seg"] == 2 and chapters[1]["start"] == segs[2]["start"]
    assert "out of range" in warn.lower(), f"expected an out-of-range warning, got: {warn!r}"


def test_validate_doc_contract(segs):
    good = {"title": "Demo", "audio": "demo.mp3", "segments": segs,
            "chapters": [{"title": "Chapter One", "start": segs[0]["start"], "seg": 0}]}
    validate_doc(good, source="test")              # must not raise

    bad = {"title": "Demo", "audio": "demo.mp3",
           "segments": [{"id": 0, "end": 2.0, "text": "missing start"}]}
    try:
        validate_doc(bad, source="test")
    except SchemaError:
        pass
    else:
        raise AssertionError("validate_doc accepted a segment with no start")


def main():
    segs = test_to_segments_skips_unusable()
    test_attach_words_packs_by_time(segs)
    test_attach_chapters_maps_and_drops(segs)
    test_validate_doc_contract(segs)
    print("✓ test_align: to_segments / attach_words / attach_chapters / schema contract all pass")


if __name__ == "__main__":
    main()
