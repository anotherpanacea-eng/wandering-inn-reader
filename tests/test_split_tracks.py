#!/usr/bin/env python3
"""Synthetic track packaging regression; no soundfile install or real audio needed."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PIPELINE = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE))
from schema import validate_doc


class SplitTracksTest(unittest.TestCase):
    def test_optional_words_and_local_timestamps(self):
        # Only audio metadata is substituted; execute the actual CLI, JSON writes,
        # schema validation and manifest assembly with two synthetic 2-second tracks.
        segments = [
            {"id": 0, "start": 0.25, "end": 1.5, "text": "Synthetic sentence only."},
            {"id": 1, "start": 2.25, "end": 3.5, "text": "Synthetic timed sentence.",
             "words": [{"w": "Synthetic", "s": 2.25, "e": 2.75}]},
        ]
        doc = {"title": "Synthetic", "audio": "", "segments": segments,
               "chapters": [{"title": "Second", "start": 2.25, "seg": 1}]}
        validate_doc(doc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "full.json"
            source.write_text(json.dumps(doc), encoding="utf-8")
            out = root / "tracks"
            argv = [str(PIPELINE / "split_tracks.py"), "--full", str(source),
                    "--audio", "01.wav", "02.wav", "--outdir", str(out),
                    "--title", "Synthetic"]
            metadata = SimpleNamespace(info=lambda _: SimpleNamespace(duration=2.0))
            with patch.dict(sys.modules, {"soundfile": metadata}), patch.object(sys, "argv", argv):
                with contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(argv[0], run_name="__main__")
            tracks = [json.loads((out / f"align{n:02d}.json").read_text()) for n in (1, 2)]
            for track in tracks:
                validate_doc(track)
            self.assertEqual(tracks[0]["segments"], [dict(segments[0], words=[])])
            self.assertEqual(tracks[1]["segments"], [
                {"id": 0, "start": 0.25, "end": 1.5, "text": "Synthetic timed sentence.",
                 "words": [{"w": "Synthetic", "s": 0.25, "e": 0.75}]}])
            self.assertEqual(tracks[1]["chapters"], [{"title": "Second", "start": 0.25, "seg": 0}])
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual([t["sentences"] for t in manifest["tracks"]], [1, 1])
            self.assertEqual([t["audio"] for t in manifest["tracks"]], ["01.wav", "02.wav"])


if __name__ == "__main__":
    unittest.main()
