"""mocap_feedback: stage 5 of the intake (docs/specs/2026-11-16-mocap-intake-design.md, sections 8 and 9).

Every evaluation record here is made by mocap_evaluate.evaluate_clip from the synthetic takes in test_mocap_evaluate,
so the shape this stage reads is the shape the evaluate stage writes and cannot drift from it."""
import copy
import json
import os

import pytest

import mocap_evaluate as me
import mocap_feedback as fb
import test_mocap_evaluate as te

WINDOW = (1, 61)
SLIDE = ('l', 'heel', WINDOW, 5.0)
KEY = fb.issue_key('walk', 'slide', 'l', 'heel', WINDOW)


def _record(revision=1, source=None, aurora=None, grux=None, reviews=None, waivers=None, labels=None, bodies=None):
    return te.record(source=source, aurora=aurora, grux=grux, reviews=reviews, waivers=waivers, revision=revision,
                     labels=labels, bodies=bodies)


def _accept(rec, evs, inventory=('walk',), bodies=te.BODIES, **kw):
    args = dict(approver='lead', verdict='accept', reason='x', on='2026-11-16', labels={'walk': te.LABELS})
    args.update(kw)
    clips = [{'id': c, 'source_frames': [te.FIRST, te.LAST]} if isinstance(c, str) else c for c in inventory]
    return fb.sign_off(rec, evs, inventory=clips, required_bodies=list(bodies), **args)


def _source_slide(revision=1, **kw):
    """The disclosed left-heel slide: in the delivered source, so on both bodies too."""
    return _record(revision, source=te.make_take(role='source', body='source', slide=SLIDE),
                   aurora=kw.pop('aurora', te.make_take(body='aurora', slide=SLIDE)),
                   grux=kw.pop('grux', te.make_take(body='grux', slide=SLIDE)), **kw)


def _grux_only(revision=1, **kw):
    return _record(revision, grux=te.make_take(body='grux', slide=SLIDE), **kw)


def _row_id(ev, body, measurement='slide', foot='l', landmark='heel', window=WINDOW):
    [r] = [r for r in ev['rows'] if r['body'] == body and r['measurement'] == measurement and r['foot'] == foot
           and r['landmark'] == landmark and r['window_source_frames'] == list(window)]
    return r['id']


# --------------------------------------------------------------------------------------------- the evaluation record
def test_a_real_evaluation_record_validates():
    for ev in (_record(), _source_slide(), _grux_only()):
        assert fb.validate_evaluation(ev) is ev


@pytest.mark.parametrize('mutate, match', [
    (lambda e: e.pop('reviews'), 'missing'),
    (lambda e: e.pop('decisions'), 'missing'),
    (lambda e: e.update(kind='other'), 'mocap_evaluation'),
    (lambda e: e.update(batch=None), 'names its batch'),
    (lambda e: e.update(revision=None), 'names its revision'),
    (lambda e: e.update(exchange='real'), 'exchange'),
    (lambda e: e['decisions'].pop('source'), 'source'),
    (lambda e: e['decisions'].update(aurora='passed'), 'not a state'),
    (lambda e: e['reviews'].pop(), 'review is mandatory'),
    (lambda e: e['reviews'][0].update(reason=''), 'who reviewed it and why'),
    (lambda e: e['rows'][0].pop('fault_owner'), 'missing'),
    (lambda e: e['rows'][0].update(evidence=[]), 'evidence'),
    (lambda e: e['sign_off'].update(signed_off=True), 'contradicts'),
])
def test_an_evaluation_outside_the_record_shape_is_refused(mutate, match):
    ev = _source_slide()
    mutate(ev)
    with pytest.raises(fb.FeedbackError, match=match):
        fb.validate_evaluation(ev)


def test_a_failing_row_without_an_owner_or_above_its_decision_is_refused():
    ev = _source_slide()
    rejected = next(r for r in ev['rows'] if r['state'] == me.REJECTED)
    rejected['fault_owner'] = None
    with pytest.raises(fb.FeedbackError, match='names no fault_owner'):
        fb.validate_evaluation(ev)
    ev = _source_slide()
    ev['decisions']['source'] = me.ACCEPTED
    with pytest.raises(fb.FeedbackError, match='at least its worst row'):
        fb.validate_evaluation(ev)


def test_the_revision_decision_is_the_worst_of_every_decision():
    assert fb.overall_decision(_record()) == me.ACCEPTED
    assert fb.overall_decision(_source_slide()) == me.REJECTED
    assert fb.overall_decision(_record(reviews={'aurora': te.REVIEW_PASS})) == me.UNMEASURABLE


