"""mocap_evaluate: the host-side checker of the intake's evaluate stage, on synthetic takes with defects of known size.

Design: docs/specs/2026-11-16-mocap-intake-design.md, sections 5 and 8. Every take here is built by hand: a two-foot
walk sampled at 30 Hz from a 120 Hz source (one sample every 4 source frames, source frames 1..241). A planted
landmark sits still at 0.2 cm clearance; a swinging one is 8 cm up. Defects are added with exact sizes, so every
expected number is known before the code runs.
"""
import ast
import copy
import math
import os

import pytest

import mocap_evaluate as me

FPS, FPS_SRC, FIRST, LAST = 30, 120, 1, 241
STEP = FPS_SRC // FPS
PLANT_Z, SWING_Z = 0.2, 8.0
# left foot planted on [1, 61] and [121, 181]; right on [61, 121] and [181, 241]
CONTACTS = [
    {'foot': 'l', 'landmark': 'both', 'source_frames': [1, 61]},
    {'foot': 'r', 'landmark': 'both', 'source_frames': [61, 121]},
    {'foot': 'l', 'landmark': 'both', 'source_frames': [121, 181]},
    {'foot': 'r', 'landmark': 'both', 'source_frames': [181, 241]},
]
LABELS = {'clip': 'walk', 'labelled_by': 'tester', 'contacts': CONTACTS}
BANDS = {'status': 'provisional', 'chosen_by': 'test fixture',
         'bands': {'slide_cm': [0.0, 1.5], 'ground_min_cm': [-0.5, 1.0], 'ground_max_cm': [-0.5, 2.0],
                   'sampler_parity_cm': [0.0, 0.1]},
         'tolerances': {'clearance_cm': 0.01, 'source_excess_cm': 0.5}}
BODIES = ['aurora', 'grux']
SHA = 'ab' * 32


def ref(name, revision=1):
    """A revision-scoped evidence reference, as every passed check and review cites."""
    return [{'path': 'B001/r%d/evaluate/%s' % (revision, name), 'sha256': SHA}]


def _check(name):
    return {'passed': True, 'evidence': ref('%s.json' % name)}


PASS_CHECKS_BODY = {n: _check(n) for n in ('boundary', 'static_bake', 'target_pose')}
PASS_CHECKS_SOURCE = {'viewer_pinned': _check('viewer_pinned')}
REVIEW_PASS = {'result': 'pass', 'reviewed_by': 'animator', 'reason': 'contacts read planted',
               'evidence': ref('contact_sheet.png')}


