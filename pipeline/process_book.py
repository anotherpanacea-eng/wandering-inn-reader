"""Plan or explicitly execute the bounded m4b/live pipeline; never deliver."""
import argparse
import json
import os
import math
import re
import subprocess
import tempfile
from pathlib import Path
import sys

# The planner promises no per-book artifacts, including an import bytecode cache.
sys.dont_write_bytecode = True
from book_manifest import ManifestError, load_manifest


def build_plan(manifest):
    base = manifest['work_directory']
    artifacts = {name: os.path.join(base, path) for name, path in {
        'urls': 'chapter-urls.txt', 'text_prefix': 'text', 'text': 'text.txt',
        'chapters': 'text.chapters.json', 'units': 'units.json', 'track_map': 'track-map.json',
        'wav': 'wav', 'alignments': 'per-chapter', 'package': 'package'}.items()}
    mapping = manifest['map']
    unresolved = [] if 'm4b_starts' in mapping else ['map.m4b_starts', 'map.m4b_end']
    stages = []

    def stage(name, script, parameters, dependencies=()):
        stages.append({'name': name, 'script': script, 'parameters': parameters,
                       'depends_on': list(dependencies), 'executed': False})

    stage('range', 'list_chapters.py', {'--from': manifest['text']['toc_from'],
          '--to': manifest['text']['toc_to'], '--out': artifacts['urls']})
    stage('text', 'fetch_text.py', {'--url-file': artifacts['urls'], '--out': artifacts['text_prefix']}, ['range'])
    stage('probe', 'probe_m4b.py', {'--m4b': manifest['audio']['m4b'], '--text': artifacts['text'],
          '--chapters': artifacts['chapters']}, ['text'])
    stages.append({'name': 'gate-a', 'state': 'user-asserted' if mapping['confirmed'] else 'pending',
                   'verified': False, 'unresolved_parameters': unresolved,
                   'description': 'Human audio-to-chapter map review; supplied confirmation is an unverified user assertion.'})
    stage('units', 'm4b_make_units.py', {'--m4b': manifest['audio']['m4b'], '--chapters': artifacts['chapters'],
          '--starts': mapping.get('m4b_starts'), '--end': mapping.get('m4b_end'),
          '--out-units': artifacts['units'], '--out-trackmap': artifacts['track_map']}, ['gate-a'])
    stage('alignment-audio', 'm4b_cut.py', {'--m4b': manifest['audio']['m4b'], '--units': artifacts['units'],
          '--outdir': artifacts['wav'], '--ext': 'wav'}, ['units'])
    wav_glob = os.path.join(artifacts['wav'], '*.wav')
    stage('align', 'align_chapters.py', {'--audio-glob': wav_glob, '--text': artifacts['text'],
          '--track-map': artifacts['track_map'], '--outdir': artifacts['alignments'], '--auto-wps': True}, ['alignment-audio'])
    stage('playback-audio', 'm4b_cut.py', {'--m4b': manifest['audio']['m4b'], '--units': artifacts['units'],
          '--outdir': artifacts['package'], '--ext': 'm4a'}, ['units'])
    stage('package', 'm4b_package.py', {'--units': artifacts['units'], '--per-chapter': artifacts['alignments'],
          '--out': artifacts['package'], '--book-title': manifest['title']}, ['align', 'playback-audio'])
    stage('verify', 'verify_tracks.py', {'--dir': artifacts['package'], '--audio-glob': wav_glob,
          '--wps-pre': True}, ['package'])
    stages.append({'name': 'gate-b', 'state': 'pending-runtime-assessment', 'verified': False,
                   'description': 'Any FLAG at exit0 requires boundary review. Nonzero exit, missing/malformed summary, zero sampled points or inconsistent counts block shipping and cannot be waived by approval.'})
    stages.append({'name': 'delivery', 'state': 'separately-authorized-action-required' if manifest['ship'] else 'not-requested',
                   'destination': manifest['ship']['dropbox_dir'] if manifest['ship'] else None,
                   'description': 'A named destination is not transfer authority; no delivery is performed.'})
    return {'mode': 'plan-only', 'inputs': manifest, 'artifacts': artifacts, 'stages': stages,
            'warnings': ['Inputs and executable availability were not checked.',
                         'Human assertions and verification were not checked; this plan grants no execution or delivery authority.',
                         'No audiobook has been processed; all stages are descriptions, not runnable commands.']}