# ------------------------------------------------------------------------------------------------------ vendor brief
def test_a_source_slide_becomes_one_vendor_issue_with_everything_the_spec_requires():
    ev = _source_slide()
    rec = fb.feedback(ev)
    assert rec['exchange'] == 'simulated' and rec['decision'] == 'rejected' and rec['send_to_vendor']
    [issue] = rec['issues']
    assert issue['id'] == 'B001-r1-1' and issue['key'] == KEY and issue['fault_owner'] == 'source'
    assert issue['source_frames'] == [1, 61] and issue['bodies'] == ['aurora', 'grux']
    assert issue['measured_on'] == ['source', 'aurora', 'grux']
    assert issue['measured_cm'] == pytest.approx(5.0) and issue['band_cm'] == [0.0, 1.5]
    cite = 'evaluation:%s#source/%s' % (fb.digest(ev)[:16], _row_id(ev, 'source'))
    sheet = 'B001/r1/evaluate/contact_sheet.png@sha256:%s' % te.SHA       # the bodies' review evidence, hashed
    assert cite in issue['evidence'] and sheet in issue['evidence'] and len(issue['evidence']) == 4
    assert issue['observed'].startswith('left heel slide over source frames 1-61, band [0, 1.5] cm: source 5 cm')
    assert issue['hypothesis'] is None
    assert issue['requested_action'] == 'remove the drift of the left heel plant, source frames 1-61'
    assert 'timing of every other contact' in issue['do_not_change'] and 'the right foot' in issue['do_not_change']
    assert issue['status'] == 'open'


def test_a_hypothesis_is_carried_only_as_a_hypothesis_and_printed_as_one():
    rec = fb.feedback(_source_slide(), notes={KEY: {'hypothesis': 'a marker swap on the left heel'}})
    issue = rec['issues'][0]
    assert issue['hypothesis'] == 'a marker swap on the left heel' and 'marker' not in issue['observed']
    assert '<em>Hypothesis, not measured:</em> a marker swap' in fb.render_page(rec)


def test_a_failure_on_one_body_only_is_withheld_not_sent():
    ev = _grux_only()
    assert ev['decisions']['grux'] == me.REJECTED
    rec = fb.feedback(ev)
    assert rec['decision'] == 'rejected' and rec['issues'] == [] and not rec['send_to_vendor']
    assert {(w['body'], w['fault_owner']) for w in rec['withheld']} == {('grux', 'body_limitation')}


def test_a_body_row_unmeasurable_under_a_source_failure_is_the_sampler_s_and_never_reads_as_zero():
    # missing target samples are a runtime fault, not the vendor's, even when the source fails the same window
    ev = _source_slide(aurora=te.make_take(body='aurora', slide=SLIDE, drop=(9,)))
    rec = fb.feedback(ev)
    issue = next(i for i in rec['issues'] if i['key'] == KEY)
    assert issue['measured_on'] == ['source', 'grux']
    assert 'aurora' not in issue['observed']
    [w] = [w for w in rec['withheld'] if w['body'] == 'aurora' and w['row'].startswith('aurora:slide:l:heel:1-61')]
    assert w['fault_owner'] == 'runtime' and w['measured'] is None


def test_a_waiver_stays_visible_is_not_sent_and_can_be_signed_off():
    probe = _grux_only()
    waiver = {'body': 'grux', 'row_id': _row_id(probe, 'grux'), 'approved_by': 'lead', 'reason': 'hoof contact'}
    ev = _grux_only(waivers=[waiver])
    rec = fb.feedback(ev)
    assert rec['decision'] == 'waived' and rec['issues'] == []
    assert rec['waived'][0]['by'] == 'lead' and rec['waived'][0]['reason'] == 'hoof contact'
    assert 'hoof contact' in fb.render_page(rec)
    s = _accept(rec, ev, reason='known hoof')
    assert s['waived'] and s['clips']['walk']['bodies']['grux']['decision'] == 'waived'


def test_a_failed_review_with_passing_metrics_rejects_but_reaches_no_vendor():
    fail = {'result': 'fail', 'reviewed_by': 'animator', 'reason': 'wrist reads broken', 'fault_owner': 'retarget'}
    rec = fb.feedback(_record(reviews={'aurora': fail, 'grux': te.REVIEW_PASS}))
    assert rec['decision'] == 'rejected' and rec['issues'] == []
    [w] = [w for w in rec['withheld'] if w['kind'] == 'review']
    assert w['body'] == 'aurora' and w['cause'] == 'wrist reads broken' and w['fault_owner'] == 'retarget'


