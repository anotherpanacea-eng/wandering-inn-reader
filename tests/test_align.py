#!/usr/bin/env python3
"""
test_align.py — the data-contract functional check AGENTS.md §"Verify before
claiming green" calls for: run align.py's pure transforms against a synthetic
aeneas sync map + chapter markers and assert the player schema comes out right.

No aeneas/torch needed — to_segments/attach_words/attach_chapters are pure Python;
only GENERATING a sync map needs a real aligner. Plain stdlib asserts (the repo has
no pytest); run directly: `python3 tests/test_align.py`. Exit 0 = pass.
"""
import contextlib, io, json, os, sys, subprocess, tempfile, types
from pathlib import Path
from unittest.mock import patch

try:                                  # cp1252 Windows console can't encode the check glyph
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "pipeline"))

import align                       # noqa: E402
import recombine_chapters           # noqa: E402
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


def test_fragment_list_cli_matches_envelope():
    """Accepted bare lists and object sync maps produce identical player output."""
    fragments = [
        {"lines": ["Synthetic first sentence."], "begin": "0.0004", "end": "2.0"},
        {"lines": [" "], "begin": "2", "end": "3"},
        {"text": "Synthetic second sentence.", "begin": "2", "end": "4.25"},
        {"text": "No timestamps."},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        outputs = []
        for name, payload in (("list", fragments), ("envelope", {"fragments": fragments})):
            source = os.path.join(tmp, name + ".json")
            dest = os.path.join(tmp, name + "-output.json")
            with open(source, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            result = subprocess.run(
                [sys.executable, os.path.join(HERE, "..", "pipeline", "align.py"),
                 "--sync", source, "--title", "Synthetic", "--audio", "sample.mp3", "--out", dest],
                capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            with open(dest, encoding="utf-8") as f:
                outputs.append(json.load(f))
        assert outputs[0] == outputs[1], outputs
        validate_doc(outputs[0])
        assert [s["text"] for s in outputs[0]["segments"]] == [
            "Synthetic first sentence.", "Synthetic second sentence."]
        assert outputs[0]["segments"][0]["start"] == 0.0
        assert outputs[0]["segments"][1]["end"] == 4.25


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


def test_recombine_track_selection():
    """Ambiguous filenames must never select audio or alter an output."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        media = root / "media"
        media.mkdir()
        chapters = root / "chapters"
        chapters.mkdir()
        track_map = root / "map.json"
        track_map.write_text(json.dumps([
            {"title": "First", "seg": 0, "tracks": [1]},
            {"title": "Second", "seg": 1, "tracks": [2]},
        ]), encoding="utf-8")
        for i in range(2):
            (chapters / f"chap{i:02d}_invented.json").write_text(json.dumps({
                "title": "Invented", "audio": "unused.wav", "segments": [{
                    "id": 0, "start": 2.0, "end": 4.0,
                    "text": "Invented sentence.",
                    "words": [{"w": "Invented", "s": 2.25, "e": 2.75}],
                }],
            }), encoding="utf-8")
        output = root / "output.json"
        argv = ["recombine", "--track-map", str(track_map),
                "--audio-glob", str(media / "*.wav"),
                "--chapters-dir", str(chapters), "--out", str(output),
                "--title", "Invented book"]
        # Equivalent numeric spellings must still be ambiguous. Test both used
        # and unused duplicate numbers: the glob itself must be unambiguous.
        (media / "02 - second.wav").touch()
        for number in (1, 9):
            if number == 9:
                (media / "01 - first.wav").touch()
            first = media / f"{number:02d} - first.wav"
            second = media / f"{number} - second.wav"
            first.touch()
            second.touch()
            for existing in (False, True):
                if existing:
                    output.write_bytes(b"retain this existing artifact")
                fake_media = types.SimpleNamespace(info=lambda path: (_ for _ in ()).throw(
                    AssertionError("ambiguous input reached media metadata")))
                with patch.object(sys, "argv", argv), patch.dict(
                        sys.modules, {"soundfile": fake_media}), contextlib.redirect_stdout(io.StringIO()):
                    try:
                        recombine_chapters.main()
                    except SystemExit as exc:
                        message = str(exc)
                        assert first.name in message and second.name in message, message
                    else:
                        raise AssertionError("recombination accepted duplicate numeric tracks")
                if existing:
                    assert output.read_bytes() == b"retain this existing artifact"
                    output.unlink()
                else:
                    assert not output.exists(), "refusal created an output"
            first.unlink()
            second.unlink()
        # Unique roster: creation/glob order differs from map order. Ignore an
        # unnumbered file and obtain timeline offsets from the selected tracks.
        (media / "02 - second.wav").touch()
        (media / "01 - first.wav").touch()
        (media / "unnumbered.wav").touch()
        durations = {"01 - first.wav": 10.0, "02 - second.wav": 20.0}
        reads = []
        def info(path):
            name = Path(path).name
            reads.append(name)
            return types.SimpleNamespace(duration=durations[name])
        with patch.object(sys, "argv", argv), patch.dict(
                sys.modules, {"soundfile": types.SimpleNamespace(info=info)}), \
                contextlib.redirect_stdout(io.StringIO()):
            recombine_chapters.main()
        doc = json.loads(output.read_text(encoding="utf-8"))
        validate_doc(doc)
        assert doc["title"] == "Invented book"
        assert doc["audio"] == "01 - first.wav"
        assert [s["id"] for s in doc["segments"]] == [0, 1]
        assert [s["start"] for s in doc["segments"]] == [2.0, 12.0]
        assert [s["end"] for s in doc["segments"]] == [4.0, 14.0]
        assert [s["words"][0]["s"] for s in doc["segments"]] == [2.25, 12.25]
        assert [s["words"][0]["e"] for s in doc["segments"]] == [2.75, 12.75]
        assert doc["chapters"] == [{"title": "First", "start": 2.0, "seg": 0},
                                   {"title": "Second", "start": 12.0, "seg": 1}]
        assert "unnumbered.wav" not in reads


def main():
    test_fragment_list_cli_matches_envelope()
    segs = test_to_segments_skips_unusable()
    test_attach_words_packs_by_time(segs)
    test_attach_chapters_maps_and_drops(segs)
    test_validate_doc_contract(segs)
    test_recombine_track_selection()
    print("✓ test_align: to_segments / attach_words / attach_chapters / schema / recombination contract all pass")


if __name__ == "__main__":
    main()
