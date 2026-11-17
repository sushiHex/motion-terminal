"""Stage 1 of the body-mocap intake, receive: the licence gate, the delivery manifest, hashes, and the frozen revision.

    manifest = load_manifest(path)                     # parsed and validated; ReceiveError on anything it cannot take
    licence  = load_licence(tests_root, library)       # the signed licence record, validated
    record   = receive(manifest_path, tests_root=..., out_root=..., tool_versions={...}, settings={...})
    stage    = receive_stage()                         # a stage_runner.Stage named 'receive', reading ctx (below)

Design: docs/specs/2026-11-16-mocap-intake-design.md, sections 4 (five stages) and 5 (data contracts). Host Python,
stdlib only, no editor. The gate never fetches a page: it reads only the licence record a person wrote and signed.

Every refusal raises ReceiveError. Its `reason` names what was refused and its `state` is `unsupported` for input
outside the slice (a format other than asf_amc, a source kind other than library, an exchange other than simulated)
and `refused` for everything else (an unsigned licence, a missing permit, a hash that does not match, a revision that
is already frozen with other content). A refusal is not a rejected clip: nothing has been measured yet, so the run
stops here (stage_runner records the stage as failed and runs nothing after it).

Licence record, `<tests_root>/licenses/<library>.json` (the section 5 fields, plus `library` and `terms_excerpt`):

    {"library": "cmu", "terms_url": "...", "terms_excerpt": "<the sentence the terms rest on, quoted>",
     "terms_sha256": "<64 hex: the page text as read>", "attribution": null,
     "permits": {"redistribute_raw": true|false|null, "derivatives": ..., "public_display": ..., "commercial": ...},
     "facts": {"fps_source": {"value": 120, "url": "...", "provenance": "verified|unverified"}},
     "status": "checked|unchecked", "checked_by": "<the person who signs>", "checked_on": "YYYY-MM-DD"}

The record is SIGNED when status is `checked` and checked_by and checked_on are filled in; that is the field the
owner signs. 'Checked' and 'signed' mean those fields are populated, not a cryptographic signature: the gate trusts
the record file, and a terms hash being present does not prove the terms were read. An unsigned record refuses every delivery from that library, and every permit an `intended_use` entry
names must be exactly `true` (null is "not known", never a yes). `cmu_licence_template()` is the CMU record before
signing: the FAQ sentence, the permits the sentence supports (copying, modifying and redistributing) set true, and the
two it does not speak to (public display, commercial) left null for the signer to decide.

Frame rate. AMC carries none, so the rate is a declared fact. A clip's `fps_source` must equal the library record's
`fps_source` fact when there is one, and the fact's provenance (`unverified` when there is no fact, or the fact is
not marked verified) is carried on the clip's record.

Skeleton map, `<tests_root>/<manifest skeleton_map>`: {"library": "<id>", "bones": {"<source bone>": "<role>"}},
explicit and authoritative; one role per bone and no role claimed twice.

Freezing. Every delivered file is read once into memory, and those bytes must hash to the manifest's declared value;
the same bytes, never a later re-read of the delivery, are what is frozen, and each frozen copy is re-hashed against
the declared value. The manifest, the licence record, the skeleton map and the delivered files are written, with the
receipt, into `<out_root>/<batch>/r<revision>/receive/`, built in a temporary folder and renamed into place, so a
revision is either frozen whole or not at all. A revision already frozen is never written again: receiving it again
refuses unless the receipt is identical AND verify_frozen() finds every frozen file present and unchanged (a changed
delivery is a new revision). A revision above 1 must name the one before it in `supersedes`, and that revision must
already be frozen from the same library and subject, with its frozen files verified, not only its receipt read. The
receipt holds no clock time, so the same delivery always makes the same receipt.

Stage context (ctx) read by receive_stage(): `manifest` (path), `tests_root`, `out_root`, `tool_versions`
({name: version string}), `settings` (any JSON-able value).
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import sys

SCHEMA = 1
EXCHANGE = 'simulated'
FORMATS = ('asf_amc',)
SOURCE_KINDS = ('library',)
PERMITS = ('redistribute_raw', 'derivatives', 'public_display', 'commercial')
FEET = ('l', 'r')
CLIP_FILES = ('asf', 'amc')
FPS_FACT = 'fps_source'
PROVENANCES = ('verified', 'unverified')
LICENCE_STATUSES = ('checked', 'unchecked')
RECEIPT = 'receipt.json'

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_ROOT = os.path.join(REPO, 'Plugins', 'MotionTerminal', 'Tests', 'Mocap')
OUT_ROOT = os.path.join(REPO, 'Saved', 'MotionTerminalTests', 'mocap')

CMU_FAQ_URL = 'http://mocap.cs.cmu.edu/faqs.php'
CMU_FAQ_SENTENCE = 'The motion capture data may be copied, modified, or redistributed without permission.'

_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
_HEX64 = re.compile(r'^[0-9a-f]{64}$')

MANIFEST_KEYS = {'batch', 'revision', 'supersedes', 'exchange', 'source', 'intended_use', 'skeleton_map', 'clips',
                 'disclosed_defects'}
CLIP_KEYS = {'id', 'format', 'files', 'sha256', 'fps_source', 'source_frames', 'brief'}
DEFECT_KEYS = {'clip', 'kind', 'foot', 'source_frames', 'size_cm', 'seed'}
LICENCE_KEYS = {'library', 'terms_url', 'terms_excerpt', 'terms_sha256', 'attribution', 'permits', 'facts', 'status',
                'checked_by', 'checked_on'}


class ReceiveError(ValueError):
    """A refused delivery. `reason` names the refusal; `state` is 'unsupported' or 'refused'."""

    def __init__(self, reason, message, state='refused'):
        super().__init__('%s: %s' % (reason, message))
        self.reason, self.state = reason, state


# ------------------------------------------------------------------------------------------------------- small helpers
def _canonical_json(value):
    return json.dumps(value, sort_keys=True, indent=1) + '\n'


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _receiver_digest():
    return sha256_file(os.path.abspath(__file__))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float('inf'), float('-inf'))


def _need(cond, reason, message, state='refused'):
    if not cond:
        raise ReceiveError(reason, message, state)


def _ident(value, where):
    _need(isinstance(value, str) and _ID.match(value) is not None, 'manifest',
          '%s must be an id of letters, digits, _ and - (got %r)' % (where, value))


def _keys(obj, allowed, required, where, reason='manifest'):
    _need(isinstance(obj, dict), reason, '%s must be an object' % where)
    extra = sorted(set(obj) - set(allowed))
    _need(not extra, reason, '%s has keys this schema does not define: %s' % (where, extra))
    missing = sorted(set(required) - set(obj))
    _need(not missing, reason, '%s is missing %s' % (where, missing))


def check_relpath(path, where, reason='manifest'):
    """A forward-slash path relative to its root that stays inside it: no drive, no leading slash, no '..'."""
    _need(isinstance(path, str) and path != '', reason, '%s must be a non-empty path' % where)
    _need('\\' not in path and not path.startswith('/') and not re.match(r'^[A-Za-z]:', path), reason,
          '%s must be relative with forward slashes (got %r)' % (where, path))
    parts = path.split('/')
    _need(all(p not in ('', '.', '..') for p in parts), reason, '%s must not contain empty, . or .. parts (got %r)'
          % (where, path))
    return path


def _frames(value, where, reason='manifest'):
    _need(isinstance(value, list) and len(value) == 2 and all(_is_int(v) for v in value), reason,
          '%s must be [first, last] source frames, integers (got %r)' % (where, value))
    _need(1 <= value[0] <= value[1], reason, '%s must satisfy 1 <= first <= last (got %r)' % (where, value))
    return value


# ------------------------------------------------------------------------------------------------------------ manifest
def validate_manifest(m):
    """Raise ReceiveError unless `m` is a delivery manifest this slice takes; return it unchanged."""
    _keys(m, MANIFEST_KEYS, MANIFEST_KEYS, 'the manifest')
    _ident(m['batch'], 'batch')
    rev = m['revision']
    _need(_is_int(rev) and rev >= 1, 'manifest', 'revision must be an integer >= 1 (got %r)' % (rev,))
    if rev == 1:
        _need(m['supersedes'] is None, 'supersedes', 'revision 1 supersedes nothing (got %r)' % (m['supersedes'],))
    else:
        _need(m['supersedes'] == rev - 1 and _is_int(m['supersedes']), 'supersedes',
              'revision %d must supersede revision %d (got %r)' % (rev, rev - 1, m['supersedes']))
    _need(m['exchange'] == EXCHANGE, 'exchange', 'exchange must be %r in this slice (got %r)'
          % (EXCHANGE, m['exchange']), 'unsupported')

    src = m['source']
    _keys(src, {'kind', 'library', 'subject'}, {'kind', 'library', 'subject'}, 'source')
    _need(src['kind'] in SOURCE_KINDS, 'source_kind', 'source kind %r is not supported (only %s)'
          % (src['kind'], list(SOURCE_KINDS)), 'unsupported')
    _ident(src['library'], 'source.library')
    _ident(src['subject'], 'source.subject')

    use = m['intended_use']
    _need(isinstance(use, list) and use and len(set(use)) == len(use), 'manifest',
          'intended_use must be a non-empty list without repeats')
    unknown = [u for u in use if u not in PERMITS]
    _need(not unknown, 'manifest', 'intended_use names %s, which are not permits (%s)' % (unknown, list(PERMITS)))
    check_relpath(m['skeleton_map'], 'skeleton_map')

    clips = m['clips']
    _need(isinstance(clips, list) and clips, 'manifest', 'clips must be a non-empty list')
    ids, spans, seen_files = set(), {}, set()
    for i, clip in enumerate(clips):
        where = 'clips[%d]' % i
        _keys(clip, CLIP_KEYS, CLIP_KEYS, where)
        _ident(clip['id'], where + '.id')
        _need(clip['id'] not in ids, 'manifest', 'clip id %r is declared twice' % clip['id'])
        ids.add(clip['id'])
        _need(clip['format'] in FORMATS, 'format', '%s format %r is not supported (only %s)'
              % (where, clip['format'], list(FORMATS)), 'unsupported')
        _keys(clip['files'], CLIP_FILES, CLIP_FILES, where + '.files')
        _keys(clip['sha256'], CLIP_FILES, CLIP_FILES, where + '.sha256')
        for kind in CLIP_FILES:
            rel = check_relpath(clip['files'][kind], '%s.files.%s' % (where, kind))
            _need(rel not in seen_files, 'manifest', '%s is delivered twice' % rel)
            seen_files.add(rel)
            digest = clip['sha256'][kind]
            _need(isinstance(digest, str) and _HEX64.match(digest) is not None, 'manifest',
                  '%s.sha256.%s must be 64 lowercase hex digits' % (where, kind))
        fps = clip['fps_source']
        _need(_is_num(fps) and fps > 0, 'manifest', '%s.fps_source must be a positive number' % where)
        spans[clip['id']] = _frames(clip['source_frames'], where + '.source_frames')
        _need(isinstance(clip['brief'], str) and clip['brief'].strip(), 'manifest', '%s.brief must be text' % where)

    defects = m['disclosed_defects']
    _need(isinstance(defects, list), 'manifest', 'disclosed_defects must be a list (empty when none)')
    for i, d in enumerate(defects):
        where = 'disclosed_defects[%d]' % i
        _keys(d, DEFECT_KEYS, {'clip', 'kind', 'source_frames'}, where)
        _need(d['clip'] in spans, 'manifest', '%s names clip %r, which is not delivered' % (where, d['clip']))
        _need(isinstance(d['kind'], str) and d['kind'], 'manifest', '%s.kind must be text' % where)
        if 'foot' in d:
            _need(d['foot'] in FEET, 'manifest', '%s.foot must be one of %s' % (where, list(FEET)))
        a, b = _frames(d['source_frames'], where + '.source_frames')
        lo, hi = spans[d['clip']]
        _need(lo <= a and b <= hi, 'manifest', '%s frames %r fall outside the clip %r' % (where, [a, b], [lo, hi]))
        if 'size_cm' in d:
            _need(_is_num(d['size_cm']) and d['size_cm'] >= 0, 'manifest', '%s.size_cm must be >= 0' % where)
        if 'seed' in d:
            _need(_is_int(d['seed']), 'manifest', '%s.seed must be an integer' % where)
    return m


def _load_json(path, reason):
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
    except FileNotFoundError:
        raise ReceiveError(reason, '%s does not exist' % path) from None
    try:
        return json.loads(data.decode('utf-8')), data
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReceiveError(reason, '%s is not JSON: %s' % (path, exc)) from None


def load_manifest(path):
    m, _ = _load_json(path, 'manifest')
    return validate_manifest(m)


# ------------------------------------------------------------------------------------------------------------- licence
def cmu_licence_template():
    """The CMU licence record before its signer signs it: status unchecked, signer empty, terms hash unknown."""
    return {'library': 'cmu', 'terms_url': CMU_FAQ_URL, 'terms_excerpt': CMU_FAQ_SENTENCE, 'terms_sha256': None,
            'attribution': None,
            'permits': {'redistribute_raw': True, 'derivatives': True, 'public_display': None, 'commercial': None},
            'facts': {FPS_FACT: {'value': 120, 'url': CMU_FAQ_URL, 'provenance': 'unverified'}},
            'status': 'unchecked', 'checked_by': None, 'checked_on': None}


def is_signed(lic):
    return (lic.get('status') == 'checked' and isinstance(lic.get('checked_by'), str) and lic['checked_by'].strip() != ''
            and isinstance(lic.get('checked_on'), str) and lic['checked_on'] != '')


def validate_licence(lic, library=None):
    """Raise ReceiveError unless `lic` is a well-formed licence record (signed or not); return it."""
    _keys(lic, LICENCE_KEYS, LICENCE_KEYS, 'the licence record', 'licence')
    _need(isinstance(lic['library'], str) and _ID.match(lic['library']) is not None, 'licence', 'library must be an id')
    if library is not None:
        _need(lic['library'] == library, 'licence', 'the record is for %r, not %r' % (lic['library'], library))
    _need(isinstance(lic['terms_url'], str) and lic['terms_url'].startswith(('http://', 'https://')), 'licence',
          'terms_url must be the URL the terms were read from')
    _need(isinstance(lic['terms_excerpt'], str) and lic['terms_excerpt'].strip(), 'licence',
          'terms_excerpt must quote the sentence the terms rest on')
    _need(lic['terms_sha256'] is None or (isinstance(lic['terms_sha256'], str)
                                          and _HEX64.match(lic['terms_sha256']) is not None),
          'licence', 'terms_sha256 must be 64 lowercase hex digits or null')
    _need(lic['attribution'] is None or isinstance(lic['attribution'], str), 'licence', 'attribution: text or null')
    _keys(lic['permits'], PERMITS, PERMITS, 'permits', 'licence')
    for name, v in lic['permits'].items():
        _need(v in (True, False, None) and not _is_int(v), 'licence', 'permit %s must be true, false or null' % name)
    _need(isinstance(lic['facts'], dict), 'licence', 'facts must be an object')
    for name, fact in lic['facts'].items():
        _keys(fact, {'value', 'url', 'provenance'}, {'value', 'url', 'provenance'}, 'facts.%s' % name, 'licence')
        _need(isinstance(fact['url'], str) and fact['url'].startswith(('http://', 'https://')), 'licence',
              'facts.%s.url must name the page it came from' % name)
        _need(fact['provenance'] in PROVENANCES, 'licence', 'facts.%s.provenance must be one of %s'
              % (name, list(PROVENANCES)))
    if FPS_FACT in lic['facts']:
        v = lic['facts'][FPS_FACT]['value']
        _need(_is_num(v) and v > 0, 'licence', 'facts.%s.value must be a positive number' % FPS_FACT)
    _need(lic['status'] in LICENCE_STATUSES, 'licence', 'status must be one of %s' % list(LICENCE_STATUSES))
    for key in ('checked_by', 'checked_on'):
        _need(lic[key] is None or isinstance(lic[key], str), 'licence', '%s must be text or null' % key)
    if lic['checked_on']:
        try:
            datetime.date.fromisoformat(lic['checked_on'])
        except ValueError:
            raise ReceiveError('licence', 'checked_on must be YYYY-MM-DD (got %r)' % lic['checked_on']) from None
    if lic['status'] == 'checked':
        _need(lic['terms_sha256'] is not None, 'licence', 'a checked record must hold the hash of the terms as read')
    return lic


def gate(lic, intended_use):
    """Raise ReceiveError unless the record is signed and every permit `intended_use` needs is exactly true."""
    _need(is_signed(lic), 'licence_unsigned', 'the %r licence record is not signed (status %r, checked_by %r, '
          'checked_on %r): a person checks the terms and signs it before any delivery is received'
          % (lic.get('library'), lic.get('status'), lic.get('checked_by'), lic.get('checked_on')))
    lacking = {u: lic['permits'].get(u) for u in intended_use if lic['permits'].get(u) is not True}
    _need(not lacking, 'licence_permit', 'intended use needs %s, which the %r record does not permit (%s)'
          % (sorted(lacking), lic['library'], ', '.join('%s=%s' % kv for kv in sorted(lacking.items()))))


def licence_path(tests_root, library):
    return os.path.join(tests_root, 'licenses', '%s.json' % library)


def load_licence(tests_root, library):
    lic, _ = _load_json(licence_path(tests_root, library), 'licence')
    return validate_licence(lic, library)


# -------------------------------------------------------------------------------------------------------- skeleton map
def validate_skeleton_map(smap, library):
    _keys(smap, {'library', 'bones'}, {'library', 'bones'}, 'the skeleton map', 'skeleton_map')
    _need(smap['library'] == library, 'skeleton_map', 'the map is for %r, not %r' % (smap['library'], library))
    bones = smap['bones']
    _need(isinstance(bones, dict) and bones, 'skeleton_map', 'bones must map source bones to roles')
    for bone, role in bones.items():
        _need(isinstance(bone, str) and bone and isinstance(role, str) and role, 'skeleton_map',
              'every bone and role must be text (%r: %r)' % (bone, role))
    roles = list(bones.values())
    doubled = sorted({r for r in roles if roles.count(r) > 1})
    _need(not doubled, 'skeleton_map', 'roles claimed by more than one bone: %s' % doubled)
    return smap


# ------------------------------------------------------------------------------------------------------------- receive
def revision_dir(out_root, batch, revision):
    return os.path.join(out_root, batch, 'r%d' % revision)


def ledger_path(out_root, batch, revision):
    """Where stage_runner keeps this revision's record (section 4)."""
    return os.path.join(revision_dir(out_root, batch, revision), 'record.json')


