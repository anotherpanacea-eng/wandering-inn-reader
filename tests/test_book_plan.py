"""Synthetic planner behavior and no-execution boundary; no audio or models."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
planner = importlib.import_module('process_book')


def manifest():
    return {'book': 1, 'title': 'Invented example', 'tier': 'm4b',
            'audio': {'m4b': 'missing-audio/book.m4b'},
            'text': {'source': 'live', 'toc_from': '1.00', 'toc_to': '1.02'}}


class BookPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.path = self.base / 'book.json'

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8')

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes() for p in self.base.rglob('*') if p.is_file()}

    def cli(self, *args, cwd=None, env=None):
        before = self.snapshot()
        result = subprocess.run([sys.executable, str(ROOT / 'pipeline/process_book.py'),
                                 str(self.path), *args], cwd=cwd, env=env, capture_output=True, text=True)
        self.assertEqual(before, self.snapshot())
        return result

    def test_unmapped_plan_retains_all_stages_and_gates(self):
        self.write(manifest())
        result = self.cli('--plan')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['mode'], 'plan-only')
        stages = {s['name']: s for s in plan['stages']}
        self.assertEqual(stages['gate-a']['state'], 'pending')
        self.assertEqual(stages['gate-a']['unresolved_parameters'], ['map.m4b_starts', 'map.m4b_end'])
        self.assertIsNone(stages['units']['parameters']['--starts'])
        self.assertEqual(stages['units']['depends_on'], ['gate-a'])
        self.assertEqual(stages['gate-b']['state'], 'pending-runtime-assessment')
        self.assertFalse(stages['gate-b']['verified'])
        self.assertEqual(stages['delivery']['state'], 'not-requested')
        self.assertEqual(stages['alignment-audio']['parameters']['--ext'], 'wav')
        self.assertEqual(stages['playback-audio']['parameters']['--ext'], 'm4a')
        self.assertTrue(stages['verify']['parameters']['--wps-pre'])
        self.assertEqual(len(plan['stages']), 12)

    def test_confirmed_map_and_destination_never_authorize_execution(self):
        value = manifest()
        value['map'] = {'m4b_starts': [0, 2], 'm4b_end': 4, 'confirmed': True}
        value['ship'] = {'dropbox_dir': 'missing-delivery'}
        self.write(value)
        result = self.cli('--plan')
        self.assertEqual(result.returncode, 0, result.stderr)
        stages = {s['name']: s for s in json.loads(result.stdout)['stages']}
        self.assertEqual(stages['gate-a']['state'], 'user-asserted')
        self.assertFalse(stages['gate-a']['verified'])
        self.assertEqual(stages['gate-b']['state'], 'pending-runtime-assessment')
        self.assertEqual(stages['delivery']['state'], 'separately-authorized-action-required')
        self.assertTrue(all(not s['executed'] for s in stages.values() if 'script' in s))

    def test_relative_paths_do_not_depend_on_current_directory(self):
        value = manifest()
        value['ship'] = {'dropbox_dir': '../delivery'}
        self.write(value)
        other = self.base / 'other'
        other.mkdir()
        a, b = self.cli('--plan', cwd=ROOT), self.cli('--plan', cwd=other)
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(a.stdout, b.stdout)
        plan = json.loads(a.stdout)
        self.assertEqual(plan['inputs']['audio']['m4b'], os.path.join(str(self.base), 'missing-audio', 'book.m4b'))
        self.assertEqual(plan['inputs']['ship']['dropbox_dir'], os.path.abspath(self.base / '../delivery'))

    def test_native_absolute_paths_preserved(self):
        value = manifest()
        absolute = os.path.join(str(self.base), 'nonexistent.m4b')
        value['audio']['m4b'] = absolute
        self.write(value)
        self.assertEqual(json.loads(self.cli('--plan').stdout)['inputs']['audio']['m4b'], absolute)

    def test_unicode_plan_survives_redirected_non_utf8_stdout(self):
        value = manifest()
        value['title'] = 'Invented Ω title'
        value['audio']['m4b'] = 'missing-Ω.m4b'
        value['text']['toc_from'] = 'Ω beginning'
        self.write(value)
        env = os.environ.copy()
        env.pop('PYTHONUTF8', None)
        env['PYTHONIOENCODING'] = 'cp1252:strict'
        result = self.cli('--plan', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['inputs']['title'], value['title'])
        self.assertEqual(plan['inputs']['text']['toc_from'], value['text']['toc_from'])
        self.assertIn('missing-Ω.m4b', plan['inputs']['audio']['m4b'])

    @unittest.skipUnless(os.name == 'nt', 'native UNC semantics only on Windows')
    def test_native_unc_path_is_lexical(self):
        value = manifest()
        value['audio']['m4b'] = r'\\invented-host\share\missing.m4b'
        self.write(value)
        result = self.cli('--plan')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['inputs']['audio']['m4b'], value['audio']['m4b'])

    def test_manifest_errors_are_named_and_emit_no_plan(self):
        changes = [
            ('book', True, 'book'), ('book', 0, 'book'), ('title', ' ', 'title'),
            ('tier', 'auto', 'tier'), ('tier', 'straddle', 'tier'),
            ('audio', {'glob': '*.mp3'}, 'audio.glob'),
            ('audio', {'m4b': 'https://example.invalid/a'}, 'audio.m4b'),
            ('audio', {'m4b': 'bad\x00name'}, 'audio.m4b'),
            ('text', {'source': 'epub'}, 'text.source'),
            ('text', {'source': 'live', 'toc_from': '', 'toc_to': 'b'}, 'text.toc_from'),
            ('map', {'confirmed': 1}, 'map.confirmed'),
            ('map', {'confirmed': True}, 'map.confirmed'),
            ('map', {'m4b_starts': [0]}, 'map'),
            ('map', {'m4b_starts': [], 'm4b_end': 2}, 'map.m4b_starts'),
            ('map', {'m4b_starts': [False], 'm4b_end': 2}, 'map.m4b_starts[0]'),
            ('map', {'m4b_starts': [2, 1], 'm4b_end': 3}, 'map.m4b_starts'),
            ('map', {'m4b_starts': [1, 1], 'm4b_end': 3}, 'map.m4b_starts'),
            ('map', {'m4b_starts': [-1], 'm4b_end': 3}, 'map.m4b_starts[0]'),
            ('map', {'m4b_starts': [1], 'm4b_end': 1}, 'map.m4b_end'),
            ('map', {'m4b_starts': [1], 'm4b_end': True}, 'map.m4b_end'),
            ('map', {'extra': 0}, 'map.extra'), ('ship', {}, 'ship.dropbox_dir'),
            ('ship', {'dropbox_dir': 'file:///bad'}, 'ship.dropbox_dir'),
            ('boundary_review', {'approved': True}, 'manifest.boundary_review'),
        ]
        for key, value, diagnostic in changes:
            with self.subTest(key=key, value=value):
                data = manifest()
                data[key] = value
                self.write(data)
                result = self.cli('--plan')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertIn(diagnostic, result.stderr)

    def test_json_errors_missing_manifest_and_required_plan(self):
        for raw in ('{', '[]', '{"book":1,"book":2}', '{"book":NaN}', '{"book":Infinity}'):
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding='utf-8')
                result = self.cli('--plan')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
        self.path.unlink()
        self.assertEqual(self.cli('--plan').returncode, 2)
        self.write(manifest())
        self.assertEqual(self.cli().returncode, 2)

    def test_dynamic_no_execution_or_writes_on_success_and_error(self):
        import builtins
        import socket
        original_open, original_import = builtins.open, builtins.__import__

        def read_only_open(path, mode='r', *args, **kwargs):
            if os.fspath(path) != str(self.path) or any(c in mode for c in 'wax+'):
                raise AssertionError(f'unexpected file access: {path} {mode}')
            return original_open(path, mode, *args, **kwargs)

        def no_models(name, *args, **kwargs):
            if name.split('.')[0] in {'torch', 'torchaudio', 'transformers'}:
                raise AssertionError('model import')
            return original_import(name, *args, **kwargs)

        for value, expected in ((manifest(), 0), ({'tier': 'auto'}, 2)):
            self.write(value)
            before = self.snapshot()
            with patch('builtins.open', side_effect=read_only_open), patch('builtins.__import__', side_effect=no_models), \
                 patch('subprocess.Popen', side_effect=AssertionError('subprocess')), \
                 patch.object(socket, 'socket', side_effect=AssertionError('network')), \
                 patch('os.mkdir', side_effect=AssertionError('mkdir')), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                if expected == 0:
                    self.assertEqual(planner.main([str(self.path), '--plan']), 0)
                else:
                    with self.assertRaises(SystemExit) as error:
                        planner.main([str(self.path), '--plan'])
                    self.assertEqual(error.exception.code, 2)
            self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main()
