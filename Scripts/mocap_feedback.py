"""Stage 5 of the body-mocap intake, feedback: the vendor brief, resolution across revisions, and sign-off.

    record  = feedback(evaluations)                        # revision 1: its vendor brief (issues only if rejected)
    record  = feedback(evaluations, prior=prior_record)    # revision n: also resolves revision n-1's open issues
    path    = write_feedback(record, feedback_root)        # feedback/<batch>-r<revision>.json + .html, never rewritten
    signoff = sign_off(record, evaluations, inventory=receipt['clips'], required_bodies=[...], labels={clip: labels},
                       approver=...,
                       verdict='accept'|'reject', reason=..., on='YYYY-MM-DD')

Design: docs/specs/2026-11-16-mocap-intake-design.md, sections 8 (decision states, fault ownership, review) and 9
(feedback and resubmission). Host Python, stdlib only.

THE EVALUATION RECORD. `evaluations` is one record, or a list of them (one per clip of one revision), exactly as
Scripts/mocap_evaluate.py's evaluate_clip writes it. The two stages share no code (writer and checker apart), so the
fields read here are restated, validated (validate_evaluation), and anything missing or out of shape refuses:

    {"schema": 1, "kind": "mocap_evaluation", "units": "cm", "batch": "<id>", "revision": <int >= 1>, "clip": "<id>",
     "invocation_id": "<text>", "required_bodies": ["<body>", ...],
     "rows": [{"id", "measurement", "body": "source" | "<body>", "clip", "foot", "landmark",
               "window_source_frames": [a, b] | null, "coverage", "measured": <number> | <bool> | null,
               "band": [lo, hi] | null, "band_digest", "state", "fault_owner", "required": <bool>,
               "evidence": {... "waiver": {"approved_by", "reason"} when waived;
                            "evidence": [{"path", "sha256"}] on a passed check: row ...}}],
     "reviews": [{"body", "result": "pass" | "fail" | null, "reviewed_by", "reason", "state",     # one per body
                  "fault_owner": <owner, required on a fail> | null, "clip", "source_frames": [a, b] | null,
                  "evidence": [{"path", "sha256"}] (required on a pass) | null}],
     "review_issues": [{"body", "kind": "review", "reviewed_by", "observed", "fault_owner", "clip",
                        "source_frames"}],                                                     # one per failed review
     "decisions": {"source": "<state>", "<body>": "<state>", ...},
     "sign_off": {"signed_off": <bool>, "blocked_by": ["..."]},
     "exchange": "simulated"}                                                # optional; refused if anything else

  <state> is accepted | rejected | unmeasurable | unsupported | waived, or `reported` on a row that is shown and
  never banded (required: false). A required row that is rejected, unmeasurable or waived must name its
  fault_owner. A banded row that is accepted has coverage 1.0, a number and lies in its band; one rejected or
  waived has coverage 1.0, a number and lies outside it. An accepted `check:` row measured true and cites evidence;
  every evidence path lies under '<batch>/r<revision>/'. A failed review owned to the source names its frames.

  Nothing in the record is trusted that can be recomputed: each decision must equal the worst of its required rows
  (and, for a body, its review), and sign_off.signed_off must equal what clip_blockers recomputes from the rows.

The revision's decision is the worst of every clip's `decisions` (source and bodies), ranked as the evaluate stage
ranks them: unsupported > rejected > unmeasurable > waived > accepted (overall_decision).

What becomes a vendor issue. Only required rows whose fault_owner is `source` (rejected or unmeasurable), and
failed animator reviews the animator owned to `source` AND placed on source frames (one `kind: review` issue per
clip and window, listing its bodies; measured_cm and band_cm are null, since nothing was measured). Everything else
that failed, every other failed or missing review included, is listed under `withheld`: recorded, never sent, so a
body-only failure is not the vendor's to fix. Waived
rows are listed under `waived` with who approved them and why. An issue states only what was MEASURED (`observed`:
landmark, measurement, source frames, band, and each row's number); a cause goes only in `hypothesis`, supplied in
`notes` and printed on the page as a hypothesis. Its `evidence` cites the evaluation record and the rows that raised
it (`evaluation:<sha256 prefix>#<row id>`), plus any review renders on those bodies.

Issues are sent whenever a source-owned failure exists (the revision is then rejected, or unsupported when another
body is out of scope: an unsupported body never hides an independent source defect).

Sign-off (sign_off). `accept` needs every clip of the received inventory (an explicit input: the receipt's clips,
each {id, source_frames}) evaluated and no other, against the receipt's source frames, with every take covering
them (an accepted `span` row per subject: a truncated take blocks), every required body (explicit too) present with every required row accepted or waived
and its review passed, the source's every required row accepted or waived, contact rows on every subject, and no
earlier issue still open. All of it is recomputed from the rows, never read from a flag.

Resubmission. A revision above 1 is resolved against the previous revision's record: every open issue (and every
earlier one still open) is `resolved` only if every row that raised it is now `accepted`, otherwise `still_open`,
with the new numbers. The earlier record is never rewritten: its hash is recorded, and write_feedback refuses to
replace a file that exists with other content.

The exchange is simulated (section 9): `exchange: "simulated"` is in every record this module writes, required by
validate_brief, and shown on the rendered page and in every sign-off.
"""
import datetime
import hashlib
import html
import json
import os
import re

SCHEMA = 1
EVALUATION_KIND = 'mocap_evaluation'
EVALUATION_SCHEMA = 1
EXCHANGE = 'simulated'
SOURCE_BODY = 'source'
STATES = ('accepted', 'rejected', 'unmeasurable', 'unsupported', 'waived')
REPORTED = 'reported'
ROW_STATES = STATES + (REPORTED,)
SEVERITY = {'accepted': 0, 'waived': 1, 'unmeasurable': 2, 'rejected': 3, 'unsupported': 4}
OWNERS = ('source', 'conversion', 'retarget', 'body_limitation', 'runtime', 'uncertain')
FAILING = ('rejected', 'unmeasurable')
NEEDS_OWNER = ('rejected', 'unmeasurable', 'waived')
ISSUE_STATUSES = ('open', 'resolved', 'still_open')
REVIEW_RESULTS = ('pass', 'fail', None)
VERDICTS = ('accept', 'reject')
FEET = ('l', 'r', None)
SIDE = {'l': 'left', 'r': 'right'}

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK_ROOT = os.path.join(REPO, 'Plugins', 'MotionTerminal', 'Tests', 'Mocap', 'feedback')

