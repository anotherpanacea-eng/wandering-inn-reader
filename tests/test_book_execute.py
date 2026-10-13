"""Synthetic driver/real helper integration; no audio decoding, network or models."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
import warnings
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
driver = importlib.import_module('process_book')
from book_manifest import load_manifest


def write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


def verification(count=2, flags=0):
    rows = ['=== wps pre-screen clear; proceeding to ASR pass. ===']
    for i in range(1, count+1):
        rows.append(f'===== track {i:02d}  (1.0min audio, text to 1.0min)  chapters: Invented =====')
        for j in range(5):
            flagged = (i-1)*5+j < flags
            rows.append(f'  t= {j/10:.1f}min  overlap={0.1 if flagged else 0.9:.2f}  {"FLAG" if flagged else "PASS"}')
    rows.extend([f'=== {count*5-flags}/{count*5} points PASS, {flags} FLAG ({flags/(count*5):.0%} fail; threshold 40%) ===', 'GATE PASS'])
    return '\n'.join(rows)+'\n'


class SyntheticRunner:
    """Run real model-free helpers; replace only acquisition, OS and inference."""
    def __init__(self, base):
        self.base = base
        self.calls = []
        self.cuts = []
        self.aligned = []
        self.fail = None
        self.mutate = None
        self.output = verification()
        self.os_output = str(os.getpid())
        self.gpu_output = 'synthetic|True'
        self.marks = [0, 60, 120]
        self.align_exit = 0

    def acquire(self):
        (self.base/'chapter-urls.txt').write_text('# synthetic\nhttps://example.invalid/one\nhttps://example.invalid/two\n', encoding='utf-8')
        (self.base/'text.txt').write_text('Invented birds gather.\nInvented clouds depart.\n', encoding='utf-8')
        write_json(self.base/'text.chapters.json', [dict(title=t, url=f'https://example.invalid/{u}', seg=i, n_sentences=1)
                                                    for i, (t,u) in enumerate([('First Ω', 'one'), ('Second', 'two')])])

    def names(self):
        return [Path(args[1]).name for args, _ in self.calls if args[0] == sys.executable and len(args)>1 and args[1]!='-c']

    def __call__(self, args, **kwargs):
        args = list(args)
        self.calls.append((args, kwargs))
        name = Path(args[1]).name if args[0]==sys.executable and len(args)>1 else Path(args[0]).name
        output = ''
        rc = 0
        if self.fail == name:
            rc = 7
        elif args[0]=='powershell':
            output = self.os_output
        elif args[0]==sys.executable and args[1]=='-c':
            output = self.gpu_output
        elif '-show_chapters' in args:
            output = json.dumps({'chapters': [{'start_time': str(n)} for n in self.marks]})
        elif '-version' in args:
            output = 'synthetic binary'
        elif name in ('list_chapters.py', 'fetch_text.py'):
            self.acquire()
        elif name=='probe_m4b.py':
            output = 'Invented raw marks for owner review.\n'
        elif name=='verify_tracks.py':
            output = self.output
        else:
            module = importlib.import_module(Path(name).stem)
            def fake_decode(argv, **unused):
                self.cuts.append(argv)
                Path(argv[-1]).write_bytes(b'synthetic-placeholder'*60)
                return subprocess.CompletedProcess(argv, 0)
            def fake_align(argv, **unused):
                self.aligned.append(argv)
                text = Path(argv[argv.index('--text')+1]).read_text(encoding='utf-8').splitlines()
                out = Path(argv[argv.index('--out')+1])
                write_json(out, dict(title='Invented', audio='synthetic.wav', segments=[
                    dict(id=i, text=s, start=i*2.0, end=i*2.0+1.0) for i,s in enumerate(text)]))
                return subprocess.CompletedProcess(argv, self.align_exit)
            with contextlib.ExitStack() as stack:
                stack.enter_context(warnings.catch_warnings())
                # Existing helpers use short-lived json.load(open(...)); unrelated
                # ResourceWarnings do not drown out these behavioral failures.
                warnings.simplefilter('ignore', ResourceWarning)
                stack.enter_context(patch.object(sys, 'argv', args[1:]))
                if name=='m4b_make_units.py':
                    stack.enter_context(patch.object(module, 'ffprobe_chapters', return_value=[(n,'Invented') for n in self.marks]))
                if name=='m4b_cut.py':
                    stack.enter_context(patch.object(module, 'require', side_effect=lambda binary,env: binary))
                    stack.enter_context(patch.object(module.subprocess, 'run', side_effect=fake_decode))
                if name=='align_chapters.py':
                    stack.enter_context(patch.object(module, 'preflight', return_value=True))
                    stack.enter_context(patch.object(module.subprocess, 'run', side_effect=fake_align))
                    stack.enter_context(patch.object(module.time, 'sleep'))
                    stack.enter_context(patch.dict(sys.modules, {'soundfile': types.SimpleNamespace(info=lambda path: types.SimpleNamespace(duration=60))}))
                try:
                    module.main()
                except SystemExit as error:
                    rc = error.code if type(error.code) is int else 1
        if self.mutate:
            self.mutate(name, args)
        if kwargs.get('log'):
            Path(kwargs['log']).write_text(output, encoding='utf-8')
        return subprocess.CompletedProcess(args, rc, output, '')


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        import builtins
        import socket
        original_import = builtins.__import__
        def no_models(name, *args, **kwargs):
            if name.split('.')[0] in {'torch', 'torchaudio', 'transformers'}:
                raise AssertionError('synthetic gate must not import models')
            return original_import(name, *args, **kwargs)
        guard = contextlib.ExitStack()
        self.addCleanup(guard.close)
        guard.enter_context(patch('builtins.__import__', side_effect=no_models))
        guard.enter_context(patch.object(socket, 'socket', side_effect=AssertionError('synthetic gate must not use network')))
        self.temp = tempfile.TemporaryDirectory(prefix='reader-invented-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()/'space Ω'
        self.base.mkdir()
        self.path = self.base/'book.json'
        self.data = dict(book=1, title='Invented Ω book', tier='m4b', audio={'m4b':'invented Ω.m4b'},
                         text=dict(source='live', toc_from='invented start', toc_to='invented end'),
                         map=dict(m4b_starts=[0,1], m4b_end=2, confirmed=True), ship={'dropbox_dir':'unused destination'})
        (self.base/'invented Ω.m4b').write_bytes(b'not audio')
        self.runner = SyntheticRunner(self.base)
        self.runner.acquire()

    def run_book(self):
        write_json(self.path, self.data)
        with contextlib.redirect_stdout(io.StringIO()):
            return driver.execute(load_manifest(self.path), command=self.runner)

    def test_clean_real_helper_route_all_tracks_and_arguments(self):
        result = self.run_book()
        self.assertEqual(result['state'], 'prepared-local')
        self.assertEqual(result['delivery'], 'not-performed')
        self.assertEqual(len(result['roster']), 2)
        self.assertEqual(self.runner.names(), ['m4b_make_units.py','m4b_cut.py','align_chapters.py','m4b_cut.py','m4b_package.py','verify_tracks.py'])
        calls = {Path(a[1]).name:(a,k) for a,k in self.runner.calls if a[0]==sys.executable and a[1]!='-c'}
        units = calls['m4b_make_units.py'][0]
        self.assertEqual(units[units.index('--starts')+1], '0,1')
        self.assertEqual(units[units.index('--end')+1], '2')
        align, settings = calls['align_chapters.py']
        self.assertEqual(settings['env']['WI_PREFLIGHT_MAX_PY'], '2')
        self.assertIn('--auto-wps', align)
        self.assertEqual(align[align.index('--device')+1], 'cuda')
        self.assertEqual(align[align.index('--track-map')+1], str(self.base/'track-map.json'))
        verify = calls['verify_tracks.py'][0]
        self.assertEqual(verify[verify.index('--tracks')+1:verify.index('--points')], ['01','02'])
        self.assertIn('--wps-pre', verify)
        self.assertEqual(verify[verify.index('--points')+1], '5')
        self.assertFalse(any(x in align+verify for x in ('--fuzzy','--allow-undercover','--allow-wps-outliers')))
        self.assertEqual(len(self.runner.cuts), 4)
        self.assertIn('-ar', self.runner.cuts[0])
        self.assertIn('copy', self.runner.cuts[-1])
        self.assertTrue(all(k['cwd']==str(self.base) for _,k in self.runner.calls))
        self.assertFalse((self.base/'unused destination').exists())

    def test_gate_a_acquires_and_holds_then_confirmed_never_refetches(self):
        self.data['map']['confirmed'] = False
        for name in ('chapter-urls.txt','text.txt','text.chapters.json'):
            (self.base/name).unlink()
        self.assertEqual(self.run_book()['state'], 'gate-a-required')
        self.assertEqual(self.runner.names(), ['list_chapters.py','fetch_text.py','probe_m4b.py'])
        self.assertFalse((self.base/'units.json').exists())
        self.assertTrue((self.base/'probe.log').exists())
        self.runner.calls.clear()
        self.data['map']['confirmed'] = True
        self.assertEqual(self.run_book()['state'], 'prepared-local')
        self.assertNotIn('fetch_text.py', self.runner.names())
        self.assertNotIn('probe_m4b.py', self.runner.names())

    def test_more_than_default_six_tracks_are_all_verified(self):
        count=7
        self.data['map']=dict(m4b_starts=list(range(count)),m4b_end=count,confirmed=True)
        self.runner.marks=[i*60 for i in range(count+1)]
        self.runner.output=verification(count=count)
        (self.base/'chapter-urls.txt').write_text(''.join(f'https://example.invalid/{i}\n' for i in range(count)))
        (self.base/'text.txt').write_text(''.join(f'Invented sentence {i}.\n' for i in range(count)))
        write_json(self.base/'text.chapters.json', [dict(title=f'Invented {i}',url=f'https://example.invalid/{i}',seg=i,n_sentences=1) for i in range(count)])
        self.assertEqual(self.run_book()['state'],'prepared-local')
        argv=next(a for a,_ in self.runner.calls if 'verify_tracks.py' in a[1])
        self.assertEqual(argv[argv.index('--tracks')+1:argv.index('--points')],[f'{i:02d}' for i in range(1,count+1)])

    def test_missing_empty_extra_wav_refuses_before_alignment(self):
        for corruption in ('missing','empty','extra'):
            def mutate(name,args):
                if name=='m4b_cut.py' and args[args.index('--ext')+1]=='wav':
                    path=self.base/'wav/01.wav'
                    if corruption=='missing': path.unlink()
                    elif corruption=='empty': path.write_bytes(b'')
                    else: (self.base/'wav/03.wav').write_bytes(b'preserve')
            self.runner.mutate=mutate
            self.runner.calls.clear()
            with self.subTest(corruption=corruption),self.assertRaises(driver.ExecutionError): self.run_book()
            self.assertNotIn('align_chapters.py',self.runner.names())
            extra=self.base/'wav/03.wav'
            if extra.exists():
                self.assertEqual(extra.read_bytes(),b'preserve'); extra.unlink()

    def test_confirmed_missing_acquisition_fails_without_refetch(self):
        (self.base/'text.txt').unlink()
        with self.assertRaises(driver.ExecutionError):
            self.run_book()
        self.assertEqual(self.runner.names(), [])

    def test_partial_duplicate_reordered_or_bad_slices_stop_before_models(self):
        original = json.loads((self.base/'text.chapters.json').read_text())
        cases = [original[:1], original[::-1], [original[0],original[0]],
                 [{**original[0],'seg':True},original[1]], [{**original[0],'n_sentences':2},original[1]]]
        for markers in cases:
            with self.subTest(markers=markers):
                write_json(self.base/'text.chapters.json', markers)
                with self.assertRaises(driver.ExecutionError):
                    self.run_book()
                self.assertEqual(self.runner.names(), [])
        self.runner.acquire()
        (self.base/'chapter-urls.txt').write_text('same\nsame\n')
        with self.assertRaises(driver.ExecutionError):
            self.run_book()

    def test_every_stage_nonzero_stops_downstream(self):
        for stage in ('m4b_make_units.py','m4b_cut.py','align_chapters.py','m4b_package.py','verify_tracks.py'):
            with self.subTest(stage=stage):
                self.runner.calls.clear()
                self.runner.fail = stage
                with self.assertRaises(driver.ExecutionError):
                    self.run_book()
                self.assertEqual(self.runner.names()[-1], stage)
        self.data['map']['confirmed'] = False
        for stage in ('list_chapters.py','fetch_text.py','probe_m4b.py'):
            with self.subTest(stage=stage):
                self.runner.calls.clear(); self.runner.fail = stage
                with self.assertRaises(driver.ExecutionError):
                    self.run_book()
                self.assertEqual(self.runner.names()[-1], stage)

    def test_unknown_competitor_and_absent_gpu_refuse_before_alignment(self):
        for output in ('', 'not a pid', str(os.getpid())+'\n999999', str(os.getpid())+'\n'+str(os.getpid())):
            with self.subTest(output=output):
                self.runner.os_output = output; self.runner.calls.clear()
                with self.assertRaises(driver.ExecutionError):
                    self.run_book()
                self.assertNotIn('align_chapters.py', self.runner.names())
        self.runner.os_output = str(os.getpid())
        for output in ('synthetic|False','bad output','True'):
            with self.subTest(output=output):
                self.runner.gpu_output = output; self.runner.calls.clear()
                with self.assertRaises(driver.ExecutionError):
                    self.run_book()
                self.assertNotIn('align_chapters.py', self.runner.names())

    def test_unavailable_os_probe_and_nonzero_probe_fail_closed(self):
        original = self.runner.__call__
        for unavailable in (True, False):
            def command(args, **kw):
                if args[0]=='powershell':
                    if unavailable:
                        raise FileNotFoundError('synthetic missing powershell')
                    return subprocess.CompletedProcess(args,1,str(os.getpid()),'')
                return original(args, **kw)
            write_json(self.path, self.data)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(driver.ExecutionError):
                driver.execute(load_manifest(self.path),command=command)
            self.assertNotIn('align_chapters.py',self.runner.names())

    def test_preflight_is_repeated_before_probe_alignment_and_verification(self):
        self.data['map']['confirmed']=False
        self.runner.os_output='unknown'
        with self.assertRaises(driver.ExecutionError): self.run_book()
        self.assertNotIn('probe_m4b.py',self.runner.names())
        self.data['map']['confirmed']=True
        self.runner.os_output=str(os.getpid())
        self.runner.calls.clear()
        self.runner.mutate=lambda name,args: setattr(self.runner,'os_output','unknown') if name=='align_chapters.py' else None
        with self.assertRaises(driver.ExecutionError): self.run_book()
        self.assertIn('align_chapters.py',self.runner.names())
        self.assertNotIn('verify_tracks.py',self.runner.names())
        self.assertEqual(sum(args[0]=='powershell' for args,_ in self.runner.calls),2)

    def test_bad_binary_audio_marks_and_mapping_refuse(self):
        original = self.runner.__call__
        def command(args, **kw):
            return subprocess.CompletedProcess(args,1,'','') if '-version' in args else original(args,**kw)
        write_json(self.path, self.data)
        with self.assertRaises(driver.ExecutionError):
            driver.execute(load_manifest(self.path),command=command)
        for marks in ([],[0,0,120],[0,60],[0,float('nan'),120],[-1,60,120]):
            with self.subTest(marks=marks):
                self.runner.marks=marks
                with self.assertRaises(driver.ExecutionError):
                    self.run_book()
                self.assertNotIn('align_chapters.py',self.runner.names())
        self.runner.marks=[0,60,120]
        (self.base/'invented Ω.m4b').write_bytes(b'')
        with self.assertRaises(driver.ExecutionError):
            self.run_book()

    def test_units_helper_incomplete_duplicate_boolean_or_wrong_map_refuses(self):
        for corrupt in ('omit','duplicate','boolean','map'):
            def mutate(name,args):
                if name=='m4b_make_units.py':
                    path=Path(args[args.index('--out-units')+1])
                    units=json.loads(path.read_text())
                    if corrupt=='omit': units.pop()
                    if corrupt=='duplicate': units[1]=units[0]
                    if corrupt=='boolean': units[0]['track']=True
                    write_json(path,units)
                    if corrupt=='map':
                        write_json(args[args.index('--out-trackmap')+1],[])
            self.runner.mutate=mutate
            with self.subTest(corrupt=corrupt), self.assertRaises(driver.ExecutionError):
                self.run_book()
            self.assertNotIn('align_chapters.py',self.runner.names())

    def test_reused_units_cannot_hide_boolean_track_as_integer(self):
        self.run_book()
        path=self.base/'units.json'
        units=json.loads(path.read_text()); units[0]['track']=True
        write_json(path,units)
        before=path.read_bytes()
        self.runner.calls.clear()
        with self.assertRaises(driver.ExecutionError): self.run_book()
        self.assertNotIn('m4b_cut.py',self.runner.names())
        self.assertEqual(path.read_bytes(),before)

    def test_alignment_and_package_corruption_fail_before_acceptance(self):
        self.run_book()
        for corruption in ('missing-align','schema','sentence','missing-audio','package-schema','manifest'):
            def mutate(name,args):
                if name=='align_chapters.py':
                    path=next((self.base/'per-chapter').glob('chap00_*.json'))
                    if corruption=='missing-align': path.unlink()
                    if corruption in ('schema','sentence'):
                        doc=json.loads(path.read_text())
                        doc['segments'][0]['text']='Wrong invented sentence.'
                        if corruption=='schema': doc['segments'][0]['end']=-1
                        write_json(path,doc)
                if name=='m4b_package.py':
                    if corruption=='missing-audio': (self.base/'package/01.m4a').unlink()
                    if corruption=='package-schema': write_json(self.base/'package/align01.json',{})
                    if corruption=='manifest': write_json(self.base/'package/manifest.json',dict(book=self.data['title'],tracks=[]))
            with self.subTest(corruption=corruption):
                self.runner.mutate=mutate
                with self.assertRaises(driver.ExecutionError): self.run_book()
                self.runner.mutate=None
                for path in (self.base/'per-chapter').glob('*.json'):
                    path.unlink()
                self.run_book()

    def test_existing_extra_and_duplicate_outputs_preserved_and_refused(self):
        for name in ('wav/001.wav','per-chapter/chap99_old.json','package/old.json','package/03.m4a'):
            path=self.base/name; path.parent.mkdir(exist_ok=True); path.write_bytes(b'preserve')
            with self.subTest(name=name), self.assertRaises(driver.ExecutionError): self.run_book()
            self.assertEqual(path.read_bytes(),b'preserve'); path.unlink()
        self.run_book()
        path=self.base/'per-chapter/chap00_duplicate.json'; path.write_text('{}')
        with self.assertRaises(driver.ExecutionError): self.run_book()
        self.assertTrue(path.exists())

    def test_one_flag_at_exit_zero_requires_gate_b_and_fresh_log(self):
        (self.base/'verify.log').write_text(verification())
        self.runner.output=verification(flags=1)
        result=self.run_book()
        self.assertEqual(result['state'],'gate-b-required')
        self.assertEqual(result['flags'],[dict(track=1,minute=0.0,overlap=0.1,status='FLAG')])
        self.assertEqual(result['roster'][0]['state'],'flagged')
        self.assertEqual((self.base/'verify.log').read_text(),self.runner.output)
        self.runner.output=verification()
        self.assertEqual(self.run_book()['state'],'prepared-local')

    def test_strict_verifier_adversarial_conservation_and_failures(self):
        clean=verification()
        cases=['', '=== 0/0 points PASS, 0 FLAG (100% fail; threshold 40%) ===',
               clean+clean,clean.replace('10/10','9/10'),clean.replace('10/10','10/5'),
               clean.replace('0 FLAG','1 FLAG'),clean.replace('threshold 40%','threshold 99%'),
               clean.replace('=== 10/10','=== -1/10'),clean.replace('t= 0.0min','t= nanmin'),
               clean.replace('t= 0.0min  overlap=0.90  PASS\n','',1),
               clean.replace('===== track 02','===== track 01'),
               'WPS GATE FAIL\n'+clean,clean+'\n[02] (no align/audio)',
               clean.replace('===== track 01', '===== track 99'),
               clean.replace('===== track 01  (1.0min audio, text to 1.0min)  chapters: Invented =====',
                             '===== track 01  (1.0min audio, text to 1.0min)  chapters: Invented =====\n'*2),
               clean+'\n t=0.5min overlap=0.9 PASS']
        for output in cases:
            with self.subTest(output=output), self.assertRaises(driver.ExecutionError):
                driver._verification(output,2)
        with self.assertRaises(driver.ExecutionError): driver._verification(verification(count=1),2)
        self.assertEqual(driver._verification(verification(count=7),7),[])

    def test_interrupted_and_repeated_calls_use_real_helper_resume_fresh_verify(self):
        unrelated=self.base/'keep-private.bin'; unrelated.write_bytes(b'preserve')
        self.runner.align_exit=1
        with self.assertRaises(driver.ExecutionError): self.run_book()
        self.assertNotIn('m4b_package.py',self.runner.names())
        self.runner.align_exit=0
        self.run_book()
        times={name:(self.base/name).stat().st_mtime_ns for name in ('units.json','track-map.json','wav/01.wav','package/01.m4a')}
        cuts,aligned=len(self.runner.cuts),len(self.runner.aligned)
        self.runner.calls.clear()
        self.run_book()
        self.assertEqual(len(self.runner.cuts),cuts)
        self.assertEqual(len(self.runner.aligned),aligned)
        self.assertEqual(times,{name:(self.base/name).stat().st_mtime_ns for name in times})
        self.assertEqual(self.runner.names().count('verify_tracks.py'),1)
        self.assertEqual(unrelated.read_bytes(),b'preserve')
        # Existing helper contract: changing inputs makes outputs stale and reruns.
        future=max(times.values())/1e9+10
        os.utime(self.base/'invented Ω.m4b',(future,future))
        self.run_book()
        self.assertGreater(len(self.runner.cuts),cuts)
        self.assertGreater(len(self.runner.aligned),aligned)

    def test_coverage_gap_child_cannot_resume_as_success(self):
        self.runner.align_exit=3
        with self.assertRaises(driver.ExecutionError): self.run_book()
        self.assertEqual(list((self.base/'per-chapter').glob('*.json')),[])
        self.assertNotIn('m4b_package.py',self.runner.names())

    def test_explicit_cli_mode_hold_and_failure_exit_codes(self):
        write_json(self.path,self.data)
        with patch.object(driver,'execute',return_value={'state':'gate-a-required'}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(driver.main([str(self.path),'--execute']),3)
        with patch.object(driver,'execute',return_value={'state':'prepared-local'}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(driver.main([str(self.path),'--execute']),0)
        with patch.object(driver,'execute',side_effect=driver.ExecutionError('invented')), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error: driver.main([str(self.path),'--execute'])
            self.assertEqual(error.exception.code,2)

    def test_real_subprocess_array_unicode_and_streamed_log(self):
        script=self.base/'invented child Ω.py'
        script.write_text('import sys,json; print(json.dumps(sys.argv[1:],ensure_ascii=False))',encoding='utf-8')
        log=self.base/'child.log'
        with contextlib.redirect_stdout(io.StringIO()):
            result=driver._command([sys.executable,str(script),'value Ω with spaces','0,1','--boolean'],
                                   cwd=str(self.base),env={**os.environ,'PYTHONIOENCODING':'utf-8'},log=log)
        self.assertEqual(result.returncode,0)
        self.assertEqual(json.loads(result.stdout),['value Ω with spaces','0,1','--boolean'])
        self.assertEqual(log.read_text(encoding='utf-8'),result.stdout)

    def test_private_outputs_refuse_git_dropbox_symlink_and_manifest_collision(self):
        for marker in ('.git','Dropbox'):
            if marker=='.git':
                (self.base/marker).mkdir()
                with self.assertRaises(driver.ExecutionError): self.run_book()
                (self.base/marker).rmdir()
            else:
                write_json(self.path,self.data)
                manifest=load_manifest(self.path); manifest['work_directory']=str(self.base/marker)
                with self.assertRaises(driver.ExecutionError): driver.execute(manifest,command=self.runner)
        (self.base/'wav').symlink_to(self.base/'other',target_is_directory=True)
        with self.assertRaises(driver.ExecutionError): self.run_book()
        (self.base/'wav').unlink()
        for name in ('text.txt','verify.log'):
            write_json(self.path,self.data)
            manifest=load_manifest(self.path); manifest['manifest']=str(self.base/name)
            with self.assertRaises(driver.ExecutionError): driver.execute(manifest,command=self.runner)


if __name__=='__main__':
    unittest.main()