def _review_fail(owner='source', frames=(121, 181), reason='left knee snaps through the plant'):
    rv = {'result': 'fail', 'reviewed_by': 'animator', 'reason': reason, 'fault_owner': owner}
    if frames is not None:
        rv.update(clip='walk', source_frames=list(frames))
    return rv


def test_a_source_owned_review_fail_with_frames_becomes_a_review_issue():
    rec = fb.feedback(_record(reviews={'aurora': _review_fail(), 'grux': _review_fail(reason='knee pops')}))
    assert rec['decision'] == 'rejected' and rec['send_to_vendor'] is True
    [issue] = rec['issues']
    assert issue['kind'] == 'review' and issue['fault_owner'] == 'source' and issue['id'] == 'B001-r1-1'
    assert issue['source_frames'] == [121, 181] and issue['bodies'] == ['aurora', 'grux']
    assert issue['measured_cm'] is None and issue['band_cm'] is None          # nothing measured, nothing invented
    assert 'left knee snaps' in issue['observed'] and 'knee pops' in issue['observed']
    assert any(e.endswith('#review:aurora') for e in issue['evidence'])
    assert issue['do_not_change'] and issue['requested_action']
    assert not [w for w in rec['withheld'] if w['kind'] == 'review']
    fb.validate_brief(rec)
    assert 'B001-r1-1' in fb.render_page(rec)


@pytest.mark.parametrize('review', [_review_fail(owner='retarget'), _review_fail(owner='retarget', frames=None),
                                    _review_fail(owner='body_limitation')])
def test_a_review_fail_not_owned_by_the_source_or_without_frames_is_withheld(review):
    rec = fb.feedback(_record(reviews={'aurora': review, 'grux': te.REVIEW_PASS}))
    assert rec['decision'] == 'rejected' and rec['issues'] == []
    [w] = [w for w in rec['withheld'] if w['kind'] == 'review']
    assert w['fault_owner'] == review['fault_owner']


@pytest.mark.parametrize('mutate, match', [
    (lambda rv: rv.update(fault_owner=None), 'names its fault_owner'),
    (lambda rv: rv.update(fault_owner='vendor'), 'names its fault_owner'),
    (lambda rv: rv.update(source_frames=None), 'together'),
    (lambda rv: rv.update(source_frames=None, clip=None), 'frame window'),
    (lambda rv: rv.update(clip='run'), "record's clip"),
    (lambda rv: rv.update(source_frames=[181, 121]), 'source_frames'),
])
def test_a_review_row_out_of_shape_is_refused(mutate, match):
    ev = _record(reviews={'aurora': _review_fail(), 'grux': te.REVIEW_PASS})
    mutate([rv for rv in ev['reviews'] if rv['body'] == 'aurora'][0])
    with pytest.raises(fb.FeedbackError, match=match):
        fb.validate_evaluation(ev)


def test_a_passed_review_naming_an_owner_is_refused():
    ev = _record()
    ev['reviews'][0]['fault_owner'] = 'source'
    with pytest.raises(fb.FeedbackError, match='only a failed review'):
        fb.validate_evaluation(ev)


def test_a_review_issue_resolves_when_the_review_passes_and_stays_open_when_it_fails_again():
    r1 = fb.feedback(_record(reviews={'aurora': _review_fail(), 'grux': te.REVIEW_PASS}))
    again = fb.feedback(_record(revision=2, reviews={'aurora': _review_fail(), 'grux': te.REVIEW_PASS}), prior=r1)
    [res] = again['resolutions']
    assert res['status'] == 'still_open' and res['now'] == {'aurora': {'state': 'rejected', 'measured_cm': None}}
    assert again['issues'][0]['carries'] == 'B001-r1-1'
    clean = fb.feedback(_record(revision=2), prior=r1)
    [res] = clean['resolutions']
    assert res['status'] == 'resolved' and clean['decision'] == 'accepted'


@pytest.mark.parametrize('field, value', [
    ('source_frames', None), ('evidence', []), ('observed', ''), ('requested_action', ' '), ('do_not_change', []),
    ('fault_owner', 'retarget'),
])
def test_an_issue_without_frames_evidence_observation_action_or_do_not_change_fails(field, value):
    rec = fb.feedback(_source_slide())
    rec['issues'][0][field] = value
    with pytest.raises(fb.FeedbackError):
        fb.validate_brief(rec)