_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
EVALUATION_KEYS = ('schema', 'kind', 'units', 'batch', 'revision', 'clip', 'invocation_id', 'required_bodies',
                   'declared_source_frames', 'rows', 'reviews', 'review_issues', 'decisions', 'sign_off')
CONTACT_MEASUREMENTS = ('slide', 'ground_min', 'ground_max')
# the infrastructure checks the design requires per role (sections 6 and 8), restated here: writer and checker
# share no code
REQUIRED_CHECKS = {'source': ('viewer_pinned',), 'body': ('boundary', 'static_bake', 'target_pose')}
ACCEPTABLE = ('accepted', 'waived')
BAND_SLACK = 1e-9
ROW_KEYS = ('id', 'measurement', 'body', 'clip', 'foot', 'landmark', 'window_source_frames', 'coverage', 'measured',
            'band', 'band_digest', 'state', 'fault_owner', 'required', 'evidence')
ISSUE_KEYS = {'id', 'key', 'clip', 'kind', 'measurement', 'foot', 'landmark', 'source_frames', 'measured_cm',
              'band_cm', 'fault_owner', 'bodies', 'evidence', 'observed', 'hypothesis', 'requested_action',
              'do_not_change', 'status', 'carries', 'measured_on', 'rows'}
BRIEF_KEYS = {'schema', 'stage', 'batch', 'revision', 'supersedes', 'decision', 'exchange', 'send_to_vendor',
              'issues', 'waived', 'withheld', 'resolutions', 'evaluation_sha256', 'prior_sha256'}


class FeedbackError(ValueError):
    """A record that breaks the contract, or a write that would replace history. `reason` names which."""

    def __init__(self, reason, message):
        super().__init__('%s: %s' % (reason, message))
        self.reason = reason


def _need(cond, reason, message):
    if not cond:
        raise FeedbackError(reason, message)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf')


def _text(v):
    return isinstance(v, str) and v.strip() != ''


def _texts(v):
    return isinstance(v, list) and len(v) > 0 and all(_text(x) for x in v)


def _window(v, where, reason='evaluation'):
    _need(isinstance(v, list) and len(v) == 2 and all(_is_num(x) for x in v) and v[0] <= v[1], reason,
          '%s must be [first, last] source frames (got %r)' % (where, v))
    return v


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _as_list(evaluations):
    return list(evaluations) if isinstance(evaluations, (list, tuple)) else [evaluations]


def _evidence(refs, where, batch, revision, reason='evaluation'):
    """Evidence references: a non-empty list of {path, sha256}, every path under '<batch>/r<revision>/'."""
    _need(isinstance(refs, list) and refs, reason, '%s must cite evidence as a non-empty [{path, sha256}] (got %r)'
          % (where, refs))
    prefix = '%s/r%d/' % (batch, revision)
    for i, ref in enumerate(refs):
        w = '%s[%d]' % (where, i)
        _need(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}, reason, '%s is not {path, sha256}' % w)
        path = ref['path']
        _need(isinstance(path, str) and path.startswith(prefix) and '\\' not in path and ':' not in path
              and '..' not in path.split('/'), reason, '%s.path %r is not under %s: evidence is revision-scoped'
              % (w, path, prefix))
        _need(isinstance(ref['sha256'], str) and _SHA256.match(ref['sha256']) is not None, reason,
              '%s.sha256 must be 64 lowercase hex digits' % w)
    return refs


def _in_band(value, band):
    return band[0] - BAND_SLACK <= value <= band[1] + BAND_SLACK


def _worst(states):
    return max(states, key=SEVERITY.__getitem__) if states else 'unmeasurable'


def expected_contacts(labels):
    """The contact measurements a clip's labels require on every subject: {(measurement, foot, landmark, (a, b))}.
    Restated from the design (section 5): a `both` window is measured on the heel and on the ball."""
    contacts = labels.get('contacts') if isinstance(labels, dict) else None
    _need(isinstance(contacts, list), 'signoff', 'labels must hold a contacts list')
    out = set()
    for c in contacts:
        _need(isinstance(c, dict) and c.get('foot') in ('l', 'r') and c.get('landmark') in ('heel', 'ball', 'both'),
              'signoff', 'a contact label names foot l|r and landmark heel|ball|both (got %r)' % (c,))
        a, b = _window(c.get('source_frames'), 'labels.source_frames', 'signoff')
        for lm in (('heel', 'ball') if c['landmark'] == 'both' else (c['landmark'],)):
            for m in CONTACT_MEASUREMENTS:
                out.add((m, c['foot'], lm, (float(a), float(b))))
    return out