def frozen_dir(out_root, batch, revision):
    return os.path.join(revision_dir(out_root, batch, revision), 'receive')


def load_receipt(out_root, batch, revision):
    """The frozen receipt of a revision, or None when that revision was never received. An unreadable receipt is
    refused (frozen_changed), never taken as absent."""
    path = os.path.join(frozen_dir(out_root, batch, revision), RECEIPT)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'rb') as fh:
            receipt = json.loads(fh.read().decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReceiveError('frozen_changed', 'the receipt of r%d of %s is not JSON: %s' % (revision, batch, exc)) \
            from None
    _need(isinstance(receipt, dict), 'frozen_changed', 'the receipt of r%d of %s is not an object' % (revision, batch))
    return receipt


def _check_versions(tool_versions):
    _need(isinstance(tool_versions, dict) and all(isinstance(k, str) and isinstance(v, str) and v
                                                  for k, v in tool_versions.items()),
          'tool_versions', 'tool_versions must be {name: version text}')


def receive(manifest_path, *, tests_root=TESTS_ROOT, out_root=OUT_ROOT, tool_versions=None, settings=None):
    """Gate, validate, hash and freeze one delivery revision; return its receipt (the stage-1 record).

    Delivered files are read relative to the manifest's folder; the licence record and the skeleton map relative to
    tests_root. Raises ReceiveError on a refusal, OSError on a file that cannot be read."""
    tool_versions = dict(tool_versions or {})
    _check_versions(tool_versions)
    try:
        json.dumps(settings, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ReceiveError('settings', 'settings must be JSON: %s' % exc) from None

    m, manifest_bytes = _load_json(manifest_path, 'manifest')
    validate_manifest(m)
    library, batch, rev = m['source']['library'], m['batch'], m['revision']

    lic_file = licence_path(tests_root, library)
    lic, lic_bytes = _load_json(lic_file, 'licence')
    validate_licence(lic, library)
    gate(lic, m['intended_use'])

    map_file = os.path.join(tests_root, *m['skeleton_map'].split('/'))
    smap, map_bytes = _load_json(map_file, 'skeleton_map')
    validate_skeleton_map(smap, library)

    prior = None
    if rev > 1:
        _need(load_receipt(out_root, batch, rev - 1) is not None, 'supersedes',
              'revision %d supersedes revision %d, which was never received' % (rev, rev - 1))
        prior = verify_frozen(out_root, batch, rev - 1)     # its frozen files, not only its receipt
        _need(prior['source'] == m['source'], 'supersedes', 'revision %d is from %r, revision %d from %r'
              % (rev, m['source'], rev - 1, prior['source']))

    base = os.path.dirname(os.path.abspath(manifest_path))
    fact = lic['facts'].get(FPS_FACT)
    prefix = '%s/r%d/receive' % (batch, rev)
    clips, copies = [], []
    for clip in m['clips']:
        if fact is not None:
            _need(clip['fps_source'] == fact['value'], 'fps_source', 'clip %r declares %r fps; the %r record states %r'
                  % (clip['id'], clip['fps_source'], library, fact['value']))
        files = {}
        for kind in CLIP_FILES:
            rel = clip['files'][kind]
            src = os.path.join(base, *rel.split('/'))
            with open(src, 'rb') as fh:     # a missing file is an infrastructure fault: OSError, not a refusal
                data = fh.read()            # read once: these exact bytes are checked and frozen
            got = sha256_bytes(data)
            _need(got == clip['sha256'][kind], 'hash_mismatch', '%s hashes to %s; the manifest says %s'
                  % (rel, got, clip['sha256'][kind]))
            files[kind] = '%s/delivery/%s' % (prefix, rel)
            copies.append(('delivery/' + rel, data, clip['sha256'][kind]))
        clips.append({'id': clip['id'], 'format': clip['format'], 'files': files, 'sha256': dict(clip['sha256']),
                      'fps_source': clip['fps_source'],
                      'fps_provenance': fact['provenance'] if fact is not None else 'unverified',
                      'fps_url': fact['url'] if fact is not None else None,
                      'source_frames': list(clip['source_frames']), 'brief': clip['brief'], 'revision': rev})

    tools = dict(tool_versions)
    tools.setdefault('python', '%d.%d.%d' % sys.version_info[:3])
    tools['mocap_receive'] = _receiver_digest()
    receipt = {
        'schema': SCHEMA, 'stage': 'receive', 'batch': batch, 'revision': rev, 'supersedes': m['supersedes'],
        'exchange': m['exchange'], 'source': dict(m['source']), 'intended_use': list(m['intended_use']),
        'manifest': {'path': '%s/manifest.json' % prefix, 'sha256': sha256_bytes(manifest_bytes)},
        'licence': {'path': '%s/licence.json' % prefix, 'sha256': sha256_bytes(lic_bytes), 'library': library,
                    'terms_url': lic['terms_url'], 'terms_excerpt': lic['terms_excerpt'],
                    'terms_sha256': lic['terms_sha256'], 'attribution': lic['attribution'],
                    'permits_used': {u: lic['permits'][u] for u in m['intended_use']},
                    'checked_by': lic['checked_by'], 'checked_on': lic['checked_on']},
        'skeleton_map': {'path': '%s/skeleton_map.json' % prefix, 'declared': m['skeleton_map'],
                         'sha256': sha256_bytes(map_bytes), 'bones': dict(smap['bones'])},
        'clips': clips, 'disclosed_defects': [dict(d) for d in m['disclosed_defects']],
        'prior_receipt_sha256': (sha256_file(os.path.join(frozen_dir(out_root, batch, rev - 1), RECEIPT))
                                 if prior is not None else None),
        'tool_versions': tools, 'settings': settings,
    }
    return _freeze(out_root, batch, rev, receipt, manifest_bytes, lic_bytes, map_bytes, copies)


def _freeze(out_root, batch, rev, receipt, manifest_bytes, lic_bytes, map_bytes, copies):
    final = frozen_dir(out_root, batch, rev)
    existing = load_receipt(out_root, batch, rev)
    if existing is not None or os.path.exists(final):
        _need(existing == receipt, 'revision_frozen', 'revision %d of %s is already frozen with other content; a '
              'changed delivery is a new revision' % (rev, batch))
        return verify_frozen(out_root, batch, rev)      # a matching receipt is not enough: its files must hold
    parent = os.path.dirname(final)
    os.makedirs(parent, exist_ok=True)
    staging = os.path.join(parent, 'receive.partial-%d' % os.getpid())
    if os.path.exists(staging):
        shutil.rmtree(staging)
    try:
        os.makedirs(staging)
        for name, data in (('manifest.json', manifest_bytes), ('licence.json', lic_bytes),
                           ('skeleton_map.json', map_bytes)):
            with open(os.path.join(staging, name), 'wb') as fh:
                fh.write(data)
        for rel, data, declared in copies:
            dst = os.path.join(staging, *rel.split('/'))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'wb') as fh:
                fh.write(data)
            _need(sha256_file(dst) == declared, 'copy', 'the frozen copy of %s does not hash to the manifest' % rel)
        with open(os.path.join(staging, RECEIPT), 'w', encoding='utf-8') as fh:
            fh.write(_canonical_json(receipt))
        os.rename(staging, final)           # fails if another run froze it first: never overwrite
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return receipt


def verify_frozen(out_root, batch, revision):
    """Re-hash a frozen revision's files against its receipt; raise ReceiveError on any difference.

    Fails closed: a receipt for another batch or revision, a path outside this revision's receive folder, a missing
    file (frozen_missing) or a malformed receipt is refused, never skipped."""
    receipt = load_receipt(out_root, batch, revision)
    _need(receipt is not None, 'not_received', 'revision %d of %s was never received' % (revision, batch))
    prefix = '%s/r%d/receive/' % (batch, revision)

    def check(rel, declared, what):
        _need(isinstance(rel, str) and rel.startswith(prefix) and isinstance(declared, str)
              and _HEX64.match(declared) is not None, 'frozen_changed',
              '%s of r%d is not a frozen path and hash under %s' % (what, revision, prefix))
        check_relpath(rel, what, 'frozen_changed')
        try:
            got = sha256_file(os.path.join(out_root, *rel.split('/')))
        except FileNotFoundError:
            raise ReceiveError('frozen_missing', '%s of r%d is gone from the frozen revision' % (rel, revision)) \
                from None
        _need(got == declared, 'frozen_changed', '%s of r%d changed after it was frozen' % (rel, revision))

    try:
        _need(receipt.get('batch') == batch and receipt.get('revision') == revision, 'frozen_changed',
              'the receipt in r%d of %s names batch %r revision %r'
              % (revision, batch, receipt.get('batch'), receipt.get('revision')))
        for part in ('manifest', 'licence', 'skeleton_map'):
            check(receipt[part]['path'], receipt[part]['sha256'], part)
        clips = receipt['clips']
        _need(isinstance(clips, list) and clips, 'frozen_changed', 'the receipt of r%d lists no clips' % revision)
        for clip in clips:
            _need(set(clip['files']) == set(CLIP_FILES) == set(clip['sha256']), 'frozen_changed',
                  'clip %r of r%d does not list every frozen file' % (clip.get('id'), revision))
            for kind in CLIP_FILES:
                check(clip['files'][kind], clip['sha256'][kind], '%s.%s' % (clip.get('id'), kind))
    except (KeyError, TypeError, AttributeError) as exc:
        raise ReceiveError('frozen_changed', 'the receipt of r%d of %s is malformed: %r' % (revision, batch, exc)) \
            from None
    return receipt


# --------------------------------------------------------------------------------------------------------------- stage
def stage_inputs(ctx):
    """What decides stage 1's result: the bytes of the manifest, licence record, skeleton map and delivered files,
    the tool versions, the settings and this module. Any change to them changes the key."""
    m, mbytes = _load_json(ctx['manifest'], 'manifest')
    validate_manifest(m)
    tests_root = ctx.get('tests_root', TESTS_ROOT)
    base = os.path.dirname(os.path.abspath(ctx['manifest']))
    _, lic_bytes = _load_json(licence_path(tests_root, m['source']['library']), 'licence')
    _, map_bytes = _load_json(os.path.join(tests_root, *m['skeleton_map'].split('/')), 'skeleton_map')
    return {'manifest': sha256_bytes(mbytes), 'licence': sha256_bytes(lic_bytes), 'skeleton_map': sha256_bytes(map_bytes),
            'files': {rel: sha256_file(os.path.join(base, *rel.split('/')))
                      for c in m['clips'] for rel in c['files'].values()},
            'tool_versions': ctx.get('tool_versions') or {}, 'settings': ctx.get('settings'),
            'receiver': _receiver_digest()}


def receive_stage():
    import stage_runner

    def run(ctx, records):
        return receive(ctx['manifest'], tests_root=ctx.get('tests_root', TESTS_ROOT),
                       out_root=ctx.get('out_root', OUT_ROOT), tool_versions=ctx.get('tool_versions'),
                       settings=ctx.get('settings'))
    return stage_runner.Stage('receive', run, inputs=stage_inputs)
