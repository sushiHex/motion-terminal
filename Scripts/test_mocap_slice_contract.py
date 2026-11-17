"""The first slice's host stages agree on the evaluation record: what mocap_evaluate writes, mocap_feedback reads.

The two modules are written apart on purpose (writer and checker share no code), so nothing but this test holds the
record shape between them. It also holds the time contract between the converter (Scripts/acclaim.py) and the
checker: the off-grid end sample the converter keeps is a sample the checker measures."""
import pytest

import acclaim as ac
import mocap_evaluate as me
import mocap_feedback as mf
import test_acclaim as ta
import test_mocap_evaluate as te

SLIDE = ('l', 'both', (1, 61), 5.0)


def _rejected_by_source():
    # a 5 cm slide in the delivered source, carried onto both bodies: the source owns it
    src = te.make_take(role='source', body='source', slide=SLIDE)
    return te.record(source=src, aurora=te.make_take(slide=SLIDE), grux=te.make_take(body='grux', slide=SLIDE))


def test_an_evaluation_record_is_a_valid_feedback_input():
    ev = _rejected_by_source()
    assert ev['kind'] == 'mocap_evaluation' and set(ev['decisions']) == {'source', 'aurora', 'grux'}
    mf.validate_evaluation(ev)                     # raises on any field the feedback stage needs and cannot find
    assert mf.overall_decision(ev) == me.REJECTED


def test_a_source_owned_rejection_becomes_a_vendor_issue():
    brief = mf.feedback(_rejected_by_source())
    mf.validate_brief(brief)
    assert brief['issues'], 'a source-owned slide must reach the vendor brief'
    assert all(i['fault_owner'] == 'source' and i['source_frames'] == [1, 61] for i in brief['issues'])


def _review_fail(owner):
    return {'result': 'fail', 'reviewed_by': 'animator', 'reason': 'left knee snaps through the plant',
            'fault_owner': owner, 'clip': 'walk', 'source_frames': [121, 181]}


def test_a_source_owned_review_fail_reaches_the_brief_and_a_retarget_owned_one_is_withheld():
    # metrics pass on every body; only the animator rejects, on source frames the evaluate stage checked
    sent = mf.feedback(te.record(reviews={'aurora': _review_fail('source'), 'grux': te.REVIEW_PASS}))
    [issue] = sent['issues']
    assert issue['kind'] == 'review' and issue['fault_owner'] == 'source' and issue['source_frames'] == [121, 181]
    assert sent['send_to_vendor'] is True
    kept = mf.feedback(te.record(reviews={'aurora': _review_fail('retarget'), 'grux': te.REVIEW_PASS}))
    assert kept['decision'] == me.REJECTED and kept['issues'] == [] and kept['send_to_vendor'] is False
    assert [w['fault_owner'] for w in kept['withheld'] if w['kind'] == 'review'] == ['retarget']


def test_an_accepted_record_raises_no_vendor_issue_and_signs_off():
    ev = te.record()
    assert mf.overall_decision(ev) == me.ACCEPTED
    assert ev['sign_off'] == {'signed_off': True, 'blocked_by': []}
    mf.validate_evaluation(ev)
    brief = mf.feedback(ev)
    assert not brief['issues'] and brief['decision'] == me.ACCEPTED
    s = mf.sign_off(brief, ev, inventory=RECEIPT_CLIPS, required_bodies=te.BODIES, labels={'walk': te.LABELS},
                    approver='lead', verdict='accept', reason='clean', on='2026-11-16')
    assert s['verdict'] == 'accept'


# the clips of a receive receipt, as Scripts/mocap_receive.py writes them (only id and source_frames are read)
RECEIPT_CLIPS = [{'id': 'walk', 'format': 'asf_amc', 'source_frames': [te.FIRST, te.LAST], 'revision': 1}]
SHORT_LABELS = dict(te.LABELS, contacts=te.CONTACTS[:3])     # every labelled window inside frames 1..181


def _sign(ev, inventory=RECEIPT_CLIPS):
    return mf.sign_off(mf.feedback(ev), ev, inventory=inventory, required_bodies=te.BODIES,
                       labels={'walk': SHORT_LABELS}, approver='lead', verdict='accept', reason='x', on='2026-11-16')