def test_every_brief_must_say_the_exchange_is_simulated():
    rec = fb.feedback(_source_slide())
    assert 'Exchange: simulated.' in fb.render_page(rec)
    rec['exchange'] = 'real'
    with pytest.raises(fb.FeedbackError) as err:
        fb.validate_brief(rec)
    assert err.value.reason == 'exchange'
    rec.pop('exchange')
    with pytest.raises(fb.FeedbackError, match='missing'):
        fb.validate_brief(rec)


def test_an_accepted_revision_raises_no_issues():
    rec = fb.feedback(_record())
    assert rec['issues'] == [] and not rec['send_to_vendor'] and rec['decision'] == 'accepted'


def test_an_unsupported_body_does_not_withhold_an_independent_source_issue():
    ev = _source_slide(reviews={'aurora': te.REVIEW_PASS})            # grux never reviewed
    assert fb.overall_decision(ev) == me.REJECTED                     # rejected outranks unmeasurable
    ev2 = _source_slide(revision=1, grux=te.make_take(body='grux', slide=SLIDE, ground={'kind': 'stairs'}))
    rec = fb.feedback(ev2)
    assert rec['decision'] == 'unsupported' and rec['send_to_vendor'] is True
    [issue] = rec['issues']
    assert issue['key'] == KEY and issue['measured_on'] == ['source', 'aurora']
    fb.validate_brief(rec)


def test_an_unpinned_conversion_is_not_sent_to_the_vendor():
    src = te.make_take(role='source', body='source', slide=SLIDE, checks={'viewer_pinned': {'passed': False}})
    rec = fb.feedback(_record(source=src, aurora=te.make_take(body='aurora', slide=SLIDE),
                              grux=te.make_take(body='grux', slide=SLIDE)))
    assert rec['send_to_vendor'] is False and rec['issues'] == []
    assert {w['fault_owner'] for w in rec['withheld'] if w['row'].startswith(('source:slide', 'aurora:slide'))} \
        == {'conversion'}


@pytest.mark.parametrize('measured, words', [
    (-10.0, 'raise the left heel to at least -0.5 cm'),               # penetration: the lower bound failed
    (6.0, 'bring the left heel down to at most 1 cm'),                # float: the upper bound failed
])
def test_a_ground_action_names_the_bound_that_failed(measured, words):
    text = fb._action('ground_min', 'l', 'heel', [1, 61], [-0.5, 1.0], measured)
    assert text.startswith(words)


def test_a_penetrating_source_heel_asks_to_raise_it():
    src = te.make_take(role='source', body='source', float_cm=('l', WINDOW, -10.0))
    [issue] = [i for i in fb.feedback(_record(source=src))['issues']
               if i['key'] == fb.issue_key('walk', 'ground_min', 'l', 'heel', WINDOW)]
    assert issue['requested_action'].startswith('raise the left heel to at least -0.5 cm clearance')


def test_a_clip_evaluated_twice_is_refused():
    ev = _source_slide()
    with pytest.raises(fb.FeedbackError, match='twice'):
        fb.feedback([ev, copy.deepcopy(ev)])


# ------------------------------------------------------------------------------------------------------- resubmission
def test_a_clean_resubmission_resolves_the_issue_and_leaves_revision_1_untouched(tmp_path):
    root = str(tmp_path / 'feedback')
    r1 = fb.feedback(_source_slide())
    p1 = fb.write_feedback(r1, root)
    r1_bytes = open(p1, 'rb').read()
    r2 = fb.feedback(_record(revision=2), prior=fb.load_feedback(root, 'B001', 1))
    assert r2['supersedes'] == 1 and r2['prior_sha256'] == fb.digest(r1) and r2['decision'] == 'accepted'
    [res] = r2['resolutions']
    assert res['issue_id'] == 'B001-r1-1' and res['status'] == 'resolved' and res['raised_in'] == 1
    assert res['previous_measured_cm'] == pytest.approx(5.0)
    assert res['now']['source'] == {'state': 'accepted', 'measured_cm': 0.0}
    fb.write_feedback(r2, root)
    assert open(p1, 'rb').read() == r1_bytes
    assert os.path.exists(fb.brief_path(root, 'B001', 2, '.json'))


