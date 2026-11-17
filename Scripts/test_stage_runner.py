import json
import os

import pytest

import stage_runner as sr


def _stages(ran, fail=None):
    def step(name):
        def run(ctx, records):
            ran.append(name)
            if fail and fail[0] == name:
                raise RuntimeError('boom')
            return {'made': name, 'from': sorted(records), 'opt': ctx.get(name)}
        return run
    return [sr.Stage('rig', step('rig'), inputs=lambda ctx: ctx.get('rig')),
            sr.Stage('retargeter', step('retargeter'), needs=['rig']),
            sr.Stage('bake', step('bake'), needs=['retargeter'], inputs=lambda ctx: ctx.get('bake')),
            sr.Stage('physics', step('physics'), inputs=lambda ctx: ctx.get('physics'))]


def _ledger(tmp_path):
    return str(tmp_path / 'hero' / 'ledger.json')


def test_stages_run_in_order_and_each_gets_the_records_it_needs(tmp_path):
    ran = []
    ledger = sr.run(_stages(ran), {'rig': 1}, _ledger(tmp_path), log=lambda m: None)
    assert ran == ['rig', 'retargeter', 'bake', 'physics']
    assert ledger['bake']['record'] == {'made': 'bake', 'from': ['retargeter'], 'opt': None}
    assert all(e['status'] == sr.DONE for e in ledger.values())
    assert json.load(open(_ledger(tmp_path))) == ledger


def test_resume_keeps_what_is_done_from_the_same_inputs_and_reruns_what_changed_and_all_after_it(tmp_path):
    ran = []
    path = _ledger(tmp_path)
    sr.run(_stages(ran), {'rig': 1}, path, log=lambda m: None)
    ran.clear()
    sr.run(_stages(ran), {'rig': 1}, path, resume=True, log=lambda m: None)
    assert ran == []
    # the rig's input changes: it, and everything made from it, runs again; physics, which does not need it, is kept
    sr.run(_stages(ran), {'rig': 2}, path, resume=True, log=lambda m: None)
    assert ran == ['rig', 'retargeter', 'bake']
    ran.clear()
    sr.run(_stages(ran), {'rig': 2, 'bake': 'x'}, path, resume=True, log=lambda m: None)
    assert ran == ['bake']


def test_without_resume_every_selected_stage_runs(tmp_path):
    ran = []
    path = _ledger(tmp_path)
    sr.run(_stages(ran), {}, path, log=lambda m: None)
    ran.clear()
    sr.run(_stages(ran), {}, path, start='bake', log=lambda m: None)
    assert ran == ['bake', 'physics']


def test_a_failure_is_recorded_keeps_earlier_records_and_resume_continues_from_it(tmp_path):
    ran, fail = [], ['bake']
    path = _ledger(tmp_path)
    with pytest.raises(sr.StageFailed) as caught:
        sr.run(_stages(ran, fail), {}, path, log=lambda m: None)
    assert caught.value.stage == 'bake' and isinstance(caught.value.__cause__, RuntimeError)
    ledger = sr.load(path)
    assert ledger['retargeter']['status'] == sr.DONE and ledger['bake']['status'] == sr.FAILED
    assert ledger['bake']['error'] == 'RuntimeError: boom' and 'physics' not in ledger
    assert sr.status(_stages([]), ledger) == [('rig', 'done'), ('retargeter', 'done'), ('bake', 'failed'),
                                              ('physics', 'pending')]
    ran.clear()
    fail.clear()
    sr.run(_stages(ran, fail), {}, path, resume=True, log=lambda m: None)
    assert ran == ['bake', 'physics']


def test_rerunning_a_stage_drops_its_dependents_records_before_it_runs(tmp_path):
    ran, fail = [], []
    path = _ledger(tmp_path)
    sr.run(_stages(ran, fail), {}, path, log=lambda m: None)
    fail.append('rig')
    with pytest.raises(sr.StageFailed):
        sr.run(_stages(ran, fail), {}, path, start='rig', log=lambda m: None)
    ledger = sr.load(path)
    # the old retargeter and bake were made from a rig that is being replaced: their records went with it
    assert sorted(ledger) == ['physics', 'rig'] and ledger['rig']['status'] == sr.FAILED


def test_starting_past_a_stage_with_no_finished_record_is_refused(tmp_path):
    ran = []
    with pytest.raises(sr.StageFailed, match='needs retargeter, which has no finished record'):
        sr.run(_stages(ran), {}, _ledger(tmp_path), start='bake', log=lambda m: None)
    assert ran == []
    with pytest.raises(ValueError, match='no stage'):
        sr.run(_stages(ran), {}, _ledger(tmp_path), start='nope', log=lambda m: None)