def test_a_take_truncated_against_the_receipt_cannot_be_signed_off():
    def cut(take):
        take['samples'] = [s for s in take['samples'] if s['source_frame'] <= 181]
        take['source_frame_last'] = 181
        return take
    assert _sign(te.record(labels=SHORT_LABELS))['verdict'] == 'accept'
    # every take ends at 181, though the receipt declares 1..241: each contact window still passes
    short = te.record(source=cut(te.make_take(role='source', body='source')), aurora=cut(te.make_take()),
                      grux=cut(te.make_take(body='grux')), labels=SHORT_LABELS)
    with pytest.raises(mf.FeedbackError, match='span'):
        _sign(short)
    # and evaluating against a declared range other than the receipt's is no way round it
    told_short = te.record(source=cut(te.make_take(role='source', body='source')), aurora=cut(te.make_take()),
                           grux=cut(te.make_take(body='grux')), labels=SHORT_LABELS, declared=(1, 181))
    assert told_short['sign_off']['signed_off'] is True
    with pytest.raises(mf.FeedbackError, match='declared'):
        _sign(told_short)
    with pytest.raises(mf.FeedbackError, match='inventory'):
        _sign(told_short, inventory=[{'id': 'walk'}])


# --------------------------------------------------------------------------------------------- time contract
# Source frames 5..8 at 120 Hz resampled to 30 Hz: the converter keeps samples at frames 5 and 8, frame 8 flagged
# `end_sample` (off the k / 30 grid). The root moves 10 cm forward between them. Before the evaluator knew the end
# sample, it dropped frame 8 as off-grid and read slide 0.0 cm at coverage 1.0: missing data reading as no error.
TEN_CM = 10.0


def _converted_take():
    asf = ta._asf(ta.ROOT_PLAIN, [ta._bone(1, 'spine', '0 1 0', 1)], ['root spine'])
    # source +Z is UE +X (basis B); TZ in ASF units so the root reaches 10 cm at frame 8
    amc = ta._amc(*[(5 + i, ['root 0 0 %r 0 0 0' % (TEN_CM / ta.S * i / 3)]) for i in range(4)])
    out = ac.resample(ta._canon(asf, amc), 30)
    frames = out['frames']
    assert frames['source_frame'] == pytest.approx([5, 8]) and frames['end_sample'] == [False, True]
    samples = []
    for sf, t, end, p in zip(frames['source_frame'], frames['t_s'], frames['end_sample'], frames['pelvis_t']):
        # the root joint stands in for the heel: its position, and its clearance above the z = 0 ground
        lm = {'position': list(p), 'clearance_cm': p[2]}
        samples.append({'source_frame': sf, 't_s': t, 'end_sample': end,
                        'landmarks': {'l': {'heel': lm, 'ball': lm}, 'r': {'heel': lm, 'ball': lm}}})
    first = frames['source_frame'][0]
    return {'body': 'source', 'role': 'source', 'clip': 'walk', 'fps': out['fps'],
            'fps_source': out['provenance']['resampled_from_fps'], 'source_frame_first': first,
            'source_frame_last': frames['source_frame'][-1], 'ground': {'kind': 'flat', 'z_cm': 0.0},
            'samples': samples, 'checks': te.PASS_CHECKS_SOURCE}


def test_the_converter_s_end_sample_is_measured_a_ten_cm_endpoint_slide_is_not_zero():
    take = _converted_take()
    labels = {'clip': 'walk', 'contacts': [{'foot': 'l', 'landmark': 'heel', 'source_frames': [5, 8]}]}
    rows = me.measure_take(take, labels, te.BANDS)
    r = te.one(rows, 'slide', 'l', 'heel', (5, 8))
    assert r['measured'] == pytest.approx(TEN_CM, abs=1e-9) and r['state'] == me.REJECTED
    assert r['coverage'] == 1.0 and r['evidence']['expected_samples'] == 2
    assert r['evidence']['duration_s'] == pytest.approx(3 / 120)
    assert 'invalid_samples' not in r['evidence']
    # without the flag, frame 8 is off the grid and invalid: the window is then unmeasurable, never 0 cm
    take['samples'][-1]['end_sample'] = False
    r = te.one(me.measure_take(take, labels, te.BANDS), 'slide', 'l', 'heel', (5, 8))
    assert r['state'] == me.UNMEASURABLE and r['measured'] is None