def test_a_resubmission_that_still_fails_on_one_body_is_still_open():
    r1 = fb.feedback(_source_slide())
    r2 = fb.feedback(_grux_only(revision=2), prior=r1)
    [res] = r2['resolutions']
    assert res['status'] == 'still_open' and res['now']['grux']['state'] == 'rejected'
    assert res['now']['source']['state'] == 'accepted'
    r3 = fb.feedback(_record(revision=3), prior=r2)
    assert [(r['issue_id'], r['raised_in'], r['status']) for r in r3['resolutions']] == [('B001-r1-1', 1, 'resolved')]


def test_the_same_source_failure_again_is_still_open_and_carried():
    r1 = fb.feedback(_source_slide())
    r2 = fb.feedback(_source_slide(revision=2), prior=r1)
    assert r2['resolutions'][0]['status'] == 'still_open'
    assert r2['issues'][0]['id'] == 'B001-r2-1' and r2['issues'][0]['carries'] == 'B001-r1-1'


def test_a_resubmission_needs_the_previous_revision_record():
    with pytest.raises(fb.FeedbackError) as err:
        fb.feedback(_record(revision=2))
    assert err.value.reason == 'prior'
    with pytest.raises(fb.FeedbackError):
        fb.feedback(_record(revision=3), prior=fb.feedback(_source_slide()))


def test_an_earlier_record_is_never_rewritten(tmp_path):
    root = str(tmp_path / 'feedback')
    r1 = fb.feedback(_source_slide())
    fb.write_feedback(r1, root)
    fb.write_feedback(copy.deepcopy(r1), root)                 # identical: allowed, nothing changes
    other = fb.feedback(_source_slide(), notes={KEY: {'hypothesis': 'changed my mind'}})
    with pytest.raises(fb.FeedbackError) as err:
        fb.write_feedback(other, root)
    assert err.value.reason == 'history'
    assert json.load(open(fb.brief_path(root, 'B001', 1, '.json'))) == r1


# ----------------------------------------------------------------------------------------------------------- sign-off
def test_sign_off_is_a_record_and_accept_needs_the_evaluation_signed_off(tmp_path):
    ev2 = _record(revision=2)
    r2 = fb.feedback(ev2, prior=fb.feedback(_source_slide()))
    s = _accept(r2, ev2, reason='slide gone on both')
    assert s['exchange'] == 'simulated' and s['verdict'] == 'accept' and s['record_sha256'] == fb.digest(r2)
    assert s['inventory'] == {'walk': [te.FIRST, te.LAST]} and s['still_open'] == []
    path = fb.write_sign_off(s, str(tmp_path))
    assert json.load(open(path)) == s
    ev1 = _source_slide()
    r1 = fb.feedback(ev1)
    with pytest.raises(fb.FeedbackError, match='cannot accept'):
        _accept(r1, ev1)
    assert _accept(r1, ev1, verdict='reject', reason='send it back')['verdict'] == 'reject'


@pytest.mark.parametrize('reviews', [
    {'aurora': {'result': 'fail', 'reviewed_by': 'animator', 'reason': 'feet float', 'fault_owner': 'uncertain'},
     'grux': te.REVIEW_PASS},
    {'aurora': te.REVIEW_PASS},
])
def test_accept_is_blocked_by_a_failed_or_missing_review_even_with_passing_metrics(reviews):
    ev = _record(reviews=reviews)
    with pytest.raises(fb.FeedbackError, match='cannot accept'):
        _accept(fb.feedback(ev), ev)


def test_sign_off_refuses_an_evaluation_other_than_the_one_the_record_was_made_from():
    ev = _record()
    rec = fb.feedback(ev)
    ev['invocation_id'] = 'inv-other'
    with pytest.raises(fb.FeedbackError, match='not the ones'):
        _accept(rec, ev)


# Each of these was signed off before: a defect, missing work or an open issue that the flags did not show.
def _no_samples(take):
    take['samples'] = []
    return take


def test_accept_is_refused_when_the_source_has_no_samples():
    ev = _record(source=_no_samples(te.make_take(role='source', body='source')))
    with pytest.raises(fb.FeedbackError, match='cannot accept.*source'):
        _accept(fb.feedback(ev), ev)


def test_accept_is_refused_when_only_the_source_fails():
    ev = _record(source=te.make_take(role='source', body='source', slide=SLIDE))
    rec = fb.feedback(ev)
    assert rec['send_to_vendor'] is True
    with pytest.raises(fb.FeedbackError, match='cannot accept'):
        _accept(rec, ev)


def test_accept_is_refused_on_empty_labels_and_an_omitted_body():
    ev = _record(source=_no_samples(te.make_take(role='source', body='source')),
                 bodies={'aurora': _no_samples(te.make_take())}, reviews={'aurora': te.REVIEW_PASS},
                 labels={'clip': 'walk', 'contacts': []})
    with pytest.raises(fb.FeedbackError, match='no_contact_rows'):
        _accept(fb.feedback(ev), ev)