def clip_blockers(ev, required_bodies, labels=None):
    """Why one validated clip record cannot be signed off, recomputed from its rows and reviews (never from its
    decisions or sign_off flag): [] when it can. With `labels` (the clip's hand labels, supplied independently of
    the record), every subject must also carry a row for every contact measurement they require, so a row deleted
    from the record is a blocker, not a silent pass."""
    blocked = []
    expected = expected_contacts(labels) if labels is not None else None
    if expected is not None and not expected:
        blocked.append('labels:no_contacts')
    bodies = sorted(n for n in ev['decisions'] if n != SOURCE_BODY)
    for b in sorted(set(required_bodies) - set(bodies)):
        blocked.append('%s:not_evaluated' % b)
    for b in sorted(set(bodies) - set(required_bodies)):
        blocked.append('%s:not_required' % b)
    rows_by = {}
    for r in ev['rows']:
        rows_by.setdefault(r['body'], []).append(r)
    reviews = {rv['body']: rv for rv in ev['reviews']}
    for subject in [SOURCE_BODY] + sorted(required_bodies):
        rows = [r for r in rows_by.get(subject, []) if r['required']]
        role = 'source' if subject == SOURCE_BODY else 'body'
        if not rows:
            blocked.append('%s:no_rows' % subject)
            continue
        if not any(r['measurement'] in CONTACT_MEASUREMENTS for r in rows):
            blocked.append('%s:no_contact_rows' % subject)
        span = [r for r in rows if r['measurement'] == 'span']
        if len(span) != 1 or span[0]['state'] != 'accepted':
            blocked.append('%s:span:%s' % (subject, span[0]['state'] if len(span) == 1 else 'missing'))
        if expected is not None:
            have = {(r['measurement'], r['foot'], r['landmark'], tuple(float(x) for x in r['window_source_frames']))
                    for r in rows if r['measurement'] in CONTACT_MEASUREMENTS and r['window_source_frames']}
            for m, foot, lm, (a, b) in sorted(expected - have):
                blocked.append('%s:%s:%s:%s:%s-%s:missing_row' % (subject, m, foot, lm, _fmt(a), _fmt(b)))
        for r in rows:
            if r['state'] not in ACCEPTABLE:
                blocked.append('%s:%s:%s' % (subject, r['id'], r['state']))
        names = {r['measurement'][6:]: r for r in rows if r['measurement'].startswith('check:')}
        for name in REQUIRED_CHECKS[role]:
            if name not in names or names[name]['state'] != 'accepted':
                blocked.append('%s:check:%s' % (subject, name))
        if role == 'body':
            parity = [r for r in rows if r['measurement'] == 'sampler_parity']
            if len(parity) != 1 or parity[0]['state'] != 'accepted':
                blocked.append('%s:sampler_parity' % subject)
            rv = reviews.get(subject)
            if rv is None or rv['result'] != 'pass' or rv['state'] != 'accepted':
                blocked.append('%s:review:%s' % (subject, (rv or {}).get('result') or 'missing'))
    return blocked


