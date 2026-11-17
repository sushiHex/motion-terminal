"""The host side of the mocap intake's 'evaluate' stage: sampled takes plus hand labels in, an evaluation record out.

    take   = attach_landmarks(take, body_landmarks)        # optional: heel/ball from bone-local offsets
    rows   = measure_take(take, labels, bands)             # one subject (the source or a body), no fault owners yet
    record = evaluate_clip(source, bodies, labels, bands, required_bodies=[...], batch=..., revision=...,
                           invocation_id=..., reviews=..., waivers=...)

Design: docs/specs/2026-11-16-mocap-intake-design.md, sections 5 (the sample, labels, landmarks, evaluation row) and
8 (decision states, measurements, fault ownership, animator review). Host Python, stdlib only.

The checker shares no code with the writer: it imports nothing from the reader/converter or any cleanup module. Its
units and space are restated here, from the spec, not imported: centimetres, Unreal component space (= world, the
mesh component at identity), up +Z, a flat ground plane z = ground.z_cm.

What it decides and what it refuses:
  * A rejection, an unmeasurable row or an unsupported take is a SUCCESSFUL record. evaluate_clip raises
    EvaluationError only for malformed inputs (a schema violation: a missing key, a band that is not an interval,
    a review without a reason, a waiver without an approver). Missing or bad DATA is never an exception and never
    zero error: a window with coverage below 1.0 is `unmeasurable` with `measured: null`.
  * Bands are explicit inputs. There is no default band anywhere in this module; a missing band key raises. Every
    row carries the bands' digest.
  * Contacts are measured on the labelled LANDMARK's surface clearance and horizontal position, never on a bone.
    The foot bone's height is reported in the evidence as a diagnostic only.
  * Every labelled contact window yields rows (a `both` window yields one per landmark), so the expected contacts
    are covered in full: a window the sampler never reached reads coverage 0.0, `unmeasurable`. Labels with no
    contact window at all give a required `labels` row, `unmeasurable`: nothing is accepted on no contacts.
  * A landmark's clearance must agree with its position: |clearance - (z - ground.z_cm)| within the bands input's
    tolerances.clearance_cm, or the sample is invalid ('clearance_disagrees').
  * Every passed infrastructure check and every passed animator review cites evidence: a non-empty list of
    {'path', 'sha256'} whose path lies under '<batch>/r<revision>/' (every artifact path carries its revision). A
    check given as a bare boolean, or passed without evidence, is refused (EvaluationError).

Time mapping. A take declares fps (its sample rate), fps_source, source_frame_first and source_frame_last. Sample k
maps to source frame first + k * fps_source / fps and to t_s = k / fps. When source_frame_last is off that grid, the
take ends on one more sample at source_frame_last, flagged `end_sample: true` (the converter keeps the last source
sample when the duration is not a multiple of the output step; Scripts/acclaim.py resample). That end sample is part
of the time contract: a window [a, b] in source frames expects every grid sample whose source frame lies in [a, b],
plus the end sample when source_frame_last lies in [a, b], whether or not the take reached them. A sample whose t_s
disagrees with its source_frame, an off-grid source frame that is not the flagged end sample, a flagged sample that
is not the declared off-grid end, a duplicated source frame, or a non-finite landmark is invalid, not used.

Decision order (the spec says "worst row" without ranking): unsupported > rejected > unmeasurable > waived >
accepted. A known out-of-band row rejects even if another row is unmeasurable, so a vendor brief can be written.
The animator review is a required row: pass -> accepted, fail -> rejected (whatever the metrics say), missing ->
unmeasurable (a required row could not be made), so nothing is `accepted` without a person having looked. A failed
review owned to the source must name the clip and source frames it is about.

Required bodies are an explicit input: a required body missing from `bodies` is not sampled (`unmeasurable`), and a
body that is not required is refused. Sign-off on a clip needs the source and every required body accepted or
waived and every review passed.

Fault ownership (section 8). A source row out of band is `source` only when the conversion evidence (the source's
viewer_pinned check) passed; a failed one makes it `conversion`, a missing one `uncertain`. A body row the sampler
could not cover (missing target samples, parity) is `runtime`, never `source`. A body row out of band where the
source also fails stays the body's own (not `source`) when that body's boundary check did not pass, or when its
distance outside the band exceeds the source's by more than tolerances.source_excess_cm, or when it fails the
other bound (a penetration under a source float is a different defect).
"""
import hashlib
import json
import math
import re

SCHEMA = 1
UNITS = 'cm'
UP_AXIS = 2                       # +Z; horizontal is the X-Y plane
FEET = ('l', 'r')
LANDMARKS = ('heel', 'ball')
LABEL_LANDMARKS = ('heel', 'ball', 'both')
ROLES = ('source', 'body')
EPS = 1e-6

ACCEPTED, REJECTED, UNMEASURABLE, UNSUPPORTED, WAIVED = 'accepted', 'rejected', 'unmeasurable', 'unsupported', 'waived'
REPORTED = 'reported'             # a row that is shown, never banded (airborne windows); not part of a decision
STATES = (ACCEPTED, REJECTED, UNMEASURABLE, UNSUPPORTED, WAIVED)
SEVERITY = {ACCEPTED: 0, WAIVED: 1, UNMEASURABLE: 2, REJECTED: 3, UNSUPPORTED: 4}
FAULT_OWNERS = ('source', 'conversion', 'retarget', 'body_limitation', 'runtime', 'uncertain')

# band keys every evaluation needs; values come only from the bands input
BAND_KEYS = ('slide_cm', 'ground_min_cm', 'ground_max_cm', 'sampler_parity_cm')
# scalar tolerances (bands['tolerances']), also explicit inputs with no default
TOLERANCE_KEYS = ('clearance_cm', 'source_excess_cm')
CONTACT_MEASUREMENTS = ('slide', 'ground_min', 'ground_max')
COVERAGE_CAUSES = ('coverage', 'empty_window', 'sampler_parity')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')

