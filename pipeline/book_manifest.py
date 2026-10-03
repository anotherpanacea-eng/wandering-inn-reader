"""Strict, model-free manifest subset for the tier-1 descriptive planner."""
import json
import os
import re


class ManifestError(ValueError):
    """A named manifest diagnostic suitable for the CLI."""


def _object(value, allowed, field):
    if not isinstance(value, dict):
        raise ManifestError(f"{field}: expected an object")
    for key in value:
        if key not in allowed:
            raise ManifestError(f"{field}.{key}: unsupported field")
    return value


def _string(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field}: expected a nonblank string")
    return value


def _integer(value, field, minimum=0):
    if type(value) is not int or value < minimum:
        raise ManifestError(f"{field}: expected an integer >= {minimum}")
    return value


def _path(value, field, parent):
    value = _string(value, field)
    if '\x00' in value:
        raise ManifestError(f"{field}: NUL is not permitted in a local path")
    drive_path = os.name == 'nt' and re.match(r'^[A-Za-z]:[\\/]', value)
    if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', value) and not drive_path:
        raise ManifestError(f"{field}: expected a local path, not a URL or drive-relative path")
    return os.path.abspath(os.path.join(parent, value))


def validate_manifest(value, manifest_path):
    """Validate without examining audio, tools or proposed output locations."""
    manifest = os.path.abspath(os.fspath(manifest_path))
    parent = os.path.dirname(manifest)
    obj = _object(value, {'book', 'title', 'tier', 'audio', 'text', 'map', 'ship'}, 'manifest')
    book = _integer(obj.get('book'), 'book', 1)
    title = _string(obj.get('title'), 'title')
    if obj.get('tier') != 'm4b':
        raise ManifestError('tier: only explicit m4b is supported by this planner')
    audio = _object(obj.get('audio'), {'m4b'}, 'audio')
    audio_path = _path(audio.get('m4b'), 'audio.m4b', parent)
    text = _object(obj.get('text'), {'source', 'toc_from', 'toc_to'}, 'text')
    if text.get('source') != 'live':
        raise ManifestError('text.source: only live is supported by this planner')
    text = {'source': 'live', 'toc_from': _string(text.get('toc_from'), 'text.toc_from'),
            'toc_to': _string(text.get('toc_to'), 'text.toc_to')}
    mapping = _object(obj.get('map', {}), {'m4b_starts', 'm4b_end', 'confirmed'}, 'map')
    confirmed = mapping.get('confirmed', False)
    if type(confirmed) is not bool:
        raise ManifestError('map.confirmed: expected a boolean')
    has_starts, has_end = 'm4b_starts' in mapping, 'm4b_end' in mapping
    if has_starts != has_end:
        raise ManifestError('map: m4b_starts and m4b_end must be supplied together')
    normalized_map = {'confirmed': confirmed}
    if has_starts:
        starts = mapping['m4b_starts']
        if not isinstance(starts, list) or not starts:
            raise ManifestError('map.m4b_starts: expected a nonempty list')
        starts = [_integer(n, f'map.m4b_starts[{i}]') for i, n in enumerate(starts)]
        if any(b <= a for a, b in zip(starts, starts[1:])):
            raise ManifestError('map.m4b_starts: indices must be strictly increasing')
        end = _integer(mapping['m4b_end'], 'map.m4b_end')
        if end <= starts[-1]:
            raise ManifestError('map.m4b_end: must be greater than the final start')
        normalized_map.update(m4b_starts=starts, m4b_end=end)
    elif confirmed:
        raise ManifestError('map.confirmed: cannot confirm without starts and end')
    ship = None
    if 'ship' in obj:
        ship_obj = _object(obj['ship'], {'dropbox_dir'}, 'ship')
        ship = {'dropbox_dir': _path(ship_obj.get('dropbox_dir'), 'ship.dropbox_dir', parent)}
    return {'manifest': manifest, 'work_directory': parent, 'book': book, 'title': title,
            'tier': 'm4b', 'audio': {'m4b': audio_path}, 'text': text,
            'map': normalized_map, 'ship': ship}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f'JSON: duplicate key {key!r}')
        result[key] = value
    return result


def _nonfinite(value):
    raise ManifestError(f'JSON: nonfinite constant {value} is not permitted')


def load_manifest(path):
    """Read only the explicit manifest, with strict JSON parsing."""
    try:
        with open(path, encoding='utf-8') as stream:
            value = json.load(stream, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
    except ManifestError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise ManifestError(f'manifest: {error}') from error
    return validate_manifest(value, path)