def test_starting_past_a_stage_whose_inputs_changed_is_refused_even_with_resume(tmp_path):
    # the rig was built from rig=1; starting at bake with rig=2 would build on a rig that is no longer the spec
    ran = []
    path = _ledger(tmp_path)
    sr.run(_stages(ran), {'rig': 1}, path, log=lambda m: None)
    ran.clear()
    with pytest.raises(sr.StageFailed, match='retargeter, which was made from \\[\'rig\'\\], which is stale'):
        sr.run(_stages(ran), {'rig': 2}, path, start='bake', resume=True, log=lambda m: None)
    assert ran == []
    # and the file says so: the stale records are gone with what was made from them, not left reading "done"
    assert sorted(sr.load(path)) == ['physics']
    with pytest.raises(sr.StageFailed, match='retargeter, which has no finished record'):
        sr.run(_stages(ran), {'rig': 2}, path, start='bake', log=lambda m: None)
    sr.run(_stages(ran), {'rig': 2}, path, resume=True, log=lambda m: None)
    assert ran == ['rig', 'retargeter', 'bake']                                    # physics, untouched, is kept


def test_starting_past_unchanged_stages_is_allowed(tmp_path):
    ran = []
    path = _ledger(tmp_path)
    sr.run(_stages(ran), {'rig': 1}, path, log=lambda m: None)
    ran.clear()
    sr.run(_stages(ran), {'rig': 1}, path, start='bake', log=lambda m: None)
    assert ran == ['bake', 'physics']


def test_prepare_and_finish_run_inside_the_lock_and_the_summary_is_kept_across_runs(tmp_path):
    path = _ledger(tmp_path)
    seen = []

    def prepare(records):
        seen.append(('prepare', os.path.exists(path + '.lock'), sorted(records)))
        records.pop('physics', None)

    def finish(records, failed):
        seen.append(('finish', os.path.exists(path + '.lock'), failed is None))
        records[sr.SUMMARY_KEY] = {'status': 'PASS' if failed is None else 'FAILED', 'runs': len(seen)}
    ran = []
    sr.run(_stages(ran), {}, path, prepare=prepare, finish=finish, log=lambda m: None)
    assert seen == [('prepare', True, []), ('finish', True, True)]
    assert sr.load(path)[sr.SUMMARY_KEY]['status'] == 'PASS' and not os.path.exists(path + '.lock')

    ran.clear()
    sr.run(_stages(ran), {}, path, resume=True, prepare=prepare, finish=finish, log=lambda m: None)
    assert ran == ['physics']                         # prepare dropped it inside the lock, so it ran again
    assert seen[2] == ('prepare', True, ['bake', 'physics', 'retargeter', 'rig', sr.SUMMARY_KEY])

    with pytest.raises(sr.StageFailed):
        sr.run(_stages(ran, ['rig']), {}, path, finish=finish, log=lambda m: None)
    assert sr.load(path)[sr.SUMMARY_KEY]['status'] == 'FAILED'      # finish saw the failure and its word was saved
    assert sr.status(_stages([]), sr.load(path))[0] == ('rig', 'failed')


def test_inputs_that_cannot_be_read_fail_the_stage_and_drop_its_old_record(tmp_path):
    ran = []
    path = _ledger(tmp_path)
    sr.run(_stages(ran), {}, path, log=lambda m: None)
    stages = _stages(ran)

    def unreadable(ctx):
        raise OSError('editor gone')
    stages[0].inputs = unreadable
    with pytest.raises(sr.StageFailed) as caught:
        sr.run(stages, {}, path, resume=True, log=lambda m: None)
    assert caught.value.stage == 'rig'
    record = sr.load(path)
    assert record['rig']['status'] == sr.FAILED and 'editor gone' in record['rig']['error']
    assert 'retargeter' not in record and 'bake' not in record


def test_a_second_run_on_the_same_record_file_is_refused_and_the_lock_is_released(tmp_path):
    path = _ledger(tmp_path)
    seen = []

    def nested(ctx, records):
        with pytest.raises(RuntimeError, match='another run holds'):
            sr.run(_stages([]), {}, path, log=lambda m: None)
        seen.append(True)
        return {}
    sr.run([sr.Stage('only', nested)], {}, path, log=lambda m: None)
    assert seen == [True]
    sr.run([sr.Stage('only', lambda ctx, records: {})], {}, path, log=lambda m: None)    # released

    def boom(ctx, records):
        raise RuntimeError('x')
    with pytest.raises(sr.StageFailed):
        sr.run([sr.Stage('only', boom)], {}, path, log=lambda m: None)
    sr.run([sr.Stage('only', lambda ctx, records: {})], {}, path, log=lambda m: None)    # released after a failure


def test_a_stage_list_with_a_forward_need_or_a_repeat_is_refused_before_anything_runs():
    ok = lambda ctx, records: {}   # noqa: E731
    with pytest.raises(ValueError, match='not declared before it'):
        sr.check([sr.Stage('a', ok, needs=['b']), sr.Stage('b', ok)])
    with pytest.raises(ValueError, match='declared twice'):
        sr.check([sr.Stage('a', ok), sr.Stage('a', ok)])


def test_dependents_are_transitive_and_in_run_order():
    stages = _stages([])
    assert sr.dependents(stages, 'rig') == ['retargeter', 'bake']
    assert sr.dependents(stages, 'physics') == []


def test_a_record_of_a_stage_that_no_longer_exists_is_dropped(tmp_path):
    path = _ledger(tmp_path)
    sr._save(path, {'gone': {'status': sr.DONE, 'key': 'k', 'record': {}}})
    ledger = sr.run(_stages([]), {}, path, log=lambda m: None)
    assert 'gone' not in ledger
