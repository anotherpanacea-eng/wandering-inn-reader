"""Describe the tier-1 pipeline; this command has no execution mode."""
import argparse
import json
import os
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--plan', action='store_true', required=True, help='describe only; no execution mode exists')
    args = parser.parse_args(argv)
    try:
        plan = build_plan(load_manifest(args.manifest))
    except ManifestError as error:
        parser.error(str(error))
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