def rescope(obj, revision):
    """A copy of obj with every 'B001/r1/' evidence path moved to revision `revision`."""
    if isinstance(obj, dict):
        return {k: (v.replace('B001/r1/', 'B001/r%d/' % revision, 1) if k == 'path' and isinstance(v, str)
                    else rescope(v, revision)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rescope(x, revision) for x in obj]
    return obj


def _planted(foot, sf):
    for c in CONTACTS:
        a, b = c['source_frames']
        if c['foot'] == foot and a <= sf <= b:
            return (a, b)
    return None


def _base(foot, lm):
    return [0.0 if lm == 'heel' else 15.0, -10.0 if foot == 'l' else 10.0]


def make_take(role='body', body='aurora', slide=None, float_cm=None, drop=(), nan_at=None, checks=None,
              parity=True, ground=None):
    """slide: (foot, landmark, window, size_cm) a linear forward drift reaching size_cm at the window's last frame.
    float_cm: (foot, window, height) both landmarks of that foot sit `height` up during that window."""
    samples = []
    for k in range((LAST - FIRST) // STEP + 1):
        sf = FIRST + k * STEP
        if sf in drop:
            continue
        lms = {}
        for foot in me.FEET:
            lms[foot] = {}
            win = _planted(foot, sf)
            for lm in me.LANDMARKS:
                x, y = _base(foot, lm)
                z = PLANT_Z if win else SWING_Z
                if slide and slide[0] == foot and slide[1] in (lm, 'both') and win == tuple(slide[2]):
                    a, b = slide[2]
                    x += slide[3] * (sf - a) / (b - a)
                if float_cm and float_cm[0] == foot and win == tuple(float_cm[1]):
                    z = float_cm[2]
                if nan_at == (foot, lm, sf):
                    x = float('nan')
                lms[foot][lm] = {'position': [x, y, z], 'clearance_cm': z}
        samples.append({'t_s': (sf - FIRST) / FPS_SRC, 'source_frame': sf, 'target_frame': k,
                        'bones': {'foot_l': {'t': [0.0, -10.0, 9.0], 'q': [1, 0, 0, 0]},
                                  'foot_r': {'t': [0.0, 10.0, 9.0], 'q': [1, 0, 0, 0]}},
                        'landmarks': lms})
    take = {'body': body, 'clip': 'walk', 'role': role, 'fps': FPS, 'fps_source': FPS_SRC,
            'source_frame_first': FIRST, 'source_frame_last': LAST,
            'ground': ground or {'kind': 'flat', 'z_cm': 0.0},
            'foot_bones': {'l': 'foot_l', 'r': 'foot_r'}, 'samples': samples,
            'checks': copy.deepcopy(checks if checks is not None else
                                    (PASS_CHECKS_SOURCE if role == 'source' else PASS_CHECKS_BODY))}
    if role == 'body' and parity:
        take['parity'] = [{'source_frame': 41, 'foot': 'l', 'landmark': 'heel',
                           'sampled': [0.0, -10.0, 0.2], 'posed': [0.0, -10.0, 0.21]}]
    return take


def rows_of(rows, measurement, foot=None, landmark=None, window=None):
    return [r for r in rows if r['measurement'] == measurement and (foot is None or r['foot'] == foot)
            and (landmark is None or r['landmark'] == landmark)
            and (window is None or r['window_source_frames'] == list(window))]


def one(rows, *a, **k):
    hits = rows_of(rows, *a, **k)
    assert len(hits) == 1, hits
    return hits[0]


def record(source=None, aurora=None, grux=None, reviews=None, waivers=None, revision=1, labels=None,
           bodies=None, declared=(FIRST, LAST)):
    """One clip's record; every fixture's evidence is moved to `revision`, as a real revision's would be."""
    source = source if source is not None else make_take(role='source', body='source')
    if bodies is None:
        bodies = {'aurora': aurora if aurora is not None else make_take(body='aurora'),
                  'grux': grux if grux is not None else make_take(body='grux')}
    if reviews is None:
        reviews = {'aurora': REVIEW_PASS, 'grux': REVIEW_PASS}
    return me.evaluate_clip(rescope(source, revision), rescope(bodies, revision), labels or LABELS, BANDS,
                            reviews=rescope(reviews, revision), waivers=waivers, required_bodies=BODIES,
                            batch='B001', revision=revision, invocation_id='inv-%d' % revision,
                            declared_source_frames=list(declared) if declared is not None else None)


# ------------------------------------------------------------------------------------------------ metrics

@pytest.mark.parametrize('size', [2.0, 5.0, 10.0])
def test_known_slides_measured_within_five_percent(size):
    rows = me.measure_take(make_take(slide=('l', 'heel', (121, 181), size)), LABELS, BANDS)
    r = one(rows, 'slide', 'l', 'heel', (121, 181))
    assert r['coverage'] == 1.0
    assert abs(r['measured'] - size) <= 0.05 * size
    assert r['state'] == me.REJECTED                     # every injected size is above the 1.5 cm band
    assert r['evidence']['path_length_cm'] == pytest.approx(size)
    assert r['evidence']['duration_s'] == pytest.approx(60 / 120)
    # 15 equal steps of size/15 over 4 source frames each: peak speed size/15 / (4/120)
    assert r['evidence']['peak_speed_cm_s'] == pytest.approx(size / 15 / (4 / 120))
    # the ball was not moved, so its row reads 0
    assert one(rows, 'slide', 'l', 'ball', (121, 181))['measured'] == pytest.approx(0.0)


def test_zero_defect_with_full_coverage_reads_zero_and_accepts():
    rows = me.measure_take(make_take(), LABELS, BANDS)
    slides = rows_of(rows, 'slide')
    assert len(slides) == 8                               # 4 windows x 'both' = heel and ball rows each
    assert all(r['measured'] == 0.0 and r['coverage'] == 1.0 and r['state'] == me.ACCEPTED for r in slides)
    assert all(r['state'] == me.ACCEPTED for r in rows if r['required'])


def test_floating_foot_fails_ground_on_surface_clearance_not_bone_height():
    rows = me.measure_take(make_take(float_cm=('r', (61, 121), 6.0)), LABELS, BANDS)
    r = one(rows, 'ground_min', 'r', 'heel', (61, 121))
    assert r['measured'] == pytest.approx(6.0) and r['state'] == me.REJECTED
    assert one(rows, 'ground_max', 'r', 'heel', (61, 121))['state'] == me.REJECTED
    # the bone height is a diagnostic: 9 cm, reported, and it is not the measured value
    diag = r['evidence']['bone_height_diagnostic']
    assert diag == {'bone': 'foot_r', 'min_cm': 9.0, 'max_cm': 9.0}
    assert r['measured'] != diag['min_cm']
    # a planted foot at 0.2 cm passes, although its bone sits 9 cm up
    ok = one(rows, 'ground_min', 'l', 'heel', (1, 61))
    assert ok['measured'] == pytest.approx(PLANT_Z) and ok['state'] == me.ACCEPTED


def test_missing_contact_window_is_unmeasurable_never_zero():
    labels = dict(LABELS, contacts=CONTACTS + [{'foot': 'l', 'landmark': 'heel', 'source_frames': [300, 360]}])
    rows = me.measure_take(make_take(), labels, BANDS)
    for m in ('slide', 'ground_min', 'ground_max'):
        r = one(rows, m, 'l', 'heel', (300, 360))
        assert r['state'] == me.UNMEASURABLE and r['measured'] is None and r['coverage'] == 0.0
        assert r['evidence']['cause'] == 'coverage'


def test_gap_in_samples_is_unmeasurable_with_partial_coverage():
    rows = me.measure_take(make_take(drop=(129, 133)), LABELS, BANDS)
    r = one(rows, 'slide', 'l', 'heel', (121, 181))
    assert r['state'] == me.UNMEASURABLE and r['measured'] is None
    assert r['coverage'] == pytest.approx(14 / 16)        # 16 grid samples in [121, 181], two dropped
    assert r['evidence']['missing_count'] == 2
    assert one(rows, 'slide', 'l', 'heel', (1, 61))['state'] == me.ACCEPTED   # other windows unaffected


def test_nan_landmark_is_unmeasurable():
    rows = me.measure_take(make_take(nan_at=('l', 'ball', 41)), LABELS, BANDS)
    r = one(rows, 'slide', 'l', 'ball', (1, 61))
    assert r['state'] == me.UNMEASURABLE and r['measured'] is None
    assert r['evidence']['missing'][0]['cause'] == 'non_finite_position'


def test_timestamp_disagreeing_with_source_frame_is_invalid():
    take = make_take()
    take['samples'][3]['t_s'] += 0.5                      # source frame 13 claims the wrong time
    r = one(me.measure_take(take, LABELS, BANDS), 'slide', 'l', 'heel', (1, 61))
    assert r['state'] == me.UNMEASURABLE and r['coverage'] == pytest.approx(15 / 16)


def test_duplicated_source_frame_is_invalid():
    take = make_take()
    take['samples'].append(copy.deepcopy(take['samples'][2]))
    r = one(me.measure_take(take, LABELS, BANDS), 'slide', 'l', 'heel', (1, 61))
    assert r['state'] == me.UNMEASURABLE


def test_heel_then_ball_windows_read_no_slide():
    # heel planted on [1, 29] at x=0; then the heel lifts and the ball, planted on [33, 61] at x=15, takes the weight
    take = make_take()
    for s in take['samples']:
        sf = s['source_frame']
        if 33 <= sf <= 61:
            s['landmarks']['l']['heel'] = {'position': [0.0, -10.0, 4.0 + sf / 10], 'clearance_cm': 4.0 + sf / 10}
    labels = {'clip': 'walk', 'contacts': [{'foot': 'l', 'landmark': 'heel', 'source_frames': [1, 29]},
                                           {'foot': 'l', 'landmark': 'ball', 'source_frames': [33, 61]}]}
    rows = me.measure_take(take, labels, BANDS)
    assert one(rows, 'slide', 'l', 'heel', (1, 29))['measured'] == 0.0
    assert one(rows, 'slide', 'l', 'ball', (33, 61))['measured'] == 0.0
    assert not rows_of(rows, 'slide', 'l', 'heel', (33, 61))           # the lifting heel is not measured


def test_airborne_windows_are_reported_not_banded():
    labels = dict(LABELS, airborne=[{'source_frames': [65, 117]}])
    rows = me.measure_take(make_take(), labels, BANDS)
    air = rows_of(rows, 'airborne_min_clearance', 'l', 'heel')
    assert len(air) == 1 and air[0]['state'] == me.REPORTED and air[0]['band'] is None
    assert air[0]['required'] is False and air[0]['measured'] == SWING_Z


def test_landmarks_from_foot_local_offsets():
    # foot bone at (10, 20, 9) turned +90 deg about Z: local x -> world y, local y -> world -x
    q = [math.cos(math.pi / 4), 0, 0, math.sin(math.pi / 4)]
    take = make_take()
    for s in take['samples']:
        s['bones'] = {'foot_l': {'t': [10.0, 20.0, 9.0], 'q': q}, 'foot_r': {'t': [0.0, 0.0, 9.0], 'q': [1, 0, 0, 0]}}
    lmk = {'mesh_sha256': 'abc', 'l': {'foot_bone': 'foot_l', 'heel': [-5.0, 1.0, -8.5], 'ball': [12.0, 0.0, -8.8]},
           'r': {'foot_bone': 'foot_r', 'heel': [-5.0, 0.0, -8.5], 'ball': [12.0, 0.0, -8.8]}}
    out = me.attach_landmarks(take, lmk)
    heel = out['samples'][0]['landmarks']['l']['heel']
    # rotate (-5, 1, -8.5) by +90 about Z -> (-1, -5, -8.5); plus (10, 20, 9) -> (9, 15, 0.5)
    assert heel['position'] == pytest.approx([9.0, 15.0, 0.5])
    assert heel['clearance_cm'] == pytest.approx(0.5)
    assert out['landmark_source']['mesh_sha256'] == 'abc'
    assert take['samples'][0]['landmarks']['l']['heel']['position'] != heel['position']  # input not mutated


def test_missing_foot_bone_is_unmeasurable():
    take = make_take()
    for s in take['samples']:
        if s['source_frame'] == 45:
            del s['bones']['foot_l']
    lmk = {'l': {'foot_bone': 'foot_l', 'heel': [0, 0, -8.8], 'ball': [15, 0, -8.8]},
           'r': {'foot_bone': 'foot_r', 'heel': [0, 0, -8.8], 'ball': [15, 0, -8.8]}}
    rows = me.measure_take(me.attach_landmarks(take, lmk), LABELS, BANDS)
    r = one(rows, 'slide', 'l', 'heel', (1, 61))
    assert r['state'] == me.UNMEASURABLE
    assert r['evidence']['missing'][0]['cause'] == 'missing_bone:foot_l'


# ------------------------------------------------------------------------------------------------ bands

def test_bands_are_explicit_inputs_with_no_defaults():
    for key in me.BAND_KEYS:
        bad = copy.deepcopy(BANDS)
        del bad['bands'][key]
        with pytest.raises(me.EvaluationError, match=key):
            me.measure_take(make_take(), LABELS, bad)
    bad = copy.deepcopy(BANDS)
    bad['bands']['slide_cm'] = [2.0, 1.0]
    with pytest.raises(me.EvaluationError):
        me.measure_take(make_take(), LABELS, bad)
    for key in me.TOLERANCE_KEYS:
        bad = copy.deepcopy(BANDS)
        del bad['tolerances'][key]
        with pytest.raises(me.EvaluationError, match=key):
            me.measure_take(make_take(), LABELS, bad)


@pytest.mark.parametrize('band, size, state', [
    ([0.0, 1.5], 5.0, me.REJECTED), ([0.0, 6.0], 5.0, me.ACCEPTED),      # widen the band: the same slide passes
    ([0.0, 1.5], 1.0, me.ACCEPTED), ([0.0, 0.5], 1.0, me.REJECTED),      # narrow it: a small one fails
])
def test_the_verdict_follows_the_band_input_and_nothing_else(band, size, state):
    # no band is hidden in the checker: the same take changes verdict only because the band input changed
    bands = copy.deepcopy(BANDS)
    bands['bands']['slide_cm'] = band
    r = one(me.measure_take(make_take(slide=('l', 'heel', (121, 181), size)), LABELS, bands), 'slide', 'l', 'heel',
            (121, 181))
    assert r['measured'] == pytest.approx(size) and r['state'] == state and r['band'] == band


def test_every_row_carries_the_band_digest_and_a_band_change_moves_it():
    rows = me.measure_take(make_take(), LABELS, BANDS)
    d = me.digest(BANDS)
    assert all(r['band_digest'] == d for r in rows)
    looser = copy.deepcopy(BANDS)
    looser['bands']['slide_cm'] = [0.0, 3.0]
    rows2 = me.measure_take(make_take(slide=('l', 'heel', (1, 61), 2.0)), LABELS, looser)
    r = one(rows2, 'slide', 'l', 'heel', (1, 61))
    assert r['state'] == me.ACCEPTED and r['band_digest'] != d and r['band'] == [0.0, 3.0]


# ------------------------------------------------------------------------------------------------ decisions

def test_accepted_and_signed_off():
    rec = record()
    assert rec['decisions'] == {'source': me.ACCEPTED, 'aurora': me.ACCEPTED, 'grux': me.ACCEPTED}
    assert rec['sign_off'] == {'signed_off': True, 'blocked_by': []}
    assert rec['bands']['status'] == 'provisional' and rec['invocation_id'] == 'inv-1'


def test_rejection_is_a_record_not_an_exception():
    slide = ('l', 'heel', (121, 181), 5.0)
    rec = record(source=make_take(role='source', body='source', slide=slide),
                 aurora=make_take(body='aurora', slide=slide), grux=make_take(body='grux', slide=slide))
    assert rec['decisions'] == {'source': me.REJECTED, 'aurora': me.REJECTED, 'grux': me.REJECTED}
    assert rec['sign_off']['signed_off'] is False
    owners = {r['body']: r['fault_owner'] for r in rows_of(rec['rows'], 'slide', 'l', 'heel', (121, 181))}
    assert owners == {'source': 'source', 'aurora': 'source', 'grux': 'source'}
    assert all(r['fault_owner'] is None for r in rec['rows'] if r['state'] == me.ACCEPTED)


def test_review_fail_with_passing_metrics_rejects_and_blocks():
    rec = record(reviews={'aurora': REVIEW_PASS,
                          'grux': {'result': 'fail', 'reviewed_by': 'animator', 'reason': 'knees pop at 140',
                                   'fault_owner': 'retarget'}})
    assert all(r['state'] == me.ACCEPTED for r in rec['rows'] if r['required'])
    assert rec['decisions']['grux'] == me.REJECTED and rec['decisions']['aurora'] == me.ACCEPTED
    assert rec['sign_off']['signed_off'] is False
    assert 'grux:review:fail' in rec['sign_off']['blocked_by']
    assert rec['review_issues'] == [{'body': 'grux', 'kind': 'review', 'reviewed_by': 'animator',
                                     'observed': 'knees pop at 140', 'fault_owner': 'retarget', 'clip': None,
                                     'source_frames': None}]


def _fail(**kw):
    return dict({'result': 'fail', 'reviewed_by': 'animator', 'reason': 'left knee snaps'}, **kw)


def test_failed_review_carries_owner_clip_and_frames():
    rec = record(reviews={'aurora': REVIEW_PASS,
                          'grux': _fail(fault_owner='source', clip='walk', source_frames=[121, 181])})
    [rv] = [r for r in rec['reviews'] if r['body'] == 'grux']
    assert (rv['fault_owner'], rv['clip'], rv['source_frames']) == ('source', 'walk', [121, 181])
    [ri] = rec['review_issues']
    assert (ri['fault_owner'], ri['clip'], ri['source_frames']) == ('source', 'walk', [121, 181])
    passed = [r for r in rec['reviews'] if r['body'] == 'aurora'][0]
    assert passed['fault_owner'] is None and passed['source_frames'] is None


@pytest.mark.parametrize('review, match', [
    (_fail(), 'fault_owner'),                                                    # a fail must name its owner
    (_fail(fault_owner='vendor'), 'fault_owner'),                                # not one of the owners
    (dict(REVIEW_PASS, fault_owner='source'), 'passed review'),                  # a pass names none
    (_fail(fault_owner='source', clip='walk'), 'together'),                      # clip without frames
    (_fail(fault_owner='source', source_frames=[1, 61]), 'together'),            # frames without clip
    (_fail(fault_owner='source', clip='run', source_frames=[1, 61]), 'not the evaluated clip'),
    (_fail(fault_owner='source', clip='walk', source_frames=[200, 300]), 'outside the take'),   # take ends at 241
    (_fail(fault_owner='source', clip='walk', source_frames=[0, 61]), 'outside the take'),      # take starts at 1
    (_fail(fault_owner='source', clip='walk', source_frames=[61, 1]), 'source_frames'),
])
def test_failed_review_fields_are_validated(review, match):
    with pytest.raises(me.EvaluationError, match=match):
        record(reviews={'aurora': REVIEW_PASS, 'grux': review})


def test_review_frames_need_a_sampled_take():
    with pytest.raises(me.EvaluationError, match='no sampled take'):
        record(bodies={'aurora': make_take(), 'grux': None},
               reviews={'grux': _fail(fault_owner='source', clip='walk', source_frames=[1, 61])})


def test_a_source_owned_review_fail_must_name_its_frames():
    with pytest.raises(me.EvaluationError, match='source_frames'):
        record(reviews={'aurora': REVIEW_PASS, 'grux': _fail(fault_owner='source')})


@pytest.mark.parametrize('evidence', [None, [], [{'path': 'B001/r2/x.png', 'sha256': SHA}],
                                      [{'path': 'elsewhere/x.png', 'sha256': SHA}],
                                      [{'path': 'B001/r1/x.png', 'sha256': 'abc'}], ['B001/r1/x.png']])
def test_a_passed_review_cites_revision_scoped_evidence(evidence):
    review = dict(REVIEW_PASS, evidence=evidence)
    with pytest.raises(me.EvaluationError, match='evidence'):
        record(reviews={'aurora': review, 'grux': REVIEW_PASS})


@pytest.mark.parametrize('check', [True, {'passed': True}, {'passed': True, 'evidence': []},
                                   {'passed': 'yes', 'evidence': ref('b.json')},
                                   {'passed': True, 'evidence': [{'path': 'B001/r9/b.json', 'sha256': SHA}]}])
def test_a_passed_check_cites_evidence_and_a_bare_boolean_is_refused(check):
    checks = dict(PASS_CHECKS_BODY, boundary=check)
    with pytest.raises(me.EvaluationError):
        record(aurora=make_take(body='aurora', checks=checks))


@pytest.mark.parametrize('kw, match', [
    (dict(invocation_id=None), 'invocation_id'), (dict(invocation_id=' '), 'invocation_id'),
    (dict(batch=None), 'batch'), (dict(revision=0), 'revision'),
    (dict(required_bodies=[]), 'required_bodies'), (dict(required_bodies=['aurora']), 'not required'),
])
def test_the_run_and_its_required_bodies_are_explicit(kw, match):
    args = dict(required_bodies=BODIES, batch='B001', revision=1, invocation_id='inv-1',
                declared_source_frames=[FIRST, LAST])
    args.update(kw)
    with pytest.raises(me.EvaluationError, match=match):
        me.evaluate_clip(make_take(role='source', body='source'), {'aurora': make_take(), 'grux': make_take()},
                         LABELS, BANDS, reviews={'aurora': REVIEW_PASS, 'grux': REVIEW_PASS}, **args)


def test_a_required_body_left_out_is_not_sampled_and_blocks():
    rec = record(bodies={'aurora': make_take()}, reviews={'aurora': REVIEW_PASS})
    assert rec['decisions']['grux'] == me.UNMEASURABLE and rec['required_bodies'] == BODIES
    assert one([r for r in rec['rows'] if r['body'] == 'grux'], 'sampled')['evidence']['cause'] == 'body_not_supplied'
    assert rec['sign_off']['signed_off'] is False


def test_labels_without_contacts_accept_nothing():
    rec = record(labels={'clip': 'walk', 'contacts': []})
    assert one([r for r in rec['rows'] if r['body'] == 'source'], 'labels')['state'] == me.UNMEASURABLE
    assert set(rec['decisions'].values()) == {me.UNMEASURABLE}
    assert rec['sign_off']['signed_off'] is False and 'labels:no_contacts' in rec['sign_off']['blocked_by']


def test_a_source_without_samples_blocks_sign_off():
    src = make_take(role='source', body='source')
    src['samples'] = []
    rec = record(source=src)
    assert rec['decisions']['source'] == me.UNMEASURABLE and rec['sign_off']['signed_off'] is False


def test_a_source_slide_blocks_sign_off_even_when_every_body_passes():
    rec = record(source=make_take(role='source', body='source', slide=('l', 'heel', (1, 61), 5.0)))
    assert rec['decisions'] == {'source': me.REJECTED, 'aurora': me.ACCEPTED, 'grux': me.ACCEPTED}
    assert rec['sign_off']['signed_off'] is False and 'source:decision:rejected' in rec['sign_off']['blocked_by']


def test_missing_review_is_unmeasurable_and_blocks():
    rec = record(reviews={'aurora': REVIEW_PASS})
    assert rec['decisions']['grux'] == me.UNMEASURABLE
    assert 'grux:review:missing' in rec['sign_off']['blocked_by']


def test_review_needs_a_reason():
    with pytest.raises(me.EvaluationError):
        record(reviews={'aurora': {'result': 'fail', 'reviewed_by': 'animator', 'reason': ''}, 'grux': REVIEW_PASS})


def test_waiver_counts_for_sign_off_and_stays_visible():
    slide = ('r', 'ball', (61, 121), 2.0)
    aurora = make_take(body='aurora', slide=slide)
    row_id = one(me.measure_take(aurora, LABELS, BANDS, subject='aurora'), 'slide', 'r', 'ball', (61, 121))['id']
    rec = record(aurora=aurora, waivers=[{'body': 'aurora', 'row_id': row_id, 'approved_by': 'lead',
                                          'reason': 'ball hidden by the cape in every shot'}])
    r = one([x for x in rec['rows'] if x['body'] == 'aurora'], 'slide', 'r', 'ball', (61, 121))
    assert r['state'] == me.WAIVED and r['evidence']['waiver']['known_failure'] is True
    assert r['measured'] == pytest.approx(2.0)
    assert r['fault_owner'] == 'body_limitation'          # source clean, one body fails, target pose passed
    assert rec['decisions']['aurora'] == me.WAIVED
    assert rec['sign_off']['signed_off'] is True and len(rec['waivers']) == 1


def test_waiver_cannot_turn_unmeasurable_into_a_pass():
    aurora = make_take(body='aurora', drop=(65,))
    row_id = 'aurora:slide:r:heel:61-121'
    rec = record(aurora=aurora, waivers=[{'body': 'aurora', 'row_id': row_id, 'approved_by': 'lead',
                                          'reason': 'try'}])
    assert rec['decisions']['aurora'] == me.UNMEASURABLE
    assert rec['waivers'] == [] and len(rec['unmatched_waivers']) == 1


def test_waiver_without_approver_raises():
    with pytest.raises(me.EvaluationError):
        record(waivers=[{'body': 'aurora', 'row_id': 'x', 'approved_by': '', 'reason': 'r'}])


def test_unsupported_ground():
    rec = record(grux=make_take(body='grux', ground={'kind': 'stairs'}))
    assert rec['decisions']['grux'] == me.UNSUPPORTED
    assert one([r for r in rec['rows'] if r['body'] == 'grux'], 'scope')['state'] == me.UNSUPPORTED


def test_rejected_outranks_unmeasurable():
    aurora = make_take(body='aurora', slide=('l', 'heel', (1, 61), 5.0), drop=(125,))
    rec = record(aurora=aurora)
    assert rec['decisions']['aurora'] == me.REJECTED


def test_body_not_sampled_is_unmeasurable():
    rec = record(bodies={'aurora': make_take(), 'grux': None}, reviews={'aurora': REVIEW_PASS})
    assert rec['decisions']['grux'] == me.UNMEASURABLE and rec['sign_off']['signed_off'] is False


def test_unpinned_viewer_blocks_sign_off():
    rec = record(source=make_take(role='source', body='source', checks={'viewer_pinned': {'passed': False}}))
    assert rec['decisions']['source'] == me.UNMEASURABLE
    assert 'source:check:viewer_pinned' in rec['sign_off']['blocked_by']
    assert one(rec['rows'], 'check:viewer_pinned')['fault_owner'] == 'conversion'


def test_missing_infrastructure_check_is_unmeasurable():
    checks = {k: v for k, v in PASS_CHECKS_BODY.items() if k != 'target_pose'}
    rec = record(aurora=make_take(body='aurora', checks=checks))
    assert rec['decisions']['aurora'] == me.UNMEASURABLE


# ------------------------------------------------------------------------------------------------ ownership

def test_clean_source_both_bodies_fail_boundary_decides():
    slide = ('l', 'heel', (1, 61), 5.0)
    rec = record(aurora=make_take(body='aurora', slide=slide), grux=make_take(body='grux', slide=slide))
    owners = {r['body']: r['fault_owner'] for r in rows_of(rec['rows'], 'slide', 'l', 'heel', (1, 61))}
    assert owners == {'source': None, 'aurora': 'retarget', 'grux': 'retarget'}
    bad = dict(PASS_CHECKS_BODY, boundary={'passed': False})
    rec = record(aurora=make_take(body='aurora', slide=slide, checks=bad),
                 grux=make_take(body='grux', slide=slide, checks=bad))
    owners = {r['body']: r['fault_owner'] for r in rows_of(rec['rows'], 'slide', 'l', 'heel', (1, 61))}
    assert owners['aurora'] == 'conversion' and owners['grux'] == 'conversion'


def test_one_body_only_is_not_sent_to_the_vendor():
    rec = record(grux=make_take(body='grux', slide=('l', 'heel', (1, 61), 10.0)))
    r = one([x for x in rec['rows'] if x['body'] == 'grux'], 'slide', 'l', 'heel', (1, 61))
    assert r['state'] == me.REJECTED and r['fault_owner'] == 'body_limitation'
    bad = dict(PASS_CHECKS_BODY, target_pose={'passed': False})
    rec = record(grux=make_take(body='grux', slide=('l', 'heel', (1, 61), 10.0), checks=bad))
    r = one([x for x in rec['rows'] if x['body'] == 'grux'], 'slide', 'l', 'heel', (1, 61))
    assert r['fault_owner'] == 'retarget'
    assert not [x for x in rec['rows'] if x['fault_owner'] == 'source']


def test_sampler_disagreement_makes_only_that_body_unmeasurable():
    grux = make_take(body='grux', slide=('l', 'heel', (1, 61), 5.0))
    grux['parity'][0]['posed'] = [0.0, -10.0, 1.2]        # 1 cm apart, band 0.1
    rec = record(grux=grux)
    grux_rows = [r for r in rec['rows'] if r['body'] == 'grux']
    assert one(grux_rows, 'sampler_parity')['state'] == me.UNMEASURABLE
    assert one(grux_rows, 'sampler_parity')['fault_owner'] == 'runtime'
    r = one(grux_rows, 'slide', 'l', 'heel', (1, 61))
    assert r['state'] == me.UNMEASURABLE and r['evidence']['unmeasured_state'] == me.REJECTED
    assert rec['decisions']['grux'] == me.UNMEASURABLE and rec['decisions']['aurora'] == me.ACCEPTED


def test_missing_parity_is_unmeasurable():
    rec = record(aurora=make_take(body='aurora', parity=False))
    assert rec['decisions']['aurora'] == me.UNMEASURABLE


def test_body_gap_with_clean_source_is_owned_by_runtime():
    rec = record(aurora=make_take(body='aurora', drop=(9,)))
    r = one([x for x in rec['rows'] if x['body'] == 'aurora'], 'slide', 'l', 'heel', (1, 61))
    assert r['state'] == me.UNMEASURABLE and r['fault_owner'] == 'runtime'


def test_every_non_accepted_required_row_has_an_owner():
    rec = record(source=make_take(role='source', body='source', drop=(201,)),
                 aurora=make_take(body='aurora', slide=('l', 'ball', (121, 181), 3.0)),
                 grux=make_take(body='grux', float_cm=('l', (1, 61), 4.0), parity=False))
    for r in rec['rows']:
        if r['required'] and r['state'] != me.ACCEPTED:
            assert r['fault_owner'] in me.FAULT_OWNERS, r


def _src_slide(size=5.0, **kw):
    return make_take(role='source', body='source', slide=('l', 'heel', (1, 61), size), **kw)


def _owner(rec, body):
    return one([x for x in rec['rows'] if x['body'] == body], 'slide', 'l', 'heel', (1, 61))['fault_owner']


@pytest.mark.parametrize('viewer, owner', [({'passed': False}, 'conversion'), (None, 'uncertain')])
def test_a_source_row_is_the_source_s_only_when_the_viewer_is_pinned(viewer, owner):
    checks = {'viewer_pinned': viewer} if viewer is not None else {}
    slide = ('l', 'heel', (1, 61), 5.0)
    rec = record(source=_src_slide(checks=checks), aurora=make_take(slide=slide),
                 grux=make_take(body='grux', slide=slide))
    assert {_owner(rec, b) for b in ('source', 'aurora', 'grux')} == {owner}
    assert not [r for r in rec['rows'] if r['fault_owner'] == 'source']


def test_missing_target_samples_are_runtime_even_under_a_source_failure():
    slide = ('l', 'heel', (1, 61), 5.0)
    rec = record(source=_src_slide(), aurora=make_take(slide=slide, drop=(9,)),
                 grux=make_take(body='grux', slide=slide))
    assert _owner(rec, 'aurora') == 'runtime' and _owner(rec, 'grux') == 'source'


def test_a_target_error_much_larger_than_the_source_s_keeps_its_own_owner():
    # source 2 cm (0.5 outside the band), aurora 10 cm (8.5 outside): 8 cm apart, more than the 0.5 cm tolerance
    rec = record(source=_src_slide(2.0), aurora=make_take(slide=('l', 'heel', (1, 61), 10.0)))
    assert _owner(rec, 'source') == 'source'
    assert _owner(rec, 'aurora') == 'body_limitation'     # target pose passed, the one body out of line
    rec = record(source=_src_slide(2.0), aurora=make_take(slide=('l', 'heel', (1, 61), 2.4)))
    assert _owner(rec, 'aurora') == 'source'              # 0.4 cm past the source's error: within the tolerance


def test_a_body_failing_the_other_bound_keeps_its_own_owner():
    # the source floats 2 cm (1 cm above the band); aurora penetrates 1.5 cm (1 cm below it): equal excess, other
    # defect
    rec = record(source=make_take(role='source', body='source', float_cm=('l', (1, 61), 2.0)),
                 aurora=make_take(float_cm=('l', (1, 61), -1.5)))
    owners = {r['body']: r['fault_owner'] for r in rows_of(rec['rows'], 'ground_min', 'l', 'heel', (1, 61))}
    assert owners == {'source': 'source', 'aurora': 'body_limitation', 'grux': None}


def test_a_body_whose_boundary_check_failed_keeps_its_own_owner():
    bad = dict(PASS_CHECKS_BODY, boundary={'passed': False})
    rec = record(source=_src_slide(), aurora=make_take(slide=('l', 'heel', (1, 61), 5.0), checks=bad),
                 grux=make_take(body='grux', slide=('l', 'heel', (1, 61), 5.0)))
    assert _owner(rec, 'aurora') == 'conversion' and _owner(rec, 'grux') == 'source'


# ------------------------------------------------------------------------------------------------ time contract

def _short_take(samples_at, last, flag_last=True):
    """30 Hz from 120 Hz, source frames 5..last: grid frames 5, 9, ...; a heel moving 10 cm over the take."""
    samples = []
    for sf in samples_at:
        x = 10.0 * (sf - 5) / (last - 5)
        lm = {'position': [x, 0.0, 0.0], 'clearance_cm': 0.0}
        s = {'source_frame': sf, 't_s': (sf - 5) / 120, 'landmarks': {'l': {'heel': lm, 'ball': lm}}}
        if flag_last and sf == last and (sf - 5) % 4:
            s['end_sample'] = True
        samples.append(s)
    return {'body': 'source', 'role': 'source', 'clip': 'walk', 'fps': 30, 'fps_source': 120,
            'source_frame_first': 5, 'source_frame_last': last, 'ground': {'kind': 'flat', 'z_cm': 0.0},
            'samples': samples, 'checks': PASS_CHECKS_SOURCE}


SHORT = {'clip': 'walk', 'contacts': [{'foot': 'l', 'landmark': 'heel', 'source_frames': [5, 8]}]}


def test_the_flagged_off_grid_end_sample_is_measured():
    r = one(me.measure_take(_short_take([5, 8], 8), SHORT, BANDS), 'slide', 'l', 'heel')
    assert r['coverage'] == 1.0 and r['evidence']['expected_samples'] == 2
    assert r['measured'] == pytest.approx(10.0) and r['state'] == me.REJECTED
    assert r['evidence']['duration_s'] == pytest.approx(3 / 120)


def test_a_declared_end_sample_that_is_missing_is_unmeasurable_never_zero():
    r = one(me.measure_take(_short_take([5], 8), SHORT, BANDS), 'slide', 'l', 'heel')
    assert r['state'] == me.UNMEASURABLE and r['measured'] is None and r['coverage'] == pytest.approx(0.5)


@pytest.mark.parametrize('mutate', [
    lambda t: t['samples'][1].pop('end_sample'),                       # off grid and not flagged
    lambda t: t.update(source_frame_last=9),                           # flagged, but not the declared end
    lambda t: t['samples'][0].update(end_sample=True),                 # a grid sample flagged as the end
    lambda t: t['samples'][1].update(end_sample='yes'),
])
def test_an_end_sample_outside_the_time_contract_is_invalid(mutate):
    take = _short_take([5, 8], 8)
    mutate(take)
    r = one(me.measure_take(take, SHORT, BANDS), 'slide', 'l', 'heel')
    assert r['state'] == me.UNMEASURABLE and r['measured'] is None


def test_a_window_with_one_sample_cannot_show_a_slide():
    labels = {'clip': 'walk', 'contacts': [{'foot': 'l', 'landmark': 'heel', 'source_frames': [1, 3]}]}
    rows = me.measure_take(make_take(), labels, BANDS)
    r = one(rows, 'slide', 'l', 'heel', (1, 3))
    assert r['state'] == me.UNMEASURABLE and r['measured'] is None and r['evidence']['cause'] == 'single_sample'
    assert one(rows, 'ground_min', 'l', 'heel', (1, 3))['state'] == me.ACCEPTED


def _truncated(take, last=181):
    """The take cut short at source frame `last`, declaring so: everything it says about itself is consistent."""
    take['samples'] = [s for s in take['samples'] if s['source_frame'] <= last]
    take['source_frame_last'] = last
    return take


def test_a_take_shorter_than_the_declared_range_is_unmeasurable_and_blocks():
    labels = dict(LABELS, contacts=CONTACTS[:3])           # every labelled window still inside the truncated take
    rec = record(aurora=_truncated(make_take()), labels=labels)
    span = one([r for r in rec['rows'] if r['body'] == 'aurora'], 'span')
    assert span['state'] == me.UNMEASURABLE and span['evidence']['declared_source_frames'] == [FIRST, LAST]
    assert span['coverage'] < 1.0 and span['fault_owner'] == 'runtime'
    assert rec['decisions']['aurora'] == me.UNMEASURABLE and rec['sign_off']['signed_off'] is False
    assert rec['declared_source_frames'] == [FIRST, LAST]
    # a truncated source blocks too
    rec = record(source=_truncated(make_take(role='source', body='source')), labels=labels)
    assert one([r for r in rec['rows'] if r['body'] == 'source'], 'span')['state'] == me.UNMEASURABLE
    assert rec['sign_off']['signed_off'] is False
    # the full take covers the declared range
    rec = record(labels=labels)
    assert {r['state'] for r in rec['rows'] if r['measurement'] == 'span'} == {me.ACCEPTED}
    assert rec['sign_off']['signed_off'] is True


def test_the_declared_range_is_required():
    with pytest.raises(me.EvaluationError, match='declared_source_frames'):
        record(declared=None)


def test_source_frame_last_is_required():
    take = make_take()
    del take['source_frame_last']
    with pytest.raises(me.EvaluationError, match='source_frame_last'):
        me.measure_take(take, LABELS, BANDS)


# ------------------------------------------------------------------------------------------------ landmarks

def test_the_ball_follows_its_own_bone():
    # the toes bend: the ball bone is 5 cm up and 15 cm forward of the foot bone, the ball 4.8 cm under it
    take = make_take()
    for s in take['samples']:
        s['bones']['ball_l'] = {'t': [15.0, -10.0, 5.0], 'q': [1, 0, 0, 0]}
    lmk = {'l': {'foot_bone': 'foot_l', 'ball_bone': 'ball_l', 'heel': [0, 0, -8.8], 'ball': [0, 0, -4.8]},
           'r': {'foot_bone': 'foot_r', 'heel': [0, 0, -8.8], 'ball': [15, 0, -8.8]}}
    out = me.attach_landmarks(take, lmk)
    assert out['samples'][0]['landmarks']['l']['ball']['position'] == pytest.approx([15.0, -10.0, 0.2])
    assert out['samples'][0]['landmarks']['r']['ball']['position'] == pytest.approx([15.0, 10.0, 0.2])
    for s in take['samples']:
        del s['bones']['ball_l']
    rows = me.measure_take(me.attach_landmarks(take, lmk), LABELS, BANDS)
    r = one(rows, 'slide', 'l', 'ball', (1, 61))
    assert r['state'] == me.UNMEASURABLE and r['evidence']['missing'][0]['cause'] == 'missing_bone:ball_l'


def test_a_clearance_that_contradicts_the_position_is_unmeasurable():
    take = make_take()
    for s in take['samples']:
        s['landmarks']['l']['heel']['position'][2] = -10.0         # 10 cm underground, clearance still says 0.2
    r = one(me.measure_take(take, LABELS, BANDS), 'ground_min', 'l', 'heel', (1, 61))
    assert r['state'] == me.UNMEASURABLE and r['evidence']['missing'][0]['cause'] == 'clearance_disagrees'
    take = make_take(ground={'kind': 'flat', 'z_cm': 0.005})       # within the 0.01 cm tolerance
    assert one(me.measure_take(take, LABELS, BANDS), 'ground_min', 'l', 'heel', (1, 61))['state'] == me.ACCEPTED


# ------------------------------------------------------------------------------------------------ separation

def test_checker_shares_no_code_with_the_writer():
    tree = ast.parse(open(me.__file__, encoding='utf-8').read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or '').split('.')[0])
    assert names <= {'hashlib', 'json', 'math', 're'}, names
    here = os.path.dirname(os.path.abspath(me.__file__))
    assert os.path.exists(os.path.join(here, 'acclaim.py'))       # the writer exists and is still not imported