# --------------------------------------------------------------------------------------------- the evaluation record
def validate_evaluation(ev):
    """Raise FeedbackError unless `ev` is an evaluate_clip record with every field this stage reads; return it."""
    _need(isinstance(ev, dict), 'evaluation', 'the evaluation record must be an object')
    missing = [k for k in EVALUATION_KEYS if k not in ev]
    _need(not missing, 'evaluation', 'the evaluation record is missing %s' % missing)
    _need(ev['kind'] == EVALUATION_KIND and ev['schema'] == EVALUATION_SCHEMA, 'evaluation',
          'not a %s record of schema %d (kind %r, schema %r)' % (EVALUATION_KIND, EVALUATION_SCHEMA, ev['kind'],
                                                                 ev['schema']))
    _need(ev['units'] == 'cm', 'evaluation', 'units must be cm (got %r)' % ev['units'])
    _need(isinstance(ev['batch'], str) and _ID.match(ev['batch']) is not None, 'evaluation',
          'batch must be an id: a brief names its batch (got %r)' % (ev['batch'],))
    _need(_is_int(ev['revision']) and ev['revision'] >= 1, 'evaluation',
          'revision must be an integer >= 1: a brief names its revision (got %r)' % (ev['revision'],))
    _need(_text(ev['clip']), 'evaluation', 'clip must be text')
    _need(_text(ev['invocation_id']), 'evaluation', 'invocation_id must be text: a record names the run that made it')
    batch, revision = ev['batch'], ev['revision']
    _window(ev['declared_source_frames'], 'declared_source_frames')
    if 'exchange' in ev:
        _need(ev['exchange'] == EXCHANGE, 'exchange', 'exchange must be %r (got %r)' % (EXCHANGE, ev['exchange']))

    decisions = ev['decisions']
    _need(isinstance(decisions, dict) and SOURCE_BODY in decisions and len(decisions) >= 2, 'evaluation',
          'decisions must hold the source and at least one body')
    for name, state in decisions.items():
        _need(state in STATES, 'evaluation', 'decisions.%s %r is not a state' % (name, state))
    bodies = sorted(n for n in decisions if n != SOURCE_BODY)
    _need(isinstance(ev['required_bodies'], list) and sorted(ev['required_bodies']) == bodies, 'evaluation',
          'required_bodies %r must be exactly the bodies decided %s' % (ev['required_bodies'], bodies))

    reviews = ev['reviews']
    _need(isinstance(reviews, list), 'evaluation', 'reviews must be a list')
    by_body = {}
    for i, rv in enumerate(reviews):
        where = 'reviews[%d]' % i
        _need(isinstance(rv, dict) and rv.get('body') in bodies, 'evaluation', '%s names no evaluated body' % where)
        _need(rv['body'] not in by_body, 'evaluation', '%s: %s is reviewed twice' % (where, rv['body']))
        by_body[rv['body']] = rv
        _need(rv.get('result') in REVIEW_RESULTS, 'evaluation', '%s.result must be pass, fail or null' % where)
        if rv['result'] is None:
            _need(rv.get('state') == 'unmeasurable', 'evaluation', '%s: a missing review is unmeasurable' % where)
        else:
            _need(_text(rv.get('reviewed_by')) and _text(rv.get('reason')), 'evaluation',
                  '%s must say who reviewed it and why' % where)
            _need(rv.get('state') == ('accepted' if rv['result'] == 'pass' else 'rejected'), 'evaluation',
                  '%s.state does not follow its result' % where)
        if rv['result'] == 'fail':
            _need(rv.get('fault_owner') in OWNERS, 'evaluation', '%s: a failed review names its fault_owner (one '
                  'of %s)' % (where, list(OWNERS)))
        else:
            _need(rv.get('fault_owner') is None, 'evaluation', '%s: only a failed review names a fault_owner' % where)
        if rv['result'] == 'pass' or rv.get('evidence') is not None:
            _evidence(rv.get('evidence'), where + '.evidence', batch, revision)
        _need((rv.get('clip') is None) == (rv.get('source_frames') is None), 'evaluation',
              '%s: clip and source_frames are given together or not at all' % where)
        _need(rv.get('fault_owner') != 'source' or rv.get('source_frames') is not None, 'evaluation',
              '%s: a failed review owned to the source names its frame window' % where)
        if rv.get('source_frames') is not None:
            _need(rv['clip'] == ev['clip'], 'evaluation', '%s.clip %r is not the record\'s clip' % (where, rv['clip']))
            _window(rv['source_frames'], where + '.source_frames')
    _need(sorted(by_body) == bodies, 'evaluation', 'the animator review is mandatory: every body has a review row '
          '(reviewed %s, bodies %s)' % (sorted(by_body), bodies))
    _need(isinstance(ev['review_issues'], list), 'evaluation', 'review_issues must be a list')
    failed = sorted(b for b, rv in by_body.items() if rv['result'] == 'fail')
    _need(sorted(ri.get('body') for ri in ev['review_issues'] if isinstance(ri, dict)) == failed, 'evaluation',
          'review_issues must list exactly the failed reviews (%s)' % failed)

    rows = ev['rows']
    _need(isinstance(rows, list), 'evaluation', 'rows must be a list')
    ids = set()
    for i, row in enumerate(rows):
        where = 'rows[%d]' % i
        _need(isinstance(row, dict), 'evaluation', '%s must be an object' % where)
        lacking = [k for k in ROW_KEYS if k not in row]
        _need(not lacking, 'evaluation', '%s is missing %s' % (where, lacking))
        _need((row['body'], row['id']) not in ids, 'evaluation', '%s: id %r repeats on %s' % (where, row['id'],
                                                                                             row['body']))
        ids.add((row['body'], row['id']))
        _need(row['body'] in decisions, 'evaluation', '%s.body %r has no decision' % (where, row['body']))
        _need(row['clip'] == ev['clip'], 'evaluation', '%s.clip %r is not the record\'s clip' % (where, row['clip']))
        _need(_text(row['measurement']), 'evaluation', '%s.measurement must be text' % where)
        _need(row['foot'] in FEET, 'evaluation', '%s.foot must be l, r or null' % where)
        _need(row['state'] in ROW_STATES, 'evaluation', '%s.state %r is not a state' % (where, row['state']))
        _need(isinstance(row['required'], bool), 'evaluation', '%s.required must be true or false' % where)
        _need(isinstance(row['evidence'], dict), 'evaluation', '%s.evidence must be an object' % where)
        if row['window_source_frames'] is not None:
            _window(row['window_source_frames'], where + '.window_source_frames')
        if row['band'] is not None:
            b = row['band']
            _need(isinstance(b, list) and len(b) == 2 and all(_is_num(x) for x in b) and b[0] <= b[1],
                  'evaluation', '%s.band must be [lo, hi] or null' % where)
        m = row['measured']
        _need(m is None or isinstance(m, bool) or _is_num(m), 'evaluation', '%s.measured: finite, bool or null'
              % where)
        if row['state'] in ('rejected', 'waived') and row['window_source_frames'] is not None:
            _need(_is_num(m) and row['band'] is not None, 'evaluation', '%s is out of band but has no number or '
                  'band' % where)
        if row['required'] and row['state'] in ('accepted', 'rejected', 'waived'):
            # a decided row is decided on a full measurement: recompute it instead of trusting the state
            _need(row['coverage'] == 1.0, 'evaluation', '%s is %s on coverage %r: only a full window decides'
                  % (where, row['state'], row['coverage']))
            if row['band'] is not None:
                _need(_is_num(m), 'evaluation', '%s is %s with no measured number' % (where, row['state']))
                _need(_in_band(m, row['band']) == (row['state'] == 'accepted'), 'evaluation',
                      '%s is %s but measured %r against band %r' % (where, row['state'], m, row['band']))
            elif row['measurement'] == 'span':
                _need(row['state'] == 'accepted' and m is True and row['evidence'].get('declared_source_frames')
                      == ev['declared_source_frames'], 'evaluation', '%s: a span row is accepted only when the take '
                      'covered the record\'s declared_source_frames' % where)
            elif row['measurement'].startswith('check:'):
                _need(row['state'] == 'accepted' and m is True, 'evaluation', '%s: a check row is accepted only '
                      'when it measured true' % where)
                _evidence(row['evidence'].get('evidence'), where + '.evidence.evidence', batch, revision)
            else:
                _need(False, 'evaluation', '%s (%s) is %s, but only a banded or check: row can be decided'
                      % (where, row['measurement'], row['state']))
        if row['required'] and row['state'] in NEEDS_OWNER:
            _need(row['fault_owner'] in OWNERS, 'evaluation', '%s is %s and names no fault_owner (one of %s)'
                  % (where, row['state'], list(OWNERS)))
        else:
            _need(row['fault_owner'] is None or row['fault_owner'] in OWNERS, 'evaluation', '%s.fault_owner' % where)
        if row['state'] == 'waived':
            w = row['evidence'].get('waiver')
            _need(isinstance(w, dict) and _text(w.get('approved_by')) and _text(w.get('reason')), 'evaluation',
                  '%s is waived and must say who approved it and why' % where)
        if row['required'] and row['state'] in STATES:
            _need(SEVERITY[decisions[row['body']]] >= SEVERITY[row['state']], 'evaluation',
                  '%s is %s but %s\'s decision is %s: a decision is at least its worst row'
                  % (where, row['state'], row['body'], decisions[row['body']]))

    # every decision is recomputed: the worst required row, and for a body its review too
    for name, state in decisions.items():
        states = [r['state'] for r in rows if r['body'] == name and r['required']]
        if name != SOURCE_BODY:
            states.append(by_body[name]['state'])
        _need(state == _worst(states), 'evaluation', 'decisions.%s is %s, but its worst row is %s'
              % (name, state, _worst(states)))

    so = ev['sign_off']
    _need(isinstance(so, dict) and isinstance(so.get('signed_off'), bool) and isinstance(so.get('blocked_by'), list),
          'evaluation', 'sign_off must be {signed_off: bool, blocked_by: [...]}')
    _need(so['signed_off'] == (not so['blocked_by']), 'evaluation', 'sign_off.signed_off contradicts blocked_by')
    recomputed = clip_blockers(ev, ev['required_bodies'])
    _need(so['signed_off'] == (not recomputed), 'evaluation', 'sign_off.signed_off is %s, but the rows say %s'
          % (so['signed_off'], recomputed or 'nothing blocks'))
    return ev


