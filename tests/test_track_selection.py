"""Numbered discovery refuses collisions before expensive or destructive work.

All fixtures are invented. Media metadata and ASR are fakes; no audio/model loads.
"""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import align_chapters
import align_qa
import verify_tracks
import wps_check


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.map = self.root / "map.json"
        self.map.write_text(json.dumps([{"title": "Invented", "seg": 0, "tracks": [1]}]))
        self.text = self.root / "text.txt"
        self.text.write_text("A small invented sentence.\n")
        self.out = self.root / "out"
        self.audio = str(self.root / "*.wav")
        self.info_calls = []
        self.sf = SimpleNamespace(info=self.info)
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {"soundfile": self.sf}).start()

    def info(self, path):
        self.info_calls.append(path)
        return SimpleNamespace(duration=2.0, samplerate=16000)

    def doc(self, name):
        p = self.root / name
        p.write_text(json.dumps({"title": "Invented", "audio": "01.wav",
            "segments": [{"id": 0, "start": 0.0, "end": 2.0,
                          "text": "A small invented sentence."}]}))
        return str(p)

    def argv(self, module, args):
        with patch.object(sys, "argv", [module.__name__, *args]):
            module.main()

    def qa(self, args):
        self.argv(align_qa, args)

    def common(self):
        return ["--audio-glob", self.audio, "--track-map", str(self.map)]

    def collision(self, call, paths):
        # Glob order must never select a winner. Existing artifacts stay intact.
        for ordered in (paths, list(reversed(paths))):
            with self.subTest(order=ordered):
                sentinel = self.root / "saved.txt"
                sentinel.write_text("unchanged")
                def discover(pattern):
                    if pattern == self.audio:
                        return [str(self.root / "01.wav")]
                    if "align*.json" in pattern:
                        return ordered if "align" in Path(ordered[0]).name else []
                    return ordered
                with patch.dict(sys.modules, {"torch": None, "torchaudio": None}), \
                     patch("glob.glob", side_effect=discover), \
                     patch.object(align_chapters, "preflight", side_effect=AssertionError("preflight")), \
                     patch.object(align_qa, "preflight", side_effect=AssertionError("preflight")), \
                     patch.object(align_qa, "make_asr", side_effect=AssertionError("model")), \
                     patch.object(align_chapters.subprocess, "run", side_effect=AssertionError("child")):
                    with self.assertRaises(SystemExit) as caught:
                        call()
                message = str(caught.exception)
                self.assertIn("ambiguous", message)
                for path in paths:
                    self.assertIn(path, message)
                self.assertEqual(self.info_calls, [])
                self.assertFalse(self.out.exists())
                self.assertEqual(sentinel.read_text(), "unchanged")

    def test_audio_collisions_all_consumers_including_unused_track(self):
        paths = [str(self.root / n) for n in ("09.wav", "9-other.wav")]
        calls = [
            lambda: self.argv(align_chapters, self.common() + ["--text", str(self.text),
                "--outdir", str(self.out)]),
            lambda: wps_check.units_from_track_map(str(self.map), str(self.text), self.audio),
            lambda: wps_check.units_from_dir(str(self.root), self.audio),
            lambda: self.qa(["profile", "--dir", str(self.root), "--audio-glob", self.audio,
                "--json", str(self.root / "saved.txt"), "--report", str(self.root / "saved.txt")]),
            lambda: self.qa(["wps-check", *self.common(), "--text", str(self.text)]),
            lambda: verify_tracks.main(["--dir", str(self.root), "--audio-glob", self.audio,
                                        "--wps-pre"]),
            lambda: verify_tracks.main(["--dir", str(self.root), "--audio-glob", self.audio]),
        ]
        # units_from_dir discovers its JSON roster before indexing audio.
        alignment = self.doc("align01.json")
        for call in calls:
            with self.subTest(consumer=call):
                def discover(pattern, ordered=None):
                    return [alignment] if pattern.endswith("align*.json") else ordered
                for ordered in (paths, list(reversed(paths))):
                    saved = self.root / "saved.txt"
                    saved.write_text("unchanged")
                    with patch.dict(sys.modules, {"torch": None, "torchaudio": None}), \
                         patch("glob.glob", side_effect=lambda p: discover(p, ordered)), \
                         patch.object(align_chapters, "preflight", side_effect=AssertionError("preflight")), \
                         patch.object(align_qa, "preflight", side_effect=AssertionError("preflight")), \
                         patch.object(align_qa, "make_asr", side_effect=AssertionError("model")), \
                         patch.object(align_chapters.subprocess, "run", side_effect=AssertionError("child")):
                        with self.assertRaises(SystemExit) as caught:
                            call()
                    self.assertIn("ambiguous", str(caught.exception))
                    for path in paths:
                        self.assertIn(path, str(caught.exception))
                    self.assertEqual(self.info_calls, [])
                    self.assertFalse(self.out.exists())
                    self.assertEqual(saved.read_text(), "unchanged")

    def test_alignment_collisions_before_duration_or_model(self):
        for names in (("align09.json", "align9.json"), ("chap00_a.json", "chap0_b.json")):
            paths = [str(self.root / n) for n in names]
            self.collision(lambda: wps_check.units_from_dir(str(self.root), self.audio), paths)
            self.collision(lambda: self.qa(["profile", "--dir", str(self.root),
                                            "--audio-glob", self.audio, "--json", str(self.root / "saved.txt"),
                                            "--report", str(self.root / "saved.txt")]), paths)
        paths = [str(self.root / n) for n in ("align09.json", "align9.json")]
        self.collision(lambda: verify_tracks.main(["--dir", str(self.root),
                        "--audio-glob", self.audio, "--wps-pre"]), paths)
        self.collision(lambda: verify_tracks.main(["--dir", str(self.root),
                        "--audio-glob", self.audio]), paths)
        self.collision(lambda: verify_tracks.main(["--dir", str(self.root),
                        *self.common(), "--text", str(self.text), "--wps-pre"]), paths)
        paths = [str(self.root / n) for n in ("chap00_a.json", "chap0_b.json")]
        self.collision(lambda: self.qa(["wps-check", *self.common(),
                                        "--dir", str(self.root)]), paths)

    def test_unique_roster_and_active_fallback(self):
        (self.root / "01.wav").touch()
        self.doc("align01.json")
        # Inactive chapter fallback collisions must not poison the align roster.
        self.doc("chap00_a.json")
        self.doc("chap0_b.json")
        with contextlib.redirect_stdout(io.StringIO()):
            units = wps_check.units_from_dir(str(self.root), self.audio)
            self.assertEqual(len(units), 1)
            self.assertEqual(units[0]["wps"], 2.0)
            verify_tracks.main(["--wps-only", "--dir", str(self.root),
                                "--audio-glob", self.audio])
            self.argv(align_chapters, self.common() + ["--text", str(self.text),
                       "--outdir", str(self.out), "--dry-run"])
        self.assertTrue(self.out.is_dir())

    def test_wps_only_canonical_mode_ignores_inactive_json_collisions(self):
        (self.root / "01.wav").touch()
        self.doc("align01.json")
        self.doc("align1.json")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            verify_tracks.main(["--wps-only", *self.common(), "--text", str(self.text),
                                "--dir", str(self.root)])
        self.assertIn("WPS GATE PASS", output.getvalue())

    def test_alignment_collision_preserves_existing_chapter_output(self):
        self.out.mkdir()
        saved = self.out / "chap00_invented.json"
        saved.write_text("existing output")
        paths = [str(self.root / "01.wav"), str(self.root / "1-other.wav")]
        with patch("glob.glob", return_value=paths), \
             patch.object(align_chapters, "preflight", side_effect=AssertionError("preflight")), \
             patch.object(align_chapters.subprocess, "run", side_effect=AssertionError("child")):
            with self.assertRaises(SystemExit) as caught:
                self.argv(align_chapters, self.common() + ["--text", str(self.text),
                           "--outdir", str(self.out), "--force"])
        self.assertIn("ambiguous", str(caught.exception))
        self.assertEqual(saved.read_text(), "existing output")
        self.assertEqual(list(self.out.iterdir()), [saved])

    def test_verifier_unique_asr_roster_samples_real_entrypoint(self):
        audio = str(self.root / "01.wav")
        Path(audio).touch()
        self.doc("align01.json")
        class Tensor:
            T = property(lambda self: self)
            def mean(self, **kw): return self
            def to(self, dev): return self
            def argmax(self, axis): return [self]
            def tolist(self): return [1, 2, 3, 4, 5, 6]
        class Model:
            def to(self, dev): return self
            def train(self, enabled): return self
            def __call__(self, wav): return Tensor(), None
        bundle = SimpleNamespace(get_model=Model,
            get_labels=lambda: ["", "a", "|small", "|invented", "|sentence", ".", ""],
            sample_rate=16000)
        modules = {
            "numpy": SimpleNamespace(linspace=lambda a, b, n: [a]),
            "torch": SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False),
                from_numpy=lambda data: data, inference_mode=contextlib.nullcontext),
            "torchaudio": SimpleNamespace(pipelines=SimpleNamespace(WAV2VEC2_ASR_BASE_960H=bundle)),
            "soundfile": SimpleNamespace(info=self.info, read=lambda *a, **kw: (Tensor(), 16000)),
        }
        with patch.dict(sys.modules, modules), contextlib.redirect_stdout(io.StringIO()) as output:
            verify_tracks.main(["--dir", str(self.root), "--audio-glob", self.audio,
                                "--tracks", "01", "--points", "1", "--win", "1"])
        self.assertIn("1/1 points PASS", output.getvalue())
        self.assertIn("GATE PASS", output.getvalue())

    def test_profile_chapter_zero_uses_audio_one(self):
        audio = str(self.root / "01.wav")
        Path(audio).touch()
        self.doc("chap00_a.json")
        heard = []
        def asr(path, t):
            heard.append(path)
            return "A small invented sentence."
        with patch.dict(sys.modules, {"numpy": SimpleNamespace(linspace=lambda a, b, n: [a])}), \
             patch.object(align_qa, "make_asr", return_value=(asr, "fake")), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.qa(["profile", "--dir", str(self.root), "--audio-glob", self.audio,
                     "--no-preflight"])
        self.assertEqual(heard, [audio])
        self.assertIn("PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