# infrastructure checks each role must report (design, section 6 and 8); a missing or failed one is unmeasurable.
# This is a list of NAMES the spec requires, not a threshold.
REQUIRED_CHECKS = {
    'source': ('viewer_pinned',),
    'body': ('boundary', 'static_bake', 'target_pose'),
}
CHECK_OWNER = {'viewer_pinned': 'conversion', 'boundary': 'conversion', 'static_bake': 'retarget',
               'target_pose': 'retarget'}


class EvaluationError(Exception):
    """A malformed input: the evaluation cannot be stated at all. Never raised for a rejection or missing data."""


# ---------------------------------------------------------------------------------------------- small helpers

def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _vec3(v):
    return isinstance(v, (list, tuple)) and len(v) == 3 and all(_finite(x) for x in v)


def digest(obj):
    """sha256 of the sorted-key JSON of obj: the spec's JSON, sorted keys."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _need(d, key, where):
    if not isinstance(d, dict) or key not in d:
        raise EvaluationError('%s: missing %r' % (where, key))
    return d[key]


def _quat_rotate(q, v):
    w, x, y, z = q
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if not n > 0:
        raise ValueError('zero quaternion')
    w, x, y, z = w / n, x / n, y / n, z / n
    # v' = v + 2w (u x v) + 2 u x (u x v), u = (x, y, z)
    cx, cy, cz = y * v[2] - z * v[1], z * v[0] - x * v[2], x * v[1] - y * v[0]
    ccx, ccy, ccz = y * cz - z * cy, z * cx - x * cz, x * cy - y * cx
    return [v[0] + 2 * (w * cx + ccx), v[1] + 2 * (w * cy + ccy), v[2] + 2 * (w * cz + ccz)]


def _hdist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------------------------- inputs

def check_bands(bands):
    """Validate the bands input and return its digest. Raises on any missing or malformed band: no defaults."""
    _need(bands, 'status', 'bands')
    table = _need(bands, 'bands', 'bands')
    for key in BAND_KEYS:
        band = _need(table, key, 'bands.bands')
        if not (isinstance(band, (list, tuple)) and len(band) == 2 and all(_finite(x) for x in band)
                and band[0] <= band[1]):
            raise EvaluationError('bands.bands.%s: not a finite [lo, hi] interval: %r' % (key, band))
    tol = _need(bands, 'tolerances', 'bands')
    for key in TOLERANCE_KEYS:
        v = _need(tol, key, 'bands.tolerances')
        if not (_finite(v) and v >= 0):
            raise EvaluationError('bands.tolerances.%s: not a finite number >= 0: %r' % (key, v))
    return digest(bands)


def check_evidence(refs, where, batch=None, revision=None):
    """Validate evidence references: a non-empty list of {'path', 'sha256'}, each path relative, forward-slashed,
    without '..', and (when batch and revision are given) under '<batch>/r<revision>/'. Returns a copy."""
    if not (isinstance(refs, list) and refs):
        raise EvaluationError('%s: evidence must be a non-empty list of {path, sha256}' % where)
    out = []
    for i, ref in enumerate(refs):
        w = '%s[%d]' % (where, i)
        if not (isinstance(ref, dict) and set(ref) == {'path', 'sha256'}):
            raise EvaluationError('%s: evidence is {path, sha256} (got %r)' % (w, ref))
        path, sha = ref['path'], ref['sha256']
        if not (isinstance(path, str) and path and '\\' not in path and not path.startswith('/') and ':' not in path
                and '..' not in path.split('/')):
            raise EvaluationError('%s.path %r: a relative, forward-slashed path' % (w, path))
        if batch is not None and not path.startswith('%s/r%d/' % (batch, revision)):
            raise EvaluationError('%s.path %r is not under %s/r%d/: evidence is revision-scoped'
                                  % (w, path, batch, revision))
        if not (isinstance(sha, str) and _SHA256.match(sha)):
            raise EvaluationError('%s.sha256 %r: 64 lowercase hex digits' % (w, sha))
        out.append({'path': path, 'sha256': sha})
    return out


def check_labels(labels):
    contacts = _need(labels, 'contacts', 'labels')
    if not isinstance(contacts, list):
        raise EvaluationError('labels.contacts: not a list')
    for i, c in enumerate(contacts):
        where = 'labels.contacts[%d]' % i
        if _need(c, 'foot', where) not in FEET:
            raise EvaluationError('%s: foot %r' % (where, c['foot']))
        if _need(c, 'landmark', where) not in LABEL_LANDMARKS:
            raise EvaluationError('%s: landmark %r' % (where, c['landmark']))
        _window(_need(c, 'source_frames', where), where)
    for i, w in enumerate(labels.get('airborne', [])):
        _window(_need(w, 'source_frames', 'labels.airborne[%d]' % i), 'labels.airborne[%d]' % i)


def _window(w, where):
    if not (isinstance(w, (list, tuple)) and len(w) == 2 and all(_finite(x) for x in w) and w[0] <= w[1]):
        raise EvaluationError('%s: source_frames is not [a, b] with a <= b: %r' % (where, w))
    return float(w[0]), float(w[1])


def _check_take(take, where):
    for key in ('fps', 'fps_source', 'source_frame_first', 'source_frame_last'):
        if not _finite(_need(take, key, where)):
            raise EvaluationError('%s.%s: not a finite number' % (where, key))
    if take['fps'] <= 0 or take['fps_source'] <= 0:
        raise EvaluationError('%s: fps and fps_source must be positive' % where)
    if take['source_frame_last'] < take['source_frame_first']:
        raise EvaluationError('%s: source_frame_last is before source_frame_first' % where)
    _need(_need(take, 'ground', where), 'kind', where + '.ground')
    if not isinstance(_need(take, 'samples', where), list):
        raise EvaluationError('%s.samples: not a list' % where)


def attach_landmarks(take, body_landmarks):
    """Return a copy of take whose samples carry heel/ball positions and surface clearance, computed from the pose of
    the bone each landmark is skinned to and the body's bone-local landmark offsets (design, section 5, 'Body
    landmarks': the ball is the lowest vertex dominantly skinned to the BALL bone, so it moves with the toes).

    body_landmarks: {'mesh_sha256': ..., 'l': {'foot_bone': 'foot_l', 'ball_bone': 'ball_l' (optional),
    'heel': [x, y, z] (foot-bone-local), 'ball': [x, y, z] (ball-bone-local, or foot-bone-local with no
    ball_bone)}, 'r': ...}. A missing or non-finite bone leaves the landmark INVALID with its cause, never a guessed
    position."""
    ground_z = _need(take['ground'], 'z_cm', 'take.ground') if take['ground'].get('kind') == 'flat' else 0.0
    out = dict(take)
    samples = []
    for s in take['samples']:
        s = dict(s)
        bones = s.get('bones', {})
        lms = {}
        for foot in FEET:
            spec = _need(body_landmarks, foot, 'body_landmarks')
            foot_bone = _need(spec, 'foot_bone', 'body_landmarks.%s' % foot)
            lms[foot] = {}
            for lm in LANDMARKS:
                bone = (spec.get('ball_bone') or foot_bone) if lm == 'ball' else foot_bone
                pose = bones.get(bone)
                local = _need(spec, lm, 'body_landmarks.%s' % foot)
                if not pose or not _vec3(pose.get('t')) or not (
                        isinstance(pose.get('q'), (list, tuple)) and len(pose['q']) == 4
                        and all(_finite(x) for x in pose['q'])):
                    lms[foot][lm] = {'invalid': 'missing_bone:%s' % bone}
                    continue
                r = _quat_rotate(pose['q'], local)
                p = [pose['t'][i] + r[i] for i in range(3)]
                lms[foot][lm] = {'position': p, 'clearance_cm': p[UP_AXIS] - ground_z}
        s['landmarks'] = lms
        samples.append(s)
    out['samples'] = samples
    out['landmark_source'] = {'mesh_sha256': body_landmarks.get('mesh_sha256'), 'digest': digest(body_landmarks)}
    return out


# ---------------------------------------------------------------------------------------------- time mapping

END = 'end'                       # the key of the flagged off-grid end sample in a grid's index


def _on_grid(sf, first, step):
    k = round((sf - first) / step)
    return k if abs(first + k * step - sf) <= EPS * max(1.0, step) else None


def _grid(take):
    """Index valid samples by grid step k, and the flagged off-grid end sample by END.
    Returns {'step', 'first', 'last', 'end_off_grid', 'by_k', 'invalid'}."""
    fps, fps_src, first = float(take['fps']), float(take['fps_source']), float(take['source_frame_first'])
    last = float(take['source_frame_last'])
    step = fps_src / fps
    end_off_grid = _on_grid(last, first, step) is None
    by_k, invalid, dup = {}, [], set()
    for i, s in enumerate(take['samples']):
        sf, t = s.get('source_frame'), s.get('t_s')
        if not (_finite(sf) and _finite(t)):
            invalid.append({'sample': i, 'cause': 'non_finite_time'})
            continue
        flag = s.get('end_sample', False)
        if flag not in (True, False):
            invalid.append({'sample': i, 'cause': 'end_sample_not_bool', 'source_frame': sf})
            continue
        k = _on_grid(sf, first, step)
        if flag:
            if not end_off_grid or abs(sf - last) > EPS:
                invalid.append({'sample': i, 'cause': 'end_sample_not_the_declared_off_grid_end',
                                'source_frame': sf})
                continue
            k = END
        elif k is None:
            invalid.append({'sample': i, 'cause': 'off_grid', 'source_frame': sf})
            continue
        if abs(t - (sf - first) / fps_src) > EPS:
            invalid.append({'sample': i, 'cause': 'time_mapping', 'source_frame': sf, 't_s': t})
            continue
        if k in by_k:
            dup.add(k)
        by_k[k] = s
    for k in sorted(dup, key=str):
        invalid.append({'k': k, 'cause': 'duplicate_source_frame', 'source_frame': _key_frame(
            {'first': first, 'step': step, 'last': last}, k)})
        del by_k[k]
    return {'step': step, 'first': first, 'last': last, 'end_off_grid': end_off_grid, 'by_k': by_k,
            'invalid': invalid}


def _key_frame(grid, k):
    return grid['last'] if k == END else grid['first'] + k * grid['step']


def _expected_ks(window, grid):
    a, b = window
    step, first = grid['step'], grid['first']
    lo = math.ceil((a - first) / step - EPS)
    hi = math.floor((b - first) / step + EPS)
    ks = list(range(lo, hi + 1))
    if grid['end_off_grid'] and a - EPS <= grid['last'] <= b + EPS:
        ks.append(END)
    return ks


def _landmark_at(sample, foot, lm, ground_z, clearance_tol):
    entry = (sample.get('landmarks') or {}).get(foot, {}).get(lm)
    if not isinstance(entry, dict):
        return None, 'missing_landmark'
    if 'invalid' in entry:
        return None, entry['invalid']
    if not _vec3(entry.get('position')):
        return None, 'non_finite_position'
    if not _finite(entry.get('clearance_cm')):
        return None, 'non_finite_clearance'
    if abs(entry['clearance_cm'] - (entry['position'][UP_AXIS] - ground_z)) > clearance_tol + 1e-9:
        return None, 'clearance_disagrees'
    return entry, None


def _window_series(take, grid, window, foot, lm, clearance_tol):
    by_k = grid['by_k']
    ks = _expected_ks(window, grid)
    ground_z = float(take['ground']['z_cm'])
    series, missing = [], []
    for k in ks:
        sf = _key_frame(grid, k)
        s = by_k.get(k)
        if s is None:
            missing.append({'source_frame': sf, 'cause': 'no_sample'})
            continue
        entry, cause = _landmark_at(s, foot, lm, ground_z, clearance_tol)
        if cause:
            missing.append({'source_frame': sf, 'cause': cause})
            continue
        series.append((sf, entry, s))
    coverage = (len(series) / len(ks)) if ks else 0.0
    return ks, series, missing, coverage


# ---------------------------------------------------------------------------------------------- measurement

def _in_band(value, band):
    return band[0] - 1e-9 <= value <= band[1] + 1e-9


def _row(subject, clip, measurement, foot, landmark, window, coverage, measured, band, band_digest, state,
         evidence, required=True):
    return {'id': '%s:%s:%s:%s:%s' % (subject, measurement, foot, landmark,
                                      '-' .join('%g' % x for x in window) if window else '-'),
            'measurement': measurement, 'body': subject, 'clip': clip, 'foot': foot, 'landmark': landmark,
            'window_source_frames': list(window) if window else None, 'coverage': coverage, 'measured': measured,
            'band': list(band) if band is not None else None, 'band_digest': band_digest, 'state': state,
            'fault_owner': None, 'required': required, 'evidence': evidence}


def _foot_bone_heights(series, take, foot):
    bone = (take.get('foot_bones') or {}).get(foot)
    if not bone:
        return None
    zs = [s['bones'][bone]['t'][UP_AXIS] for _, _, s in series
          if _vec3(((s.get('bones') or {}).get(bone) or {}).get('t'))]
    return {'bone': bone, 'min_cm': min(zs), 'max_cm': max(zs)} if zs else None


def _contact_rows(subject, clip, take, grid, window, foot, lm, bands, bdig, clearance_tol):
    ks, series, missing, coverage = _window_series(take, grid, window, foot, lm, clearance_tol)
    fps_src = float(take['fps_source'])
    base_ev = {'expected_samples': len(ks), 'valid_samples': len(series)}
    if missing:
        base_ev['missing'] = missing[:20]
        base_ev['missing_count'] = len(missing)
    if coverage < 1.0 or not series:
        cause = 'coverage' if ks else 'empty_window'
        ev = dict(base_ev, cause=cause)
        return [_row(subject, clip, m, foot, lm, window, coverage, None, bands[k], bdig, UNMEASURABLE, dict(ev))
                for m, k in (('slide', 'slide_cm'), ('ground_min', 'ground_min_cm'),
                             ('ground_max', 'ground_max_cm'))]
    p0 = series[0][1]['position']
    disp = [_hdist(e['position'], p0) for _, e, _ in series]
    path, peak = 0.0, 0.0
    for (f1, e1, _), (f2, e2, _) in zip(series, series[1:]):
        d = _hdist(e1['position'], e2['position'])
        path += d
        peak = max(peak, d / ((f2 - f1) / fps_src))
    clear = [e['clearance_cm'] for _, e, _ in series]
    duration = (series[-1][0] - series[0][0]) / fps_src
    slide_ev = dict(base_ev, path_length_cm=path, duration_s=duration, peak_speed_cm_s=peak,
                    first_source_frame=series[0][0], worst_source_frame=series[disp.index(max(disp))][0])
    heights = _foot_bone_heights(series, take, foot)
    ground_ev = dict(base_ev, bone_height_diagnostic=heights,
                     min_at_source_frame=series[clear.index(min(clear))][0],
                     max_at_source_frame=series[clear.index(max(clear))][0])
    rows = []
    for m, k, value, ev in (('slide', 'slide_cm', max(disp), slide_ev),
                            ('ground_min', 'ground_min_cm', min(clear), ground_ev),
                            ('ground_max', 'ground_max_cm', max(clear), ground_ev)):
        if m == 'slide' and len(series) < 2:
            # one sample cannot show a drift: a zero here would be missing data reading as no error
            rows.append(_row(subject, clip, m, foot, lm, window, coverage, None, bands[k], bdig, UNMEASURABLE,
                             dict(ev, cause='single_sample')))
            continue
        state = ACCEPTED if _in_band(value, bands[k]) else REJECTED
        rows.append(_row(subject, clip, m, foot, lm, window, coverage, value, bands[k], bdig, state, dict(ev)))
    return rows


def _airborne_rows(subject, clip, take, grid, window, bdig, clearance_tol):
    rows = []
    for foot in FEET:
        for lm in LANDMARKS:
            ks, series, missing, coverage = _window_series(take, grid, window, foot, lm, clearance_tol)
            measured = min(e['clearance_cm'] for _, e, _ in series) if series else None
            rows.append(_row(subject, clip, 'airborne_min_clearance', foot, lm, window, coverage, measured, None,
                             bdig, REPORTED, {'expected_samples': len(ks), 'valid_samples': len(series)},
                             required=False))
    return rows


def _parity_row(subject, clip, take, bands, bdig):
    pairs = take.get('parity')
    band = bands['sampler_parity_cm']
    if not pairs:
        return _row(subject, clip, 'sampler_parity', None, None, None, 0.0, None, band, bdig, UNMEASURABLE,
                    {'cause': 'parity_missing'})
    deltas = []
    for p in pairs:
        if not (_vec3(p.get('sampled')) and _vec3(p.get('posed'))):
            return _row(subject, clip, 'sampler_parity', None, None, None, 0.0, None, band, bdig, UNMEASURABLE,
                        {'cause': 'parity_non_finite', 'pair': p})
        deltas.append((math.dist(p['sampled'], p['posed']), p))
    worst, wp = max(deltas, key=lambda d: d[0])
    state = ACCEPTED if _in_band(worst, band) else UNMEASURABLE
    ev = {'pairs': len(deltas), 'worst': {k: wp.get(k) for k in ('source_frame', 'foot', 'landmark')}}
    if state != ACCEPTED:
        ev['cause'] = 'parity_disagreement'
    return _row(subject, clip, 'sampler_parity', None, None, None, 1.0, worst, band, bdig, state, ev)


def check_checks(take, role, where, batch=None, revision=None):
    """Validate a take's infrastructure checks: each present check is {'passed': bool, 'evidence': [...]}; a passed
    one cites evidence (check_evidence). A bare boolean, or a pass without evidence, raises. Returns
    {name: {'passed', 'evidence'}} for the names present."""
    checks = take.get('checks')
    if checks is None:
        return {}
    if not isinstance(checks, dict):
        raise EvaluationError('%s.checks: not an object' % where)
    out = {}
    for name, c in checks.items():
        w = '%s.checks.%s' % (where, name)
        if not isinstance(c, dict):
            raise EvaluationError('%s: a check is {passed, evidence}, not a bare %r' % (w, c))
        passed = _need(c, 'passed', w)
        if passed not in (True, False):
            raise EvaluationError('%s.passed: true or false (got %r)' % (w, passed))
        if passed:
            refs = check_evidence(c.get('evidence'), w + '.evidence', batch, revision)
        else:
            refs = check_evidence(c['evidence'], w + '.evidence', batch, revision) if c.get('evidence') else None
        out[name] = {'passed': passed, 'evidence': refs}
    return out


def _check_rows(subject, clip, checks, role, bdig):
    rows = []
    for name in REQUIRED_CHECKS[role]:
        c = checks.get(name)
        passed = c['passed'] if c is not None else None
        state = ACCEPTED if passed is True else UNMEASURABLE
        ev = {'check': name, 'evidence': c['evidence'] if c is not None else None}
        if state != ACCEPTED:
            ev['cause'] = 'check_failed' if passed is False else 'check_missing'
        rows.append(_row(subject, clip, 'check:' + name, None, None, None, 1.0 if passed is not None else 0.0,
                         passed, None, bdig, state, ev))
    return rows


def _span_row(subject, clip, grid, declared, bdig):
    """Does the take cover the declared source range [a, b] (the receive receipt's, for this clip)? Every grid
    sample in [a, b], plus the flagged end sample when it lies there, must be a valid sample; a take cut short is
    `unmeasurable` (cause 'coverage'), never accepted on the windows it happens to reach."""
    ks = _expected_ks(declared, grid)
    present = [k for k in ks if k in grid['by_k']]
    absent = [_key_frame(grid, k) for k in ks if k not in grid['by_k']]
    coverage = len(present) / len(ks) if ks else 0.0
    covered = bool(ks) and coverage == 1.0
    ev = {'declared_source_frames': list(declared), 'take_source_frames': [grid['first'], grid['last']],
          'expected_samples': len(ks), 'valid_samples': len(present)}
    if not covered:
        ev.update(cause='coverage', missing_count=len(absent), missing=absent[:20])
    return _row(subject, clip, 'span', None, None, None, coverage, True if covered else None, None, bdig,
                ACCEPTED if covered else UNMEASURABLE, ev)


def measure_take(take, labels, bands, subject=None, role=None, batch=None, revision=None, declared=None):
    """Rows for one subject: the canonical source (role 'source') or one body (role 'body'). No fault owners.
    batch and revision, when given, scope every check's evidence path (evaluate_clip always gives them). declared,
    when given (evaluate_clip always gives it), is the clip's [first, last] source frames from the receive receipt:
    a required `span` row says whether the take covers it.

    take: {'body', 'clip', 'role', 'fps', 'fps_source', 'source_frame_first', 'source_frame_last',
           'ground': {'kind': 'flat', 'z_cm'},
           'samples': [{'t_s', 'source_frame', 'end_sample' (optional bool), 'target_frame',
                        'bones': {name: {'t', 'q'}},
                        'landmarks': {'l': {'heel': {'position', 'clearance_cm'}, 'ball': ...}, 'r': ...}}],
           'parity': [{'source_frame', 'foot', 'landmark', 'sampled', 'posed'}]   (bodies),
           'checks': {name: {'passed': bool, 'evidence': [{'path', 'sha256'}]}},
           'foot_bones': {'l': ..., 'r': ...} (diagnostic)}"""
    bdig = check_bands(bands)
    check_labels(labels)
    subject = subject or _need(take, 'body', 'take')
    role = role or _need(take, 'role', 'take')
    if role not in ROLES:
        raise EvaluationError('take.role %r' % role)
    _check_take(take, 'take[%s]' % subject)
    checks = check_checks(take, role, 'take[%s]' % subject, batch, revision)
    clip = take.get('clip')
    table = bands['bands']
    clearance_tol = float(bands['tolerances']['clearance_cm'])
    ground = take['ground']
    if ground['kind'] != 'flat':
        return [_row(subject, clip, 'scope', None, None, None, 0.0, None, None, bdig, UNSUPPORTED,
                     {'cause': 'ground', 'ground': ground})]
    if not _finite(_need(ground, 'z_cm', 'take.ground')):
        raise EvaluationError('take.ground.z_cm: not finite')
    grid = _grid(take)
    rows = []
    if declared is not None:
        rows.append(_span_row(subject, clip, grid, declared, bdig))
    if not labels['contacts']:
        rows.append(_row(subject, clip, 'labels', None, None, None, 0.0, None, None, bdig, UNMEASURABLE,
                         {'cause': 'no_contacts'}))
    for c in labels['contacts']:
        window = _window(c['source_frames'], 'labels')
        lms = LANDMARKS if c['landmark'] == 'both' else (c['landmark'],)
        for lm in lms:
            rows.extend(_contact_rows(subject, clip, take, grid, window, c['foot'], lm, table, bdig, clearance_tol))
    for w in labels.get('airborne', []):
        rows.extend(_airborne_rows(subject, clip, take, grid, _window(w['source_frames'], 'labels'), bdig,
                                   clearance_tol))
    if role == 'body':
        parity = _parity_row(subject, clip, take, table, bdig)
        rows.append(parity)
        if parity['state'] != ACCEPTED:     # one body's sampler never chooses for another: only this body's rows
            for r in rows:
                if r['required'] and r['measurement'] in CONTACT_MEASUREMENTS:
                    if r['state'] in (ACCEPTED, REJECTED):
                        r['evidence']['unmeasured_state'] = r['state']
                        r['evidence']['unmeasured_value'] = r['measured']
                    r['state'] = UNMEASURABLE
                    r['measured'] = None
                    r['evidence']['cause'] = 'sampler_parity'
    rows.extend(_check_rows(subject, clip, checks, role, bdig))
    if grid['invalid']:
        for r in rows:
            r['evidence'].setdefault('invalid_samples', len(grid['invalid']))
    return rows


# ---------------------------------------------------------------------------------------------- ownership

def _metric_key(r):
    return (r['measurement'], r['foot'], r['landmark'], tuple(r['window_source_frames'] or ()))


def _excess(r):
    """How far a measured row lies outside its band, in cm (0 inside)."""
    lo, hi = r['band']
    return max(lo - r['measured'], r['measured'] - hi, 0.0)


def _side(r):
    """Which bound a measured row fails: -1 below lo, +1 above hi, 0 inside."""
    lo, hi = r['band']
    return -1 if r['measured'] < lo else (1 if r['measured'] > hi else 0)


def _passed(checks, name):
    return (checks.get(name) or {}).get('passed')


def _body_own_owner(r, checks, failing, all_bodies):
    """Who answers for a body row out of band that is not the source's: boundary or target-pose checks decide."""
    if len(all_bodies) > 1 and failing.get(_metric_key(r), set()) == all_bodies:
        r['evidence']['ownership'] = 'fails on every body; boundary checks decide'
        return {True: 'retarget', False: 'conversion'}.get(_passed(checks, 'boundary'), 'uncertain')
    r['evidence']['ownership'] = ('fails on this body only; target-pose check decides (hypothesis, not measured '
                                  'cause)')
    return {True: 'body_limitation', False: 'retarget'}.get(_passed(checks, 'target_pose'), 'uncertain')


def _assign_owners(source_rows, source_checks, body_rows, body_checks, source_excess_cm):
    """Tag every failing or unmeasurable row (design, section 8, 'Fault ownership')."""
    src = {_metric_key(r): r for r in source_rows}
    # a source defect is the source's only when the conversion it was measured through is pinned to the viewer
    viewer = _passed(source_checks, 'viewer_pinned')
    source_owner = {True: 'source', False: 'conversion'}.get(viewer, 'uncertain')
    for r in source_rows:
        if r['state'] in (REJECTED, WAIVED):
            r['fault_owner'] = source_owner
            if source_owner != 'source':
                r['evidence']['ownership'] = 'out of band on the source, but viewer_pinned did not pass'
        elif r['state'] == UNMEASURABLE:
            name = r['measurement'][6:] if r['measurement'].startswith('check:') else None
            r['fault_owner'] = CHECK_OWNER.get(name, 'uncertain')
    failing = {}
    for body, rows in body_rows.items():
        for r in rows:
            if r['state'] in (REJECTED, WAIVED):
                failing.setdefault(_metric_key(r), set()).add(body)
    all_bodies = set(body_rows)
    for body, rows in body_rows.items():
        checks = body_checks.get(body) or {}
        for r in rows:
            if r['state'] not in (REJECTED, WAIVED, UNMEASURABLE):
                continue
            m = r['measurement']
            if m.startswith('check:'):
                r['fault_owner'] = CHECK_OWNER.get(m[6:], 'uncertain')
                continue
            if m == 'sampler_parity':
                r['fault_owner'] = 'runtime'
                continue
            s = src.get(_metric_key(r))
            if r['state'] == UNMEASURABLE:
                # missing target samples are the sampler's, whatever the source did
                r['fault_owner'] = 'runtime' if r['evidence'].get('cause') in COVERAGE_CAUSES else 'uncertain'
                continue
            # out of band on this body
            if s is None or s['state'] not in (ACCEPTED, REJECTED, WAIVED):
                r['fault_owner'] = 'uncertain'
                r['evidence']['ownership'] = 'source row not measured'
            elif s['state'] in (REJECTED, WAIVED):
                if s['fault_owner'] != 'source':
                    r['fault_owner'] = s['fault_owner']
                    r['evidence']['ownership'] = 'source row fails, but its conversion evidence did not pass'
                elif _passed(checks, 'boundary') is not True:
                    r['fault_owner'] = 'conversion' if _passed(checks, 'boundary') is False else 'uncertain'
                    r['evidence']['ownership'] = ('source row fails, but this body\'s boundary check did not pass: '
                                                  'the body keeps its own issue')
                elif _side(r) != _side(s):
                    r['fault_owner'] = _body_own_owner(r, checks, failing, all_bodies)
                    r['evidence']['ownership'] = ('fails the other bound from the source (a different defect): '
                                                  'the body keeps its own issue; %s' % r['evidence']['ownership'])
                elif _excess(r) - _excess(s) > source_excess_cm + 1e-9:
                    r['fault_owner'] = _body_own_owner(r, checks, failing, all_bodies)
                    r['evidence']['ownership'] = ('outside the band by %g cm, the source by %g cm (more than %g '
                                                  'cm apart): the body keeps its own issue; %s'
                                                  % (_excess(r), _excess(s), source_excess_cm,
                                                     r['evidence']['ownership']))
                else:
                    r['fault_owner'] = 'source'
            else:
                r['fault_owner'] = _body_own_owner(r, checks, failing, all_bodies)
                r['evidence']['ownership'] = 'passes on source, ' + r['evidence']['ownership']


# ---------------------------------------------------------------------------------------------- waivers, review

def _apply_waivers(rows_by_subject, waivers):
    used, unmatched = [], []
    for i, w in enumerate(waivers or []):
        for key in ('body', 'row_id', 'approved_by', 'reason'):
            if not _need(w, key, 'waivers[%d]' % i):
                raise EvaluationError('waivers[%d].%s: empty' % (i, key))
        hit = None
        for r in rows_by_subject.get(w['body'], []):
            if r['id'] == w['row_id']:
                hit = r
        if hit is None or hit['state'] != REJECTED:
            unmatched.append(dict(w, why='no such row' if hit is None else 'row is %s, only an out-of-band row '
                                  'can be waived' % hit['state']))
            continue
        hit['state'] = WAIVED
        hit['evidence']['waiver'] = {'approved_by': w['approved_by'], 'reason': w['reason'],
                                     'known_failure': True}
        used.append(dict(w))
    return used, unmatched


def _take_span(take):
    """The first and last source frame the take actually sampled, or None."""
    frames = [s.get('source_frame') for s in (take or {}).get('samples', []) if _finite(s.get('source_frame'))]
    return (min(frames), max(frames)) if frames else None


def _review_row(body, review, clip, take, batch, revision):
    """A review row. A failed review names its fault_owner (who the animator judges should fix it); it may also
    name the clip and the source frames [a, b] it is about, given together, the window inside what the body's take
    sampled, and MUST when it is owned to the source (a vendor issue names its frames). A passed review cites its
    evidence (renders, contact sheet: check_evidence, revision-scoped). The animator's owner is a judgement,
    recorded as such, not a measurement."""
    if review is None:
        return {'body': body, 'result': None, 'reviewed_by': None, 'reason': None, 'state': UNMEASURABLE,
                'cause': 'review_missing', 'fault_owner': None, 'clip': None, 'source_frames': None,
                'evidence': None}
    where = 'reviews[%s]' % body
    result = _need(review, 'result', where)
    if result not in ('pass', 'fail'):
        raise EvaluationError('%s.result %r: pass or fail' % (where, result))
    for key in ('reviewed_by', 'reason'):
        if not _need(review, key, where):
            raise EvaluationError('%s.%s: empty' % (where, key))
    owner = review.get('fault_owner')
    if result == 'fail':
        if owner not in FAULT_OWNERS:
            raise EvaluationError('%s: a failed review names its fault_owner, one of %s (got %r)'
                                  % (where, list(FAULT_OWNERS), owner))
    elif owner is not None:
        raise EvaluationError('%s: a passed review names no fault_owner (got %r)' % (where, owner))
    if 'renders' in review:
        raise EvaluationError('%s.renders: cite renders under evidence, as [{path, sha256}]' % where)
    r_clip, frames = review.get('clip'), review.get('source_frames')
    if (r_clip is None) != (frames is None):
        raise EvaluationError('%s: clip and source_frames are given together or not at all' % where)
    if owner == 'source' and frames is None:
        raise EvaluationError('%s: a failed review owned to the source names its clip and source_frames (the frame '
                              'window the vendor is to fix)' % where)
    if result == 'pass' or review.get('evidence') is not None:
        evidence = check_evidence(review.get('evidence'), where + '.evidence', batch, revision)
    else:
        evidence = None
    if frames is not None:
        if r_clip != clip:
            raise EvaluationError('%s.clip %r is not the evaluated clip %r' % (where, r_clip, clip))
        a, b = _window(frames, where + '.source_frames')
        span = _take_span(take)
        if span is None:
            raise EvaluationError('%s.source_frames: the body has no sampled take to place them in' % where)
        if a < span[0] - EPS or b > span[1] + EPS:
            raise EvaluationError('%s.source_frames [%g, %g] lie outside the take [%g, %g]'
                                  % (where, a, b, span[0], span[1]))
        frames = [frames[0], frames[1]]
    out = {'body': body, 'result': result, 'reviewed_by': review['reviewed_by'], 'reason': review['reason'],
           'state': ACCEPTED if result == 'pass' else REJECTED, 'fault_owner': owner, 'clip': r_clip,
           'source_frames': frames, 'evidence': evidence}
    return out


def _decide(rows, review=None):
    states = [r['state'] for r in rows if r['required']]
    if review is not None:
        states.append(review['state'])
    if not states:
        return UNMEASURABLE
    return max(states, key=SEVERITY.__getitem__)


# ---------------------------------------------------------------------------------------------- the record

def evaluate_clip(source, bodies, labels, bands, *, required_bodies, batch, revision, invocation_id,
                  declared_source_frames, reviews=None, waivers=None):
    """The evaluation record for one clip of one revision: the source plus every body, labels, bands, reviews.

    required_bodies: every body the clip must be evaluated on (explicit; a required body missing from `bodies` is
    not sampled, a body not required is refused). bodies: {name: take or None}; a None take means the body was not
    sampled: its decision is unmeasurable. batch (an id), revision (an integer >= 1) and invocation_id (text) are
    required: every evidence path is scoped to '<batch>/r<revision>/', and the id ties the record to one run.
    declared_source_frames: [first, last], the source frames the receive receipt declares for this clip (its
    clips[].source_frames); the source and every body take must cover it (a required `span` row each), so a take
    cut short is unmeasurable even when every labelled window lies inside what it reached.
    reviews: {name: {'result': 'pass'|'fail', 'reviewed_by', 'reason', 'evidence' (required on a pass),
    'fault_owner' (required on a fail), 'clip' and 'source_frames': [a, b] (together; required on a fail owned to
    the source)}}. waivers: [{'body', 'row_id', 'approved_by', 'reason'}]. Returns the record; never raises for a
    rejection."""
    bdig = check_bands(bands)
    check_labels(labels)
    if not (isinstance(batch, str) and _ID.match(batch)):
        raise EvaluationError('batch: an id is required (got %r)' % (batch,))
    if not (isinstance(revision, int) and not isinstance(revision, bool) and revision >= 1):
        raise EvaluationError('revision: an integer >= 1 is required (got %r)' % (revision,))
    if not (isinstance(invocation_id, str) and invocation_id.strip()):
        raise EvaluationError('invocation_id: required, so the record names the run that made it (got %r)'
                              % (invocation_id,))
    if not (isinstance(required_bodies, (list, tuple)) and required_bodies
            and all(isinstance(b, str) and _ID.match(b) and b != 'source' for b in required_bodies)
            and len(set(required_bodies)) == len(required_bodies)):
        raise EvaluationError('required_bodies: a non-empty list of distinct body ids (got %r)' % (required_bodies,))
    if not (isinstance(declared_source_frames, (list, tuple)) and len(declared_source_frames) == 2
            and all(_finite(x) for x in declared_source_frames)
            and declared_source_frames[0] <= declared_source_frames[1]):
        raise EvaluationError("declared_source_frames: the receipt's [first, last] for this clip is required (got %r)"
                              % (declared_source_frames,))
    declared = [declared_source_frames[0], declared_source_frames[1]]
    bodies = dict(bodies or {})
    extra = set(bodies) - set(required_bodies)
    if extra:
        raise EvaluationError('bodies not required for this clip: %s' % sorted(extra))
    not_supplied = sorted(set(required_bodies) - set(bodies))
    for name in not_supplied:
        bodies[name] = None
    reviews = reviews or {}
    unknown = set(reviews) - set(bodies)
    if unknown:
        raise EvaluationError('reviews for bodies not evaluated: %s' % sorted(unknown))
    tol = bands['tolerances']
    src_rows = (measure_take(source, labels, bands, subject='source', role='source', batch=batch, revision=revision,
                             declared=declared)
                if source is not None else [])
    src_checks = check_checks(source, 'source', 'take[source]', batch, revision) if source is not None else {}
    body_rows, body_checks = {}, {}
    for name in sorted(bodies):
        take = bodies[name]
        if take is None:
            cause = 'body_not_supplied' if name in not_supplied else 'body_not_sampled'
            body_rows[name] = [_row(name, labels.get('clip'), 'sampled', None, None, None, 0.0, None, None, bdig,
                                    UNMEASURABLE, {'cause': cause})]
            continue
        body_rows[name] = measure_take(take, labels, bands, subject=name, role='body', batch=batch,
                                       revision=revision, declared=declared)
        body_checks[name] = check_checks(take, 'body', 'take[%s]' % name, batch, revision)
    all_rows = {'source': src_rows}
    all_rows.update(body_rows)
    used, unmatched = _apply_waivers(all_rows, waivers)
    _assign_owners(src_rows, src_checks, body_rows, body_checks, float(tol['source_excess_cm']))
    for name, rows in body_rows.items():     # a body row not sampled has no source to compare with
        for r in rows:
            if r['state'] == UNMEASURABLE and r['fault_owner'] is None:
                r['fault_owner'] = 'runtime' if r['measurement'] == 'sampled' else 'uncertain'

    review_rows = {name: _review_row(name, reviews.get(name), labels.get('clip'), bodies[name], batch, revision)
                   for name in sorted(bodies)}
    decisions = {'source': _decide(src_rows) if source is not None else UNMEASURABLE}
    for name in sorted(bodies):
        decisions[name] = _decide(body_rows[name], review_rows[name])

    blocked = []
    if source is None:
        blocked.append('source:not_measured')
    elif decisions['source'] not in (ACCEPTED, WAIVED):
        blocked.append('source:decision:%s' % decisions['source'])
    for r in src_rows:
        if r['required'] and r['state'] not in (ACCEPTED, WAIVED):
            blocked.append(r['id'] if r['window_source_frames'] else 'source:%s' % r['measurement'])
    for name in sorted(bodies):
        if decisions[name] not in (ACCEPTED, WAIVED):
            blocked.append('%s:decision:%s' % (name, decisions[name]))
        if review_rows[name]['state'] != ACCEPTED:
            blocked.append('%s:review:%s' % (name, review_rows[name]['result'] or 'missing'))
    if not labels['contacts']:
        blocked.append('labels:no_contacts')

    review_issues = [{'body': n, 'kind': 'review', 'reviewed_by': rv['reviewed_by'], 'observed': rv['reason'],
                      'fault_owner': rv['fault_owner'], 'clip': rv['clip'], 'source_frames': rv['source_frames']}
                     for n, rv in review_rows.items() if rv['result'] == 'fail']
    rows = src_rows + [r for name in sorted(body_rows) for r in body_rows[name]]
    return {
        'schema': SCHEMA, 'kind': 'mocap_evaluation', 'units': UNITS, 'batch': batch, 'revision': revision,
        'clip': labels.get('clip'), 'invocation_id': invocation_id, 'required_bodies': sorted(required_bodies),
        'declared_source_frames': declared,
        'bands': {'digest': bdig, 'status': bands['status'], 'chosen_by': bands.get('chosen_by'),
                  'tolerances': dict(tol)},
        'inputs': {'labels': digest(labels), 'source': digest(source) if source is not None else None,
                   'bodies': {n: (digest(t) if t is not None else None) for n, t in sorted(bodies.items())}},
        'rows': rows,
        'reviews': [review_rows[n] for n in sorted(review_rows)],
        'review_issues': review_issues,
        'waivers': used, 'unmatched_waivers': unmatched,
        'decisions': decisions,
        'sign_off': {'signed_off': not blocked, 'blocked_by': blocked},
    }