class ExecutionError(ValueError):
    """A failed stage or inconsistent local artifact; cannot be human-waived."""


def _json(path):
    from book_manifest import _unique_object, _nonfinite
    return json.loads(Path(path).read_text(encoding='utf-8'),
                      object_pairs_hook=_unique_object, parse_constant=_nonfinite)


def _command(args, *, cwd, env, capture=False, log=None, timeout=None):
    """Ordinary sequential child; streamed review logs remain local."""
    if log is None:
        return subprocess.run(args, cwd=cwd, env=env, capture_output=capture,
                              text=True, encoding='utf-8', errors='replace', timeout=timeout)
    output = []
    with Path(log).open('w', encoding='utf-8') as stream:
        with subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                              errors='replace') as child:
            for line in child.stdout:
                stream.write(line); stream.flush()
                print(line, end='', flush=True)
                output.append(line)
            return subprocess.CompletedProcess(args, child.wait(), ''.join(output))


def _preflight(command, base, env):
    # Identify the actual driver, rather than granting an arbitrary process count.
    status = command(['powershell', '-NoProfile', '-Command',
                      "Get-Process -Name 'python*' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
                     cwd=base, env=env, capture=True, timeout=30)
    lines = (status.stdout or '').strip().splitlines()
    if status.returncode or not lines or any(not line.strip().isdigit() for line in lines):
        raise ExecutionError('preflight: unknown competing-Python process status')
    pids = [int(line.strip()) for line in lines]
    if pids != [os.getpid()]:
        raise ExecutionError('preflight: unexpected Python processes; only the driver may be active')
    gpu = command([sys.executable, '-c',
                   "import torch,sys; sys.stdout.write(f'{torch.__version__}|{torch.cuda.is_available()}')"],
                  cwd=base, env=env, capture=True, timeout=180)
    if gpu.returncode or not re.fullmatch(r'[^|\s]+\|True', (gpu.stdout or '').strip()):
        raise ExecutionError('preflight: selected interpreter has no verified CUDA/ROCm GPU')


def _acquisition(paths):
    urls = [line.strip() for line in Path(paths['urls']).read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.lstrip().startswith('#')]
    markers = _json(paths['chapters'])
    lines = [line.strip() for line in Path(paths['text']).read_text(encoding='utf-8').splitlines() if line.strip()]
    if not urls or len(set(urls)) != len(urls) or not isinstance(markers, list) or len(markers) != len(urls):
        raise ExecutionError('text: incomplete/duplicate requested chapter roster')
    cursor = 0
    for url, chapter in zip(urls, markers):
        if not isinstance(chapter, dict) or chapter.get('url') != url or not isinstance(chapter.get('title'), str) or not chapter['title'].strip():
            raise ExecutionError('text: omitted/reordered chapter URL or missing title')
        count = chapter.get('n_sentences')
        if type(chapter.get('seg')) is not int or chapter['seg'] != cursor or type(count) is not int or count <= 0:
            raise ExecutionError('text: invalid chapter slice/count')
        cursor += count
    if not lines or cursor != len(lines):
        raise ExecutionError('text: chapter slices do not conserve all nonblank sentences')
    return markers, lines


def _units(units, trackmap, chapters, mapping, marks):
    starts = mapping['m4b_starts']; end = mapping['m4b_end']
    if len(starts) != len(chapters) or end >= len(marks):
        raise ExecutionError('map: starts count or raw end-mark index does not match inputs')
    bounds = starts + [end]
    expected = []
    for i, chapter in enumerate(chapters):
        a, b = round(marks[bounds[i]], 3), round(marks[bounds[i+1]], 3)
        if b <= a:
            raise ExecutionError('units: nonpositive chapter duration')
        expected.append(dict(track=i+1, title=chapter['title'], seg=chapter['seg'], start=a, end=b))
    expected_map = [dict(title=c['title'], seg=c['seg'], tracks=[i+1]) for i,c in enumerate(chapters)]
    if (not isinstance(units, list) or not isinstance(trackmap, list)
            or any(not isinstance(u, dict) or type(u.get('track')) is not int
                   or type(u.get('seg')) is not int
                   or any(type(u.get(k)) not in (int, float) for k in ('start', 'end')) for u in units)
            or any(not isinstance(u, dict) or type(u.get('seg')) is not int
                   or not isinstance(u.get('tracks'), list)
                   or any(type(n) is not int for n in u['tracks']) for u in trackmap)
            or units != expected or trackmap != expected_map):
        raise ExecutionError('units: missing/duplicate/extra or mismatched unit/track-map roster')
    return expected


def _audio_roster(directory, suffix, count, *, complete):
    files = list(Path(directory).glob('*' + suffix))
    expected = {f'{i:02d}{suffix}' for i in range(1, count+1)}
    if any(p.name not in expected or p.is_symlink() for p in files):
        raise ExecutionError('audio: unexpected/duplicate output; preserve and inspect existing files')
    if complete and ({p.name for p in files} != expected or any(not p.is_file() or p.stat().st_size <= 0 for p in files)):
        raise ExecutionError('audio: incomplete/empty output roster')


def _alignments(directory, chapters, lines, units, *, complete):
    from schema import validate_doc
    from align_chapters import slug
    for i, chapter in enumerate(chapters):
        text_path = Path(directory)/f"chap{i:02d}_{slug(chapter['title'])}.txt"
        if text_path.is_symlink():
            raise ExecutionError('align: chapter-text symlink redirect; preserve and inspect')
    by_index = {}
    for path in Path(directory).glob('*.json'):
        match = re.fullmatch(r'chap(\d+)_.*\.json', path.name)
        i = int(match[1]) if match else -1
        if i not in range(len(chapters)) or i in by_index or path.is_symlink():
            raise ExecutionError('align: unexpected/duplicate chapter output; preserve and inspect')
        by_index[i] = path
    if complete and set(by_index) != set(range(len(chapters))):
        raise ExecutionError('align: incomplete chapter output roster')
    if complete:
        for i, path in by_index.items():
            doc = _json(path); validate_doc(doc, source=str(path))
            start = chapters[i]['seg']; stop = start + chapters[i]['n_sentences']
            if [s['text'] for s in doc['segments']] != lines[start:stop]:
                raise ExecutionError('align: missing/reordered source sentences')
            if any(s['end'] > units[i]['end']-units[i]['start']+0.01 for s in doc['segments']):
                raise ExecutionError('align: segment outside audio unit')
    return by_index


def _package(directory, chapters, lines, units, title, *, complete):
    from schema import validate_doc
    expected = {f'align{i:02d}.json' for i in range(1, len(chapters)+1)} | {'manifest.json'}
    files = list(Path(directory).glob('*.json'))
    if any(p.name not in expected or p.is_symlink() for p in files):
        raise ExecutionError('package: unexpected output; preserve and inspect')
    _audio_roster(directory, '.m4a', len(chapters), complete=complete)
    if not complete:
        return
    if {p.name for p in files} != expected:
        raise ExecutionError('package: incomplete JSON roster')
    manifest = _json(Path(directory)/'manifest.json')
    tracks = manifest.get('tracks') if isinstance(manifest, dict) else None
    if not isinstance(tracks, list) or len(tracks) != len(chapters) or manifest.get('book') != title:
        raise ExecutionError('package: incomplete manifest roster')
    for i, (chapter, unit, track) in enumerate(zip(chapters, units, tracks), 1):
        audio, name = f'{i:02d}.m4a', f'align{i:02d}.json'
        doc = _json(Path(directory)/name); validate_doc(doc, source=name)
        start, stop = chapter['seg'], chapter['seg'] + chapter['n_sentences']
        if (doc['audio'] != audio or [s['text'] for s in doc['segments']] != lines[start:stop]
                or doc.get('chapters') != [dict(title=chapter['title'], start=round(doc['segments'][0]['start'],3), seg=0)]
                or any(s['end'] > unit['end']-unit['start']+0.01 for s in doc['segments'])):
            raise ExecutionError('package: wrong chapter/audio/sentence mapping')
        expected_track = dict(track=f'{i:02d}', file=name, audio=audio,
                              minutes=round((unit['end']-unit['start'])/60,1),
                              sentences=len(doc['segments']),
                              chapters=[dict(title=chapter['title'], start=round(doc['segments'][0]['start'],3))])
        if (not isinstance(track, dict) or type(track.get('sentences')) is not int
                or type(track.get('minutes')) not in (int, float) or track != expected_track):
            raise ExecutionError('package: mismatched manifest track')


def _verification(output, count):
    summaries = []; points = {i: [] for i in range(1,count+1)}; current = None
    seen = set()
    for line in output.splitlines():
        line = line.strip()
        if 'GATE FAIL' in line or '(no align/audio)' in line or '--allow-wps-outliers' in line:
            raise ExecutionError('verify: failed or waived verification')
        if line.startswith('===== track '):
            match = re.fullmatch(r'===== track (\d+)  \(.*\)  chapters: .* =====', line)
            current = int(match[1]) if match else None
            if current not in points or current in seen or summaries:
                raise ExecutionError('verify: unexpected/duplicate/malformed track header')
            seen.add(current)
        elif line.startswith('t='):
            match = re.fullmatch(r't=\s*(-?\d+(?:\.\d+)?)min\s+overlap=(\d+(?:\.\d+)?)\s+(PASS|FLAG)', line)
            if not match or current is None or summaries or float(match[1]) < 0 or not 0 <= float(match[2]) <= 1:
                raise ExecutionError('verify: malformed sample point')
            points[current].append(dict(track=current, minute=float(match[1]), overlap=float(match[2]), status=match[3]))
        elif 'points PASS' in line:
            match = re.fullmatch(r'=== (\d+)/(\d+) points PASS, (\d+) FLAG \(\d+% fail; threshold 40%\) ===', line)
            if not match:
                raise ExecutionError('verify: malformed summary')
            summaries.append(tuple(int(n) for n in match.groups()))
    all_points = [point for rows in points.values() for point in rows]
    flags = [point for point in all_points if point['status']=='FLAG']
    if (len(summaries)!=1 or any(len(rows)!=5 for rows in points.values())
            or summaries[0] != (len(all_points)-len(flags), count*5, len(flags))):
        raise ExecutionError('verify: missing/ambiguous/inconsistent summary or five-point track roster')
    return flags


def execute(manifest, *, command=None, smartctl=None, smartctl_dev=None):
    """Only the existing explicit m4b/live route; no approval latch or delivery.

    smartctl/smartctl_dev pass through to align_chapters.py's drive-temperature
    watchdog; without smartctl, alignment runs with fixed cooldowns only, as before.
    """
    command = command or _command
    base = Path(manifest['work_directory']).resolve()
    paths = build_plan(manifest)['artifacts']
    env = os.environ.copy()
    try:
        if any(p.lower()=='dropbox' for p in base.parts):
            raise ExecutionError('work directory: active book caches must stay outside Dropbox')
        for path in [*paths.values(), str(base/'probe.log'), str(base/'verify.log')]:
            target = Path(path)
            if any((p/'.git').exists() or (p/'.git').is_symlink() for p in (target,*target.parents)):
                raise ExecutionError('work directory: keep private book artifacts outside Git')
            if target.is_symlink() or any(p.is_symlink() for p in target.parents):
                raise ExecutionError('work directory: symlink artifact redirects are refused')
        if Path(manifest['manifest']).resolve() in {Path(v).resolve() for v in [*paths.values(), str(base/'probe.log'), str(base/'verify.log')]}:
            raise ExecutionError('manifest: collides with a generated artifact')
        audio = Path(manifest['audio']['m4b'])
        if audio.suffix.lower() != '.m4b' or not audio.is_file() or audio.stat().st_size<=0:
            raise ExecutionError('audio: explicit nonempty local m4b required')
        # Lazy stdlib helper: --plan never probes executable availability.
        from m4b_common import FFMPEG, FFPROBE
        env.update(FFMPEG=FFMPEG, FFPROBE=FFPROBE)
        for binary in (FFMPEG, FFPROBE):
            result = command([binary,'-version'],cwd=str(base),env=env,capture=True,timeout=30)
            if result.returncode:
                raise ExecutionError('tools: ffmpeg/ffprobe is not runnable')
        helper_dir = Path(__file__).resolve().parent
        def stage(name, arguments, *, model=False, log=None, child_env=None):
            if model:
                _preflight(command, str(base), env)
            print('Stage: '+name, flush=True)
            result = command([sys.executable,str(helper_dir/name),*arguments],cwd=str(base),
                             env=child_env or env,log=log)
            if result.returncode:
                raise ExecutionError(f'{name}: child exit {result.returncode}; downstream stages refused')
            return result.stdout or ''
        if not manifest['map']['confirmed']:
            stage('list_chapters.py',['--from',manifest['text']['toc_from'],'--to',manifest['text']['toc_to'],'--out',paths['urls']])
            stage('fetch_text.py',['--url-file',paths['urls'],'--out',paths['text_prefix']])
            chapters, lines = _acquisition(paths)
            stage('probe_m4b.py',['--m4b',str(audio),'--text',paths['text'],'--chapters',paths['chapters'],'--ffmpeg',FFMPEG],
                  model=True,log=str(base/'probe.log'))
            return dict(mode='execute',state='gate-a-required',roster=[dict(chapter=i+1,title=c['title'],state='pending-map') for i,c in enumerate(chapters)],
                        artifacts=paths,probe_log=str(base/'probe.log'),
                        review='Review probe.log and set raw map.m4b_starts/m4b_end and confirmed=true. No cuts or alignment performed.')
        chapters, lines = _acquisition(paths)
        probe = command([FFPROBE,'-v','error','-print_format','json','-show_chapters',str(audio)],cwd=str(base),env=env,capture=True,timeout=30)
        if probe.returncode:
            raise ExecutionError('map: ffprobe chapter-mark read failed')
        raw_marks = json.loads(probe.stdout)['chapters']
        if (not isinstance(raw_marks, list) or any(not isinstance(c, dict)
                or type(c.get('start_time')) not in (str, int, float) for c in raw_marks)):
            raise ExecutionError('map: malformed raw chapter marks')
        marks = [float(c['start_time']) for c in raw_marks]
        if not marks or any(not math.isfinite(x) or x<0 for x in marks) or any(b<=a for a,b in zip(marks,marks[1:])):
            raise ExecutionError('map: invalid raw chapter marks')
        mapping = manifest['map']
        if len(mapping['m4b_starts']) != len(chapters) or mapping['m4b_end']>=len(marks):
            raise ExecutionError('map: starts count or raw end-mark index does not match inputs')
        # The units helper always writes; temp outputs avoid invalidating unchanged
        # track-map mtimes and therefore preserve its existing alignment resume path.
        with tempfile.TemporaryDirectory(prefix='.units-',dir=base) as temporary:
            unit_path, map_path = Path(temporary)/'units.json', Path(temporary)/'track-map.json'
            stage('m4b_make_units.py',['--m4b',str(audio),'--chapters',paths['chapters'],
                  '--starts',','.join(str(n) for n in mapping['m4b_starts']),'--end',str(mapping['m4b_end']),
                  '--out-units',str(unit_path),'--out-trackmap',str(map_path),'--ffprobe',FFPROBE])
            units = _units(_json(unit_path),_json(map_path),chapters,mapping,marks)
            for source, destination in ((unit_path,paths['units']),(map_path,paths['track_map'])):
                if not Path(destination).exists() or _json(destination)!=_json(source):
                    os.replace(source,destination)
        # JSON equality treats true as 1; validate the actual files reused by
        # children as well as the freshly generated helper outputs.
        _units(_json(paths['units']),_json(paths['track_map']),chapters,mapping,marks)
        _audio_roster(paths['wav'],'.wav',len(chapters),complete=False)
        _alignments(paths['alignments'],chapters,lines,units,complete=False)
        _package(paths['package'],chapters,lines,units,manifest['title'],complete=False)
        stage('m4b_cut.py',['--m4b',str(audio),'--units',paths['units'],'--outdir',paths['wav'],'--ext','wav','--ffmpeg',FFMPEG])
        _audio_roster(paths['wav'],'.wav',len(chapters),complete=True)
        child_env = {**env,'WI_PREFLIGHT_MAX_PY':'2'}
        watchdog = ['--smartctl',smartctl,*(['--smartctl-dev',smartctl_dev] if smartctl_dev else [])] if smartctl else []
        stage('align_chapters.py',['--audio-glob',str(Path(paths['wav'])/'*.wav'),'--text',paths['text'],
              '--track-map',paths['track_map'],'--outdir',paths['alignments'],'--auto-wps','--device','cuda',*watchdog],
              model=True,child_env=child_env)
        _alignments(paths['alignments'],chapters,lines,units,complete=True)
        stage('m4b_cut.py',['--m4b',str(audio),'--units',paths['units'],'--outdir',paths['package'],'--ext','m4a','--ffmpeg',FFMPEG])
        stage('m4b_package.py',['--units',paths['units'],'--per-chapter',paths['alignments'],'--out',paths['package'],'--book-title',manifest['title']])
        _package(paths['package'],chapters,lines,units,manifest['title'],complete=True)
        output = stage('verify_tracks.py',['--dir',paths['package'],'--audio-glob',str(Path(paths['wav'])/'*.wav'),
                       '--wps-pre','--tracks',*[f'{i:02d}' for i in range(1,len(chapters)+1)],
                       '--points','5','--win','8','--min-overlap','0.4','--max-fail-frac','0.4'],
                       model=True,log=str(base/'verify.log'))
        flags = _verification(output,len(chapters))
        flagged = {point['track'] for point in flags}
        return dict(mode='execute',state='gate-b-required' if flags else 'prepared-local',flags=flags,
                    roster=[dict(chapter=i+1,title=c['title'],state='flagged' if i+1 in flagged else 'aligned') for i,c in enumerate(chapters)],
                    delivery='not-performed')
    except ExecutionError:
        raise
    except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError, subprocess.SubprocessError) as error:
        raise ExecutionError('execution: '+str(error)) from error


def main(argv=None):
    # Children emit UTF-8 review transcripts, including paths and chapter titles.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true', help='describe only; reads only the manifest')
    mode.add_argument('--execute', action='store_true', help='explicit local execution; no delivery')
    parser.add_argument('--smartctl', help="smartctl path for alignment's drive-temperature watchdog")
    parser.add_argument('--smartctl-dev', help="smartctl device to protect (align_chapters.py default: /dev/sdb)")
    args = parser.parse_args(argv)
    if args.smartctl_dev and not args.smartctl:
        parser.error('--smartctl-dev requires --smartctl')
    try:
        manifest = load_manifest(args.manifest)
        plan = (build_plan(manifest) if args.plan
                else execute(manifest, smartctl=args.smartctl, smartctl_dev=args.smartctl_dev))
    except (ManifestError, ExecutionError) as error:
        parser.error(str(error))
    print(json.dumps(plan, indent=2))
    return 3 if plan.get('state', '').endswith('-required') else 0


if __name__ == '__main__':
    sys.exit(main())