def validate_evaluations(evaluations):
    """One revision's records: each valid, one batch and revision, no clip twice."""
    evs = _as_list(evaluations)
    _need(evs, 'evaluation', 'at least one evaluation record is required')
    for ev in evs:
        validate_evaluation(ev)
    _need(len({(e['batch'], e['revision']) for e in evs}) == 1, 'evaluation', 'the records are from more than one '
          'batch or revision')
    clips = [e['clip'] for e in evs]
    _need(len(set(clips)) == len(clips), 'evaluation', 'a clip is evaluated twice: %s' % clips)
    return evs


def overall_decision(evaluations):
    """The revision's decision: the worst decision of every clip's source and bodies."""
    evs = _as_list(evaluations)
    return max((s for e in evs for s in e['decisions'].values()), key=SEVERITY.__getitem__)


# ------------------------------------------------------------------------------------------------------------- issues
def issue_key(clip, measurement, foot, landmark, frames):
    """The identity of a measured issue across revisions: clip, measurement, foot, landmark and source window."""
    return '%s/%s/%s/%s/%s-%s' % (clip, measurement, foot or '-', landmark or '-', _fmt(frames[0]), _fmt(frames[1]))


def _fmt(x):
    return ('%d' % x) if float(x).is_integer() else ('%g' % x)


def _row_key(row):
    return issue_key(row['clip'], row['measurement'], row['foot'], row['landmark'], row['window_source_frames'])


def _who(foot, landmark):
    parts = [SIDE[foot]] if foot else []
    if landmark:
        parts.append(landmark)
    return ' '.join(parts) or 'the measured point'


def _frames_text(frames):
    return 'source frames %s-%s' % (_fmt(frames[0]), _fmt(frames[1]))


def _observed(measurement, foot, landmark, frames, band, measured_by):
    """What was measured, per row, and nothing else: [(body, cm or None)], the source first."""
    each = ', '.join('%s %s' % (body, ('%g cm' % v) if _is_num(v) else 'not measurable') for body, v in measured_by)
    return '%s %s over %s, band [%g, %g] cm: %s' % (_who(foot, landmark), measurement, _frames_text(frames),
                                                    band[0], band[1], each)


def _action(measurement, foot, landmark, frames, band, measured):
    who, span = _who(foot, landmark), _frames_text(frames)
    if measured is None:
        return 'deliver the %s contact so it can be measured, %s' % (who, span)
    if measurement == 'slide':
        return 'remove the drift of the %s plant, %s' % (who, span)
    if measurement in ('ground_min', 'ground_max'):
        # name the bound that failed: below lo is a penetration (raise it), above hi a float (bring it down)
        if measured < band[0]:
            return 'raise the %s to at least %g cm clearance during the plant, %s' % (who, band[0], span)
        if measured > band[1]:
            return 'bring the %s down to at most %g cm clearance during the plant, %s' % (who, band[1], span)
    return 'bring the %s %s within [%g, %g], %s' % (who, measurement, band[0], band[1], span)


def _do_not_change(foot, frames):
    keep = ['timing of every other contact', 'every source frame outside %s-%s' % (_fmt(frames[0]), _fmt(frames[1]))]
    if foot:
        keep.append('the %s foot' % SIDE['r' if foot == 'l' else 'l'])
    return keep


def _renders(ev, body):
    for rv in ev['reviews']:
        if rv['body'] == body:
            return ['%s@sha256:%s' % (f['path'], f['sha256']) for f in (rv.get('evidence') or [])]
    return []


def _issues(ev, notes):
    """(issues without ids, withheld, waived) from one validated record."""
    cite = 'evaluation:%s' % digest(ev)[:16]
    groups = {}
    for row in ev['rows']:
        if row['window_source_frames'] is not None:
            groups.setdefault(_row_key(row), []).append(row)
    issues, withheld, waived = [], [], []
    for row in ev['rows']:
        if row['state'] == 'waived':
            w = row['evidence']['waiver']
            waived.append({'row': row['id'], 'body': row['body'], 'measured_cm': row['measured'],
                           'band_cm': row['band'], 'fault_owner': row['fault_owner'], 'by': w['approved_by'],
                           'reason': w['reason']})
        elif (row['required'] and row['state'] in FAILING
              and (row['fault_owner'] != 'source' or row['window_source_frames'] is None)):
            withheld.append({'row': row['id'], 'kind': 'metric', 'body': row['body'], 'state': row['state'],
                             'measured': row['measured'], 'fault_owner': row['fault_owner'],
                             'cause': row['evidence'].get('cause')})
    review_groups = {}
    for rv in ev['reviews']:
        if rv['state'] == 'accepted':
            continue
        if _review_to_vendor(rv):
            review_groups.setdefault(_review_key(ev['clip'], rv['source_frames']), []).append(rv)
            continue
        withheld.append({'row': 'review:%s' % rv['body'], 'kind': 'review', 'body': rv['body'],
                         'state': rv['state'], 'measured': None, 'fault_owner': rv.get('fault_owner'),
                         'cause': rv['reason'] if rv['result'] == 'fail' else 'review_missing'})

    for key in sorted(groups):
        raised = [r for r in groups[key] if r['required'] and r['state'] in FAILING and r['fault_owner'] == 'source']
        if not raised:
            continue
        first = raised[0]
        src = [r for r in raised if r['body'] == SOURCE_BODY]
        bodies = sorted(r['body'] for r in raised if r['body'] != SOURCE_BODY)
        where = ([SOURCE_BODY] if src else []) + bodies
        by_body = {r['body']: r['measured'] for r in raised}
        # measured_cm: the source's own number when it has one, else the largest a body measured; each row's number
        # (or its absence) is in `observed`, so a missing value never reads as zero or disappears
        nums = [v for v in by_body.values() if _is_num(v)]
        if src and _is_num(src[0]['measured']):
            measured = src[0]['measured']
        else:
            measured = max(nums) if nums else None
        band = first['band'] or [0, 0]
        _need(first['band'] is not None, 'evaluation', 'row %s raised an issue but has no band' % first['id'])
        frames = list(first['window_source_frames'])
        evidence = ['%s#%s/%s' % (cite, r['body'], r['id']) for r in raised]
        evidence += sorted({f for b in bodies for f in _renders(ev, b)})
        note = (notes or {}).get(key, {})
        issues.append({
            'key': key, 'clip': first['clip'], 'kind': 'metric', 'measurement': first['measurement'],
            'foot': first['foot'], 'landmark': first['landmark'], 'source_frames': frames,
            'measured_cm': measured, 'band_cm': list(band), 'fault_owner': 'source', 'bodies': bodies,
            'measured_on': where, 'rows': ['%s/%s' % (r['body'], r['id']) for r in raised], 'evidence': evidence,
            'observed': _observed(first['measurement'], first['foot'], first['landmark'], frames, band,
                                  [(b, by_body[b]) for b in where]),
            'hypothesis': note.get('hypothesis'),
            'requested_action': note.get('requested_action') or _action(first['measurement'], first['foot'],
                                                                        first['landmark'], frames, band, measured),
            'do_not_change': list(note.get('do_not_change') or _do_not_change(first['foot'], frames)),
            'status': 'open'})
    for key in sorted(review_groups):
        rvs = sorted(review_groups[key], key=lambda r: r['body'])
        frames = list(rvs[0]['source_frames'])
        bodies = [r['body'] for r in rvs]
        span = _frames_text(frames)
        note = (notes or {}).get(key, {})
        evidence = ['%s#review:%s' % (cite, b) for b in bodies]
        evidence += sorted({f for b in bodies for f in _renders(ev, b)})
        issues.append({
            'key': key, 'clip': ev['clip'], 'kind': 'review', 'measurement': 'animator_review', 'foot': None,
            'landmark': None, 'source_frames': frames, 'measured_cm': None, 'band_cm': None, 'fault_owner': 'source',
            'bodies': bodies, 'measured_on': bodies, 'rows': ['review:%s' % b for b in bodies], 'evidence': evidence,
            'observed': 'animator review failed over %s: %s' % (span, '; '.join(
                '%s (%s): %s' % (r['body'], r['reviewed_by'], r['reason']) for r in rvs)),
            'hypothesis': note.get('hypothesis'),
            'requested_action': note.get('requested_action') or 'address the animator\'s note over %s' % span,
            'do_not_change': list(note.get('do_not_change') or _do_not_change(None, frames)),
            'status': 'open'})
    return issues, withheld, waived