def test_accept_is_refused_while_an_earlier_issue_is_still_open():
    r1 = fb.feedback(_source_slide())
    # revision 2 drops the failing label but keeps the defective motion: everything measured passes
    ev2 = _source_slide(revision=2, labels=dict(te.LABELS, contacts=te.CONTACTS[1:]))
    assert ev2['sign_off']['signed_off'] is True
    r2 = fb.feedback(ev2, prior=r1)
    assert r2['decision'] == 'accepted' and r2['resolutions'][0]['status'] == 'still_open'
    with pytest.raises(fb.FeedbackError, match='B001-r1-1 still open'):
        _accept(r2, ev2)


def test_accept_needs_every_clip_of_the_received_inventory_and_every_required_body():
    ev = _record()
    rec = fb.feedback(ev)
    _accept(rec, ev)
    with pytest.raises(fb.FeedbackError, match='run: not evaluated'):
        _accept(rec, ev, inventory=['walk', 'run'])
    with pytest.raises(fb.FeedbackError, match='walk: not in the received inventory'):
        _accept(rec, ev, inventory=['run'])
    with pytest.raises(fb.FeedbackError, match='kestrel:not_evaluated'):
        _accept(rec, ev, bodies=['aurora', 'grux', 'kestrel'])
    for bad in ([], None, ['walk', 'walk']):
        with pytest.raises(fb.FeedbackError, match='inventory'):
            _accept(rec, ev, inventory=bad if bad is not None else ())


def test_accept_is_refused_when_a_failing_row_is_deleted_from_the_record():
    # the record is rewritten without aurora's rejected rows, with its flags made to agree: the rows that remain
    # are consistent, so only the independently supplied labels show the missing measurement
    ev = _record(aurora=te.make_take(body='aurora', slide=('l', 'heel', (121, 181), 10.0)))
    ev['rows'] = [r for r in ev['rows'] if r['state'] != me.REJECTED]
    ev['decisions']['aurora'] = me.ACCEPTED
    ev['sign_off'] = {'signed_off': True, 'blocked_by': []}
    rec = fb.feedback(ev)
    with pytest.raises(fb.FeedbackError, match='aurora:slide:l:heel:121-181:missing_row'):
        _accept(rec, ev)
    with pytest.raises(fb.FeedbackError, match='labels:not_supplied'):
        _accept(rec, ev, labels={})


def _slides(ev):
    return [r for r in ev['rows'] if r['measurement'] == 'slide']


@pytest.mark.parametrize('mutate, match', [
    (lambda ev: [r.update(coverage=0, measured=None) for r in _slides(ev)], 'coverage'),
    (lambda ev: [r.update(measured=None) for r in _slides(ev)], 'no measured number'),
    (lambda ev: [r.update(measured=9.0) for r in _slides(ev)], 'against band'),
    (lambda ev: [r.update(measured=True) for r in ev['rows'] if r['measurement'].startswith('check:')]
     and [r['evidence'].update(evidence=None) for r in ev['rows'] if r['measurement'].startswith('check:')],
     'evidence'),
    (lambda ev: ev['reviews'][0].update(evidence=None), 'evidence'),
    (lambda ev: ev['reviews'][0].update(evidence=[{'path': 'B001/r2/sheet.png', 'sha256': te.SHA}]),
     'revision-scoped'),
    (lambda ev: ev.update(invocation_id=None), 'invocation_id'),
    (lambda ev: ev.update(required_bodies=['aurora']), 'required_bodies'),
])
def test_an_accepted_record_is_recomputed_on_load_not_trusted(mutate, match):
    ev = _record()
    mutate(ev)
    with pytest.raises(fb.FeedbackError, match=match):
        fb.validate_evaluation(ev)


def test_a_decision_or_sign_off_flag_that_disagrees_with_the_rows_is_refused():
    ev = _source_slide()
    ev['decisions']['aurora'] = me.UNSUPPORTED                       # worse than its rows: still not its worst row
    with pytest.raises(fb.FeedbackError, match='worst row'):
        fb.validate_evaluation(ev)
    ev = _record(source=te.make_take(role='source', body='source', slide=SLIDE))
    ev['sign_off'] = {'signed_off': True, 'blocked_by': []}
    with pytest.raises(fb.FeedbackError, match='rows say'):
        fb.validate_evaluation(ev)