def _review_to_vendor(rv):
    """A failed review goes to the vendor only when the animator owned it to the source and named its frames."""
    return rv['result'] == 'fail' and rv.get('fault_owner') == 'source' and rv.get('source_frames') is not None


def _review_key(clip, frames):
    return issue_key(clip, 'animator_review', None, None, frames)


# --------------------------------------------------------------------------------------------------------- resolution
def open_items(prior):
    """What a resubmission must answer: the prior record's open issues and the earlier issues it left still open."""
    items = [dict(i, raised_in=prior['revision']) for i in prior['issues'] if i['status'] == 'open']
    items += [dict(r['issue'], raised_in=r['raised_in']) for r in prior['resolutions'] if r['status'] == 'still_open']
    return items


def _resolve(item, evs):
    """(status, now): resolved only when every row that raised the item is accepted now. A review issue is resolved
    only when every body it named now has a passed review on that clip."""
    if item.get('kind') == 'review':
        reviews = {rv['body']: rv for ev in evs if ev['clip'] == item['clip'] for rv in ev['reviews']}
        now = {b: ({'state': reviews[b]['state'], 'measured_cm': None} if b in reviews else None)
               for b in item['measured_on']}
        ok = bool(now) and all(v is not None and v['state'] == 'accepted' for v in now.values())
        return ('resolved' if ok else 'still_open'), now
    rows = {r['body']: r for ev in evs for r in ev['rows']
            if r['window_source_frames'] is not None and _row_key(r) == item['key']}
    now = {b: ({'state': rows[b]['state'], 'measured_cm': rows[b]['measured']} if b in rows else None)
           for b in item['measured_on']}
    ok = bool(now) and all(v is not None and v['state'] == 'accepted' for v in now.values())
    return ('resolved' if ok else 'still_open'), now


def resolutions(prior, evs):
    out = []
    for item in open_items(prior):
        status, now = _resolve(item, evs)
        issue = {k: v for k, v in item.items() if k != 'raised_in'}
        issue['status'] = 'open'
        out.append({'issue_id': item['id'], 'raised_in': item['raised_in'], 'issue': issue, 'status': status,
                    'previous_measured_cm': item['measured_cm'], 'now': now})
    return out


# -------------------------------------------------------------------------------------------------------------- brief
def feedback(evaluations, prior=None, notes=None):
    """The stage-5 record for one revision: the vendor brief (issues only when rejected) and, for a resubmission,
    the resolution of the previous revision's issues. `prior` is the previous revision's record (required above
    revision 1); it is read, hashed and never changed. notes: {issue_key: {hypothesis, requested_action,
    do_not_change}}."""
    evs = validate_evaluations(evaluations)
    rev, batch = evs[0]['revision'], evs[0]['batch']
    if rev > 1:
        _need(prior is not None, 'prior', 'revision %d needs revision %d\'s record to resolve against' % (rev, rev - 1))
        validate_brief(prior)
        _need(prior['batch'] == batch and prior['revision'] == rev - 1, 'prior', 'the prior record is %s r%d, not '
              '%s r%d' % (prior['batch'], prior['revision'], batch, rev - 1))
    else:
        _need(prior is None, 'prior', 'revision 1 has no prior record')
    decision = overall_decision(evs)

    issues, withheld, waived = [], [], []
    for ev in sorted(evs, key=lambda e: e['clip']):
        i, w, v = _issues(ev, notes)
        issues += i
        withheld += [dict(x, clip=ev['clip']) for x in w]
        waived += [dict(x, clip=ev['clip']) for x in v]
    # every source-owned issue is sent: each rests on a rejected row or a rejected review, so the revision is
    # rejected, or unsupported when another body is out of scope; an unsupported body never withholds it
    _need(not issues or decision in ('rejected', 'unsupported'), 'brief', 'source-owned issues under decision %s'
          % decision)
    res = resolutions(prior, evs) if prior is not None else []
    still = {r['issue']['key']: r['issue_id'] for r in res if r['status'] == 'still_open'}
    for n, issue in enumerate(issues, 1):
        issue['id'] = '%s-r%d-%d' % (batch, rev, n)
        issue['carries'] = still.get(issue['key'])
    record = {'schema': SCHEMA, 'stage': 'feedback', 'batch': batch, 'revision': rev,
              'supersedes': rev - 1 if rev > 1 else None, 'decision': decision, 'exchange': EXCHANGE,
              'send_to_vendor': bool(issues), 'issues': issues, 'waived': waived, 'withheld': withheld,
              'resolutions': res, 'evaluation_sha256': digest(evs),
              'prior_sha256': digest(prior) if prior is not None else None}
    return validate_brief(record)


def validate_brief(b):
    """Raise FeedbackError unless `b` is a well-formed feedback record; return it."""
    _need(isinstance(b, dict), 'brief', 'a feedback record must be an object')
    missing = sorted(BRIEF_KEYS - set(b))
    _need(not missing, 'brief', 'the feedback record is missing %s' % missing)
    _need(b['exchange'] == EXCHANGE, 'exchange', 'every brief says exchange %r (got %r)' % (EXCHANGE, b['exchange']))
    _need(b['decision'] in STATES, 'brief', 'decision must be a state')
    _need(_is_int(b['revision']) and b['revision'] >= 1, 'brief', 'revision must be an integer >= 1')
    _need(b['supersedes'] == (b['revision'] - 1 if b['revision'] > 1 else None), 'brief', 'supersedes must name '
          'the previous revision')
    _need(b['send_to_vendor'] == bool(b['issues']), 'brief', 'send_to_vendor must say whether there are issues')
    _need(not b['issues'] or b['decision'] in ('rejected', 'unsupported'), 'brief', 'only a rejected (or '
          'unsupported) revision raises issues')
    ids = set()
    for i, issue in enumerate(b['issues']):
        where = 'issues[%d]' % i
        extra = sorted(set(issue) - ISSUE_KEYS)
        lacking = sorted(ISSUE_KEYS - set(issue))
        _need(not extra and not lacking, 'issue', '%s keys: unknown %s, missing %s' % (where, extra, lacking))
        _need(issue['id'] == '%s-r%d-%d' % (b['batch'], b['revision'], i + 1) and issue['id'] not in ids, 'issue',
              '%s.id must be %s-r%d-%d' % (where, b['batch'], b['revision'], i + 1))
        ids.add(issue['id'])
        _need(issue['fault_owner'] == 'source', 'issue', '%s: only source-owned issues go to the vendor' % where)
        _window(issue['source_frames'], where + '.source_frames', 'issue')
        _need(_texts(issue['evidence']), 'issue', '%s must cite its evidence' % where)
        _need(_text(issue['observed']), 'issue', '%s must state what was observed' % where)
        _need(_text(issue['requested_action']), 'issue', '%s must request an action' % where)
        _need(_texts(issue['do_not_change']), 'issue', '%s must say what not to change' % where)
        _need(issue['hypothesis'] is None or _text(issue['hypothesis']), 'issue', '%s.hypothesis: text or null'
              % where)
        _need(issue['status'] in ISSUE_STATUSES, 'issue', '%s.status must be one of %s' % (where, list(ISSUE_STATUSES)))
        _need(issue['kind'] in ('metric', 'review'), 'issue', '%s.kind must be metric or review' % where)
        _need(_texts(issue['measured_on']), 'issue', '%s must say where it was measured' % where)
    for r in b['resolutions']:
        _need(r.get('status') in ('resolved', 'still_open'), 'resolution', 'a resolution is resolved or still_open')
        _need(isinstance(r.get('issue'), dict) and r.get('issue_id') == r['issue'].get('id'), 'resolution',
              'a resolution carries the issue it resolves')
    return b


# --------------------------------------------------------------------------------------------------------- the files
def brief_path(feedback_root, batch, revision, suffix=''):
    return os.path.join(feedback_root, '%s-r%d%s' % (batch, revision, suffix))


def _write_once(path, text):
    """Write text to path unless the file exists; an existing file with other content refuses (history is kept)."""
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            _need(fh.read() == text, 'history', '%s exists with other content; an earlier record is never rewritten'
                  % path)
        return path
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'x', encoding='utf-8') as fh:
        fh.write(text)
    return path


def write_feedback(record, feedback_root=FEEDBACK_ROOT):
    """feedback/<batch>-r<revision>.json and its page; returns the JSON's path."""
    validate_brief(record)
    path = _write_once(brief_path(feedback_root, record['batch'], record['revision'], '.json'),
                       json.dumps(record, sort_keys=True, indent=1) + '\n')
    _write_once(brief_path(feedback_root, record['batch'], record['revision'], '.html'), render_page(record))
    return path


def load_feedback(feedback_root, batch, revision):
    with open(brief_path(feedback_root, batch, revision, '.json'), encoding='utf-8') as fh:
        return validate_brief(json.load(fh))


def render_page(record):
    """A feedback record as a self-contained HTML page. The simulated exchange is the first thing on it."""
    e = html.escape
    out = ['<!doctype html><html><head><meta charset="utf-8"><title>Brief %s r%d</title></head><body>'
           % (e(record['batch']), record['revision']),
           '<p class="exchange"><strong>Exchange: %s.</strong> This is a simulated vendor exchange, not a real one.'
           '</p>' % e(record['exchange']),
           '<h1>%s revision %d: %s</h1>' % (e(record['batch']), record['revision'], e(record['decision']))]
    if record['resolutions']:
        out.append('<h2>Earlier issues</h2><ul>')
        for r in record['resolutions']:
            out.append('<li>%s (raised in r%d): <strong>%s</strong>; was %s cm, now %s</li>' % (
                e(r['issue_id']), r['raised_in'], e(r['status']), e(str(r['previous_measured_cm'])),
                e(json.dumps(r['now'], sort_keys=True))))
        out.append('</ul>')
    out.append('<h2>Issues for the vendor</h2>' + ('' if record['issues'] else '<p>None.</p>'))
    for i in record['issues']:
        out.append('<section><h3>%s</h3><p>Observed: %s</p>' % (e(i['id']), e(i['observed'])))
        if i['hypothesis']:
            out.append('<p><em>Hypothesis, not measured:</em> %s</p>' % e(i['hypothesis']))
        out.append('<p>Requested: %s</p><p>Do not change: %s</p><p>Evidence: %s</p></section>' % (
            e(i['requested_action']), e('; '.join(i['do_not_change'])), e(', '.join(i['evidence']))))
    if record['waived']:
        out.append('<h2>Waived (known failures)</h2><ul>%s</ul>' % ''.join(
            '<li>%s on %s, waived by %s: %s</li>' % (e(w['row']), e(w['body']), e(w['by']), e(w['reason']))
            for w in record['waived']))
    if record['withheld']:
        out.append('<h2>Not sent: not owned by the source</h2><ul>%s</ul>' % ''.join(
            '<li>%s on %s: %s (%s)</li>' % (e(w['row']), e(w['body']), e(str(w['fault_owner'])), e(str(w['cause'])))
            for w in record['withheld']))
    out.append('</body></html>\n')
    return '\n'.join(out)


# ----------------------------------------------------------------------------------------------------------- sign-off
def sign_off(record, evaluations, *, inventory, required_bodies, labels, approver, verdict, reason, on):
    """The approver's decision on one revision, as a record.

    inventory: the received revision's clips, as the receive receipt lists them (its `clips`): a non-empty list of
    {'id': <clip id>, 'source_frames': [first, last], ...}; only id and source_frames are read (other receipt fields
    are ignored, and this module imports nothing from the receive stage). required_bodies: every body each clip must
    pass on. labels: {clip: the clip's hand labels} (read from the labels folder, not from the record). All are
    explicit, so nothing omitted from the evaluations, a clip, a body, a measured row or frames cut from a take,
    goes unnoticed.

    `accept` needs, recomputed from the rows (clip_blockers) and never read from a flag: every inventory clip
    evaluated and no other, each evaluated against the receipt's source_frames with every take covering them (an
    accepted `span` row on the source and every body), the source and every required body with every required row
    accepted or waived, contact rows on each, every review passed, and no earlier issue still open (sections 8 and
    9); `reject` is always open to the approver. Both need a reason."""
    validate_brief(record)
    evs = validate_evaluations(evaluations)
    _need(digest(evs) == record['evaluation_sha256'], 'signoff', 'the evaluations are not the ones the record was '
          'made from')
    _need(isinstance(inventory, (list, tuple)) and inventory, 'signoff', 'inventory must be the receipt\'s clips, '
          'non-empty (got %r)' % (inventory,))
    declared = {}
    for i, clip in enumerate(inventory):
        _need(isinstance(clip, dict) and _text(clip.get('id')) and clip['id'] not in declared, 'signoff',
              'inventory[%d] must be a receipt clip {id, source_frames} with a distinct id (got %r)' % (i, clip))
        declared[clip['id']] = list(_window(clip.get('source_frames'), 'inventory[%d].source_frames' % i, 'signoff'))
    _need(isinstance(required_bodies, (list, tuple)) and required_bodies and all(_text(b) for b in required_bodies)
          and SOURCE_BODY not in required_bodies and len(set(required_bodies)) == len(required_bodies), 'signoff',
          'required_bodies must be distinct body ids (got %r)' % (required_bodies,))
    _need(isinstance(labels, dict), 'signoff', 'labels must be {clip: labels}')
    _need(_text(approver), 'signoff', 'a sign-off names its approver')
    _need(verdict in VERDICTS, 'signoff', 'verdict must be one of %s' % list(VERDICTS))
    _need(_text(reason), 'signoff', 'a sign-off gives its reason')
    try:
        datetime.date.fromisoformat(on)
    except (TypeError, ValueError):
        raise FeedbackError('signoff', 'on must be YYYY-MM-DD (got %r)' % (on,)) from None
    clips = {}
    for ev in evs:
        reviews = {rv['body']: rv['result'] for rv in ev['reviews']}
        clips[ev['clip']] = {'bodies': {b: {'decision': s, 'review': reviews[b]}
                                        for b, s in sorted(ev['decisions'].items()) if b != SOURCE_BODY},
                             'source': ev['decisions'][SOURCE_BODY],
                             'blocked_by': (clip_blockers(ev, required_bodies, labels[ev['clip']])
                                            if ev['clip'] in labels else
                                            clip_blockers(ev, required_bodies) + ['labels:not_supplied'])}
    still_open = sorted(r['issue_id'] for r in record['resolutions'] if r['status'] == 'still_open')
    if verdict == 'accept':
        blocked = ['%s: not evaluated' % c for c in sorted(set(declared) - set(clips))]
        blocked += ['%s: not in the received inventory' % c for c in sorted(set(clips) - set(declared))]
        blocked += ['%s: evaluated against declared source frames %s, the receipt declares %s'
                    % (ev['clip'], ev['declared_source_frames'], declared[ev['clip']]) for ev in evs
                    if ev['clip'] in declared and [float(x) for x in ev['declared_source_frames']]
                    != [float(x) for x in declared[ev['clip']]]]
        blocked += ['%s: %s' % (c, x) for c, v in sorted(clips.items()) for x in v['blocked_by']]
        blocked += ['%s still open' % i for i in still_open]
        if record['decision'] not in ACCEPTABLE or record['issues']:
            blocked.append('the revision is %s with %d open issue(s)' % (record['decision'], len(record['issues'])))
        _need(not blocked, 'signoff', 'cannot accept: %s' % '; '.join(blocked))
    return {'schema': SCHEMA, 'stage': 'signoff', 'batch': record['batch'], 'revision': record['revision'],
            'exchange': EXCHANGE, 'approver': approver, 'verdict': verdict, 'reason': reason, 'on': on,
            'inventory': {c: declared[c] for c in sorted(declared)}, 'required_bodies': sorted(required_bodies), 'still_open': still_open,
            'clips': clips, 'waived': ['%s %s/%s' % (w['clip'], w['body'], w['row']) for w in record['waived']],
            'record_sha256': digest(record), 'evaluation_sha256': record['evaluation_sha256']}


def write_sign_off(signoff, feedback_root=FEEDBACK_ROOT):
    _need(signoff.get('exchange') == EXCHANGE, 'exchange', 'a sign-off says exchange %r' % EXCHANGE)
    return _write_once(brief_path(feedback_root, signoff['batch'], signoff['revision'], '-signoff.json'),
                       json.dumps(signoff, sort_keys=True, indent=1) + '\n')
