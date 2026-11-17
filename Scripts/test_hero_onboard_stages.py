"""hero_onboard's stages without the editor: the stage list itself, a whole run with every stage stubbed (the wiring and
the record file), the summary other scripts read, --status, the ordering guards inside the stages (git-ignore before
the first delete, dependents first, outputs deleted again on any failure), and the kinds through realize."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_pipeline as ap  # noqa: E402
import hero_kinds as kinds  # noqa: E402
import hero_onboard as ho  # noqa: E402
import hero_onboard_core as core  # noqa: E402
import hit_reaction_core  # noqa: E402
import stage_runner as sr  # noqa: E402

B = '/Game/MT/X/'


def _h():
    cls = lambda p: '%s.%s_C' % (p, p.rsplit('/', 1)[1])         # noqa: E731
    return {'rig': 'RigX', 'short': 'X', 'clip_prefix': 'X_', 'retarget_dir': B + 'Retargeted/',
            'inplace_dir': B + 'InPlace/', 'ik_rig': B + 'Retarget/IK_MT_X', 'retargeter': B + 'Retarget/RTG_MT_T_to_X',
            'bs': B + 'X_BS_Locomotion_2D', 'bs_inplace': B + 'InPlace/X_BS_Locomotion_2D_InPlace',
            'abp_main': B + 'ABP_MT_X', 'abp_main_class': cls(B + 'ABP_MT_X'), 'physics_asset': B + 'PA_MT_X',
            'cloth_collision_asset': B + 'PA_MT_X_ClothCollision', 'abp_path': B + 'ABP_MT_XDynamics',
            'pawn_path': B + 'BP_MT_TestPawn_X', 'inplace_path': B + 'InPlace/ABP_MT_X_InPlace',
            'anim_class': cls(B + 'InPlace/ABP_MT_X_InPlace'), 'mesh': '/Game/X/SK_X', 'skeleton': '/Game/X/SK_X_Skeleton',
            'profile': {'status': 'ok', 'roles': {'pelvis': 'pelvis'}, 'stock': {}}}


class FakeCtx(ho.Ctx):
    def reads(self):
        return {'skeleton': {'ok': True}, 'source_rig': {}, 'source_root': {'bone': 'root', 'from': 'x'},
                'template': {'cdo': {}, 'bs': {}, 'clip_kinds': {}}}


def _ctx(tmp_path, **kw):
    return FakeCtx(_h(), str(tmp_path / 'onboard_RigX.json'), ops='ops', **kw)


def _summary(doc):
    return doc[sr.SUMMARY_KEY]


def _stubbed(seen, fail=None):
    def step(name):
        def run(ctx, records):
            seen.append((name, sorted(records)))
            if name == fail:
                raise RuntimeError('%s broke' % name)
            rec = {'made': name}
            if name == 'preflight':
                rec['has_foot_ik'] = True
            if name == 'verify':
                rec.update(gaps=[], has_foot_ik=False)
            return rec
        return run
    return [sr.Stage(s.name, step(s.name), s.needs, s.inputs) for s in ho.STAGES]


def test_the_stage_list_is_ordered_and_every_need_is_a_declared_stage():
    sr.check(ho.STAGES)
    assert ho.STAGE_NAMES == ('preflight', 'ik_rig', 'retargeter', 'bake', 'locomotion', 'inplace', 'pawn', 'physics',
                              'verify')
    names = set(ho.STAGE_NAMES)
    assert all(set(s.needs) <= names for s in ho.STAGES)
    needs = {s.name: list(s.needs) for s in ho.STAGES}
    assert needs == {'preflight': [], 'ik_rig': ['preflight'], 'retargeter': ['preflight', 'ik_rig'],
                     'bake': ['preflight', 'retargeter'], 'locomotion': ['preflight', 'bake'],
                     'inplace': ['preflight', 'locomotion'], 'pawn': ['inplace'], 'physics': [], 'verify': []}
    # physics and verify need no record: they run on a hero whose locomotion was made by hand (hit_reaction_release
    # runs from physics; --check runs verify alone)
    assert all(s.inputs for s in ho.STAGES)
    assert ho.ALIASES == {'retarget': 'ik_rig'} and [s.name for s in ho.CHECK_STAGES] == ['verify']


def test_a_whole_run_hands_each_stage_its_needs_and_writes_the_record_file_with_the_summary(tmp_path):
    seen, ctx = [], _ctx(tmp_path)
    doc = ho.onboard(ctx, _stubbed(seen), log=lambda m: None)
    assert [n for n, _r in seen] == list(ho.STAGE_NAMES)
    assert dict(seen) == {s.name: sorted(s.needs) for s in ho.STAGES}
    on_disk = json.load(open(ctx.record_path))
    assert on_disk == doc
    for name in ho.STAGE_NAMES:
        assert on_disk[name]['status'] == sr.DONE and on_disk[name]['record']['made'] == name and on_disk[name]['key']
    s = _summary(on_disk)
    assert s['status'] == 'PASS' and s['gaps'] == [] and s['hero'] == 'RigX'
    assert s['has_foot_ik'] is False and s['unsupported'] == ho.UNSUPPORTED
    # the summary lives under its one key, nothing of it at the top level
    assert not set(ho.SUMMARY_KEYS) & set(on_disk)
    assert sr.status(ho.STAGES, on_disk) == [(n, 'done') for n in ho.STAGE_NAMES]
    # resumed with nothing changed: every stage is kept except physics and verify, which never are
    seen.clear()
    ctx2 = FakeCtx(_h(), ctx.record_path, ops='ops')
    ho.onboard(ctx2, _stubbed(seen), resume=True, log=lambda m: None)
    assert [n for n, _r in seen] == ['physics', 'verify']


def test_resuming_twice_on_one_context_runs_verify_and_physics_both_times(tmp_path):
    seen, ctx = [], _ctx(tmp_path)
    ho.onboard(ctx, _stubbed(seen), log=lambda m: None)
    for _ in range(2):
        seen.clear()
        doc = ho.onboard(ctx, _stubbed(seen), resume=True, log=lambda m: None)
        assert [n for n, _r in seen] == ['physics', 'verify'] and _summary(doc)['status'] == 'PASS'
    # the id is made by onboard() on every call, not by the context
    first = ctx.invocation
    ho.onboard(ctx, _stubbed([]), resume=True, log=lambda m: None)
    assert ctx.invocation and ctx.invocation != first


def test_physics_inputs_read_nothing_from_the_record_file_and_need_an_invocation(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    with pytest.raises(RuntimeError, match='invocation'):
        ho._physics_inputs(ctx)
    monkeypatch.setattr(sr, 'load', lambda p: pytest.fail('physics inputs must not read the record file'))
    ctx.invocation = 'a'
    one = ho._physics_inputs(ctx)
    ctx.invocation = 'b'
    assert ho._physics_inputs(ctx) != one and 'locomotion' not in one


def test_a_failed_stage_leaves_the_record_file_saying_where_and_why_and_raises(tmp_path):
    seen, ctx = [], _ctx(tmp_path)
    with pytest.raises(sr.StageFailed) as caught:
        ho.onboard(ctx, _stubbed(seen, fail='bake'), log=lambda m: None)
    assert caught.value.stage == 'bake'
    doc = json.load(open(ctx.record_path))
    s = _summary(doc)
    assert s['status'] == 'FAILED' and s['failed_stage'] == 'bake' and s['error'] == 'RuntimeError: bake broke'
    assert doc['bake']['status'] == sr.FAILED and doc['retargeter']['status'] == sr.DONE and 'locomotion' not in doc
    assert ho.status_lines(ctx.record_path)[3] == 'bake        failed  (RuntimeError: bake broke)'
    assert ho.status_lines(ctx.record_path)[-1] == 'status      FAILED'


def test_a_partial_run_drops_the_last_verify_and_starting_from_physics_needs_no_earlier_record(tmp_path):
    seen, ctx = [], _ctx(tmp_path)
    ho.onboard(ctx, _stubbed(seen), log=lambda m: None)
    # a hero onboarded by hand has no records: hit_reaction_release runs from physics
    fresh = FakeCtx(_h(), str(tmp_path / 'hand.json'), ops='ops')
    seen.clear()
    doc = ho.onboard(fresh, _stubbed(seen), start='physics', log=lambda m: None)
    assert [n for n, _r in seen] == ['physics', 'verify'] and _summary(doc)['status'] == 'PASS'
    # a run that rebuilds something and fails before verify must not keep the old verify's PASS
    with pytest.raises(sr.StageFailed):
        ho.onboard(ctx, _stubbed([], fail='pawn'), start='pawn', log=lambda m: None)
    doc = json.load(open(ctx.record_path))
    assert 'verify' not in doc and _summary(doc)['status'] == 'FAILED'


def test_the_record_file_is_written_only_by_the_runner(tmp_path, monkeypatch):
    # every change onboard makes to the file goes through prepare / finish, inside the runner's lock
    calls = []
    real = sr.run

    def run(stages, ctx, path, **kw):
        calls.append(sorted(kw))
        assert kw.get('prepare') and kw.get('finish')
        return real(stages, ctx, path, **kw)
    monkeypatch.setattr(sr, 'run', run)
    ho.onboard(_ctx(tmp_path), _stubbed([]), log=lambda m: None)
    assert calls == [['finish', 'log', 'prepare', 'resume', 'start']]
    src = open(ho.__file__).read()
    assert not hasattr(ho, '_write') and 'os.replace' not in src and 'json.dump(' not in src


def test_the_summary_reads_verify_and_falls_back_to_preflight():
    led = {'preflight': {'status': sr.DONE, 'record': {'has_foot_ik': True}}}
    s = ho.summarize(led, 'R')
    assert s['status'] == 'PARTIAL' and s['has_foot_ik'] is True and s['gaps'] == []
    led['verify'] = {'status': sr.DONE, 'record': {'gaps': ['missing pawn'], 'has_foot_ik': False}}
    s = ho.summarize(led, 'R')
    assert s['status'] == 'GAPS' and s['gaps'] == ['missing pawn'] and s['has_foot_ik'] is False


def test_summary_of_reads_the_summary_key_and_the_older_flat_layout():
    assert ho.summary_of({sr.SUMMARY_KEY: {'status': 'PASS', 'gaps': []}, 'verify': {}}) == {'status': 'PASS',
                                                                                              'gaps': []}
    assert ho.summary_of({'status': 'GAPS', 'gaps': ['x'], 'verify': {'status': 'done'}}) == {'status': 'GAPS',
                                                                                               'gaps': ['x']}
    assert ho.summary_of({}) == {}


def test_status_needs_no_editor_and_the_cli_exits_non_zero_on_a_failure(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / 'onboard_RigX.json')
    h = dict(_h(), onboard_record=path)
    monkeypatch.setattr(ho.dyn_hero, 'resolve', lambda rig: h)
    monkeypatch.setattr(ho, '_ops', lambda: pytest.fail('--status must not reach the editor'))
    assert ho.main(['--hero', 'RigX', '--status']) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ['preflight', 'pending'] and out[-1] == 'status      (no summary)'

    # a record from before stages were recorded says PASS while every stage is pending: the line must say so
    with open(path, 'w') as fh:
        json.dump({'hero': 'RigX', 'status': 'PASS', 'gaps': []}, fh)
    last = ho.status_lines(path)[-1]
    assert last.startswith('status      PASS  (from a record written before stages were recorded')
    with open(path, 'w') as fh:
        json.dump({sr.SUMMARY_KEY: {'status': 'PASS'}}, fh)
    assert ho.status_lines(path)[-1] == 'status      PASS'

    def broken(*a, **k):
        raise sr.StageFailed('bake', 'boom')
    monkeypatch.setattr(ho, 'run', broken)
    assert ho.main(['--hero', 'RigX', '--stage', 'retarget']) == 1
    with pytest.raises(SystemExit):
        ho.main(['--hero', 'RigX', '--stage', 'nope'])


# --- inside the stages -------------------------------------------------------------------------------------------

class Recorder:
    """The host-side effects a stage makes, in order: git-ignore checks, deletes, editor actions, realizes."""

    def __init__(self, monkeypatch, editor=None, outside=None):
        self.log = []
        monkeypatch.setattr(ap, 'require_gitignored', lambda p: self.log.append(('ignored', p)))
        monkeypatch.setattr(ap, 'delete_asset', lambda ops, p: self.log.append(('delete', p)))
        monkeypatch.setattr(ho, '_disk_assets', lambda folder: [])
        defaults = {'referenced_outside': lambda a: {'outside': dict(outside or {})},
                    'inventory': lambda a: {'assets': []}, 'dirty': lambda a: {'dirty': []}}

        def ed(ctx, action, args=None, timeout=900):
            self.log.append(('editor', action))
            return dict(defaults, **(editor or {})).get(action, lambda a: {})(args)
        monkeypatch.setattr(ho, '_editor', ed)

        def realize(ctx, kind, path, spec):
            self.log.append(('realize', kind.name, path))
            return {'kind': kind.name, 'path': path, 'spec_digest': ap.digest(spec), 'described': {}}
        monkeypatch.setattr(ho, '_realize', realize)

    def first(self, what):
        return next(i for i, e in enumerate(self.log) if e[0] == what)


def _pre():
    tpl = {'cdo': {'Anim_Idle': '/Game/T/Idle.Idle', 'Anim_Jump': None, 'Anim_Fall': None, 'Anim_Land': None,
                   core.BS_VAR: '/Game/T/BS.BS'},
           'bs': {'axis_x': {k: 0 for k in core.AXIS_KEYS}, 'axis_y': {k: 0 for k in core.AXIS_KEYS},
                  'samples': [{'animation': '/Game/T/Idle.Idle', 'x': 0.0, 'y': 0.0},
                              {'animation': '/Game/T/Jog.Jog', 'x': 0.0, 'y': 500.0}]}}
    return {'chains': [['Head', 'head', 'head']], 'pelvis': 'pelvis', 'root': 'root', 'travel_bones': ['root', 'pelvis'],
            'has_foot_ik': False, 'template': tpl,
            'source_rig': {'path': '/Game/T/IK_T', 'mesh': '/Game/T/SK_T', 'chains': [['Head', 'head', 'head']]}}


def test_the_ik_rig_stage_checks_both_targets_then_deletes_the_retargeter_that_references_the_rig(tmp_path, monkeypatch):
    rec = Recorder(monkeypatch)
    h = _h()
    got = ho.ik_rig(_ctx(tmp_path), {'preflight': _pre()})
    assert rec.log[:2] == [('ignored', h['ik_rig']), ('ignored', h['retargeter'])]
    assert rec.log[2:] == [('editor', 'referenced_outside'), ('delete', h['retargeter']),
                           ('realize', 'ik_rig', h['ik_rig'])]
    assert got['path'] == h['ik_rig']


def test_the_locomotion_stage_checks_both_targets_first_and_deletes_the_abp_before_its_blend_space(tmp_path,
                                                                                                  monkeypatch):
    rec = Recorder(monkeypatch)
    h = _h()
    made = {'/Game/T/Idle': B + 'Retargeted/X_Idle', '/Game/T/Jog': B + 'Retargeted/X_Jog'}
    got = ho.locomotion(_ctx(tmp_path), {'preflight': _pre(), 'bake': {'destinations': made}})
    assert rec.log[:2] == [('ignored', h['bs']), ('ignored', h['abp_main'])]
    assert [e for e in rec.log if e[0] in ('delete', 'realize')] == [
        ('delete', h['abp_main']), ('realize', 'blend_space', h['bs']), ('realize', 'abp', h['abp_main'])]
    assert got['abp_compile_warnings'] is False


@pytest.mark.parametrize('how', ['raises', 'problems', 'unsaved'])
def test_the_bake_deletes_every_expected_output_first_and_again_on_any_failure(tmp_path, monkeypatch, how):
    extra = B + 'Retargeted/X_RefPose'
    # made by the batch but named by no reply (the call died, or raised decoding its results); only the inventory
    # after the failure, against the one before the bake, finds it
    unnamed = B + 'Retargeted/X_Half'
    kept = B + 'Retargeted/X_Kept'                     # there before the bake: never the bake's to delete
    state = {'baked': False}

    def bake(args):
        state['baked'] = True
        if how == 'raises':
            raise RuntimeError('the editor action raised mid-way')
        rows = [{'source': s, 'retargeted': d, 'status': 'Retargeted', 'requested': True}
                for s, d in args['destinations'].items()]
        rows.append({'source': '/Game/T/RefPose', 'retargeted': extra, 'status': 'Retargeted', 'requested': False})
        return {'results': rows, 'clips': [], 'problems': ['static bake'] if how == 'problems' else [],
                'saved': how != 'unsaved' and how != 'problems'}
    rec = Recorder(monkeypatch, {'bake': bake,
                                 'inventory': lambda a: {'assets': [kept] + ([unnamed] if state['baked'] else [])}})
    dests = [B + 'Retargeted/X_Idle', B + 'Retargeted/X_Jog']
    with pytest.raises(RuntimeError):
        ho.bake(_ctx(tmp_path), {'preflight': _pre()})
    # every destination is checked before the first delete, and deleted before the bake runs
    assert rec.log[:2] == [('ignored', d) for d in dests]
    deletes = [i for i, e in enumerate(rec.log) if e[0] == 'delete']
    bake_at = rec.log.index(('editor', 'bake'))
    assert [rec.log[i][1] for i in deletes if i < bake_at] == dests
    # the inventory is taken after those deletes and before the bake
    assert max(deletes[:2]) < rec.log.index(('editor', 'inventory')) < bake_at
    after = [rec.log[i][1] for i in deletes if i > bake_at]
    # ...and again after the failure, with whatever the batch made that was not asked for: named by its reply, or
    # found only by the inventory
    assert after == dests + sorted([unnamed] + ([extra] if how != 'raises' else []))
    assert kept not in after


def test_a_bake_cleanup_that_cannot_reach_the_editor_is_recorded_and_done_first_by_the_next_bake(tmp_path,
                                                                                                monkeypatch):
    unnamed = B + 'Retargeted/X_Half'
    later = B + 'Retargeted/X_AddedSince'               # put in the folder after the failure: not the bake's
    state = {'down': False}
    disk = {'now': []}

    def bake(args):
        state['down'], disk['now'] = True, [unnamed]     # wrote a file, then the editor went away
        raise RuntimeError('the editor went away')

    def inventory(args):
        if state['down']:
            raise RuntimeError('no editor')
        return {'assets': list(disk['now'])}
    rec = Recorder(monkeypatch, {'bake': bake, 'inventory': inventory})
    monkeypatch.setattr(ho, '_disk_assets', lambda folder: list(disk['now']))
    ctx = _ctx(tmp_path)
    with pytest.raises(RuntimeError, match='went away'):
        ho.bake(ctx, {'preflight': _pre()})
    pending = ctx.pending_bake_cleanup
    assert pending['before'] == [] and pending['folder'] == B + 'Retargeted/' and 'no editor' in pending['error']
    assert pending['expected'] == [B + 'Retargeted/X_Idle', B + 'Retargeted/X_Jog']
    # what a deferred cleanup may delete is frozen at the failure, from the disk (no editor needed)
    assert pending['disk_after'] == [unnamed]
    # the next run's bake (the editor back): the cleanup first, then this bake's own deletes; an asset added to the
    # folder since is not taken for the bake's
    state['down'], disk['now'] = False, [unnamed, later]
    rec.log.clear()
    with pytest.raises(RuntimeError, match='went away'):
        ho.bake(ctx, {'preflight': _pre()})
    deletes = [e[1] for e in rec.log[:rec.log.index(('editor', 'bake'))] if e[0] == 'delete']
    assert deletes == pending['expected'] + [unnamed] + pending['expected'] and later not in deletes


def test_a_deferred_bake_cleanup_asks_the_tracking_guard_again_for_every_target(tmp_path, monkeypatch):
    rec = Recorder(monkeypatch)
    tracked = B + 'Retargeted/X_Jog'

    def ignored(p):
        rec.log.append(('ignored', p))
        if p == tracked:
            raise RuntimeError('not git-ignored')
    monkeypatch.setattr(ap, 'require_gitignored', ignored)
    pending = {'folder': B + 'Retargeted/', 'before': [], 'made': [],
               'expected': [B + 'Retargeted/X_Idle', tracked], 'disk_after': [B + 'Retargeted/X_Half']}
    ho._bake_cleanup(_ctx(tmp_path), pending, pending['disk_after'])
    # a destination that became tracked since the failure is not deleted, expected or not
    assert [e[1] for e in rec.log if e[0] == 'delete'] == [B + 'Retargeted/X_Idle', B + 'Retargeted/X_Half']


def test_a_pending_bake_cleanup_goes_into_the_failure_record_and_comes_back_from_it(tmp_path):
    pending = {'folder': B + 'Retargeted/', 'before': [], 'expected': [B + 'Retargeted/X_Idle'], 'made': []}
    stages = _stubbed([], fail='bake')
    bake_at = [s.name for s in stages].index('bake')

    def failing(ctx, records):
        ctx.pending_bake_cleanup = dict(pending)
        raise RuntimeError('bake broke')
    stages[bake_at] = sr.Stage('bake', failing, stages[bake_at].needs, stages[bake_at].inputs)
    ctx = _ctx(tmp_path)
    with pytest.raises(sr.StageFailed):
        ho.onboard(ctx, stages, log=lambda m: None)
    doc = json.load(open(ctx.record_path))
    assert doc['bake']['pending_cleanup'] == pending and _summary(doc)['pending_bake_cleanup'] == pending
    # a fresh context (the next run) reads it back before any stage runs
    seen = []

    def watch(ctx, records):
        seen.append(ctx.pending_bake_cleanup)
        return {'made': 'preflight', 'has_foot_ik': True}
    stages = _stubbed([], fail='ik_rig')
    stages[0] = sr.Stage('preflight', watch, (), stages[0].inputs)
    with pytest.raises(sr.StageFailed):
        ho.onboard(_ctx(tmp_path), stages, log=lambda m: None)
    assert seen == [pending]
    assert ho._pending_cleanup(json.load(open(ctx.record_path))) == pending


def test_a_cleanup_still_owed_stays_on_disk_while_a_run_is_under_way(tmp_path):
    # rebuilding preflight drops the failed bake entry that holds it; a run that dies mid-way must not lose it
    pending = {'folder': B + 'Retargeted/', 'before': [], 'expected': [B + 'Retargeted/X_Idle'], 'made': []}
    ctx = _ctx(tmp_path)
    os.makedirs(os.path.dirname(ctx.record_path), exist_ok=True)
    with open(ctx.record_path, 'w') as fh:
        json.dump({'bake': {'status': sr.FAILED, 'key': 'k', 'error': 'x', 'pending_cleanup': pending}}, fh)
    mid = []

    def look(ctx, records):
        mid.append(json.load(open(ctx.record_path)))
        raise RuntimeError('the host died here')
    stages = _stubbed([])
    stages[1] = sr.Stage('ik_rig', look, stages[1].needs, stages[1].inputs)
    with pytest.raises(sr.StageFailed):
        ho.onboard(ctx, stages, log=lambda m: None)
    assert 'bake' not in mid[0] and _summary(mid[0]) == {'hero': 'RigX', 'status': 'RUNNING',
                                                         'pending_bake_cleanup': pending}
    assert ho._pending_cleanup(mid[0]) == pending


def test_a_stage_that_deleted_past_outside_referencers_and_then_failed_names_them(tmp_path, monkeypatch):
    def deleted_then_failed(ctx, records):
        ctx.external[B + 'BP_MT_TestPawn_X'] = ['/Game/Levels/Lvl_Show']
        raise RuntimeError('build failed after the delete')
    stages = _stubbed([])
    at = [s.name for s in stages].index('pawn')
    stages[at] = sr.Stage('pawn', ho._with_referencers(deleted_then_failed), stages[at].needs, stages[at].inputs)
    ctx = _ctx(tmp_path, allow_external_referencers=True)
    with pytest.raises(sr.StageFailed):
        ho.onboard(ctx, stages, log=lambda m: None)
    doc = json.load(open(ctx.record_path))
    assert doc['pawn']['external_referencers'] == {B + 'BP_MT_TestPawn_X': ['/Game/Levels/Lvl_Show']}
    assert _summary(doc)['external_referencers'] == doc['pawn']['external_referencers']


def test_an_unexpected_bake_output_is_deleted_only_inside_the_heros_folder_and_when_git_ignored(monkeypatch):
    def ignored(p):
        if p.endswith('Tracked'):
            raise RuntimeError('not git-ignored')
    monkeypatch.setattr(ap, 'require_gitignored', ignored)
    assert ho._deletable(_h(), [B + 'Retargeted/X_Ref', '/Game/Other/X_Ref', B + 'Tracked']) == [
        B + 'Retargeted/X_Ref']


def test_the_bake_passes_the_per_chain_witness_pairs_and_records_its_destinations(tmp_path, monkeypatch):
    sent = {}

    def bake(args):
        sent.update(args)
        rows = [{'source': s, 'retargeted': d, 'status': 'Retargeted', 'requested': True}
                for s, d in args['destinations'].items()]
        return {'results': rows, 'clips': [{'dst': d} for d in args['destinations'].values()], 'problems': [],
                'saved': True}
    Recorder(monkeypatch, {'bake': bake})
    got = ho.bake(_ctx(tmp_path), {'preflight': _pre()})
    assert sent['vary_pairs'] == [('head', 'head')] and sent['source_mesh'] == '/Game/T/SK_T'
    assert got['destinations'] == {'/Game/T/Idle': B + 'Retargeted/X_Idle', '/Game/T/Jog': B + 'Retargeted/X_Jog'}


def test_the_in_place_stage_deletes_the_pawn_and_every_output_first_and_its_outputs_again_on_failure(tmp_path,
                                                                                                    monkeypatch):
    def boom(args):
        raise RuntimeError('pin failed')
    rec = Recorder(monkeypatch, {'inplace': boom})
    h = _h()
    with pytest.raises(RuntimeError, match='pin failed'):
        ho.inplace(_ctx(tmp_path), {'preflight': _pre()})
    # dependents first: the pawn (references the in-place ABP), the in-place ABP (its blend space and clips), the
    # in-place blend space (its clips), then the clips
    outputs = [h['inplace_path'], h['bs_inplace'], B + 'InPlace/X_Idle_InPlace', B + 'InPlace/X_Jog_InPlace']
    assert rec.first('delete') > max(i for i, e in enumerate(rec.log) if e[0] == 'ignored')
    deletes = [e[1] for e in rec.log if e[0] == 'delete']
    assert deletes == [h['pawn_path']] + outputs + outputs


def test_a_pawn_that_does_not_read_back_is_deleted(tmp_path, monkeypatch):
    h = _h()
    rec = Recorder(monkeypatch, {'pawn': lambda a: {'mesh': h['mesh'], 'anim_class': h['abp_main_class'],
                                                    'cloth_disabled': False}})
    with pytest.raises(RuntimeError, match='not the verified in-place ABP'):
        ho.pawn(_ctx(tmp_path), {'inplace': {}})
    assert [e[1] for e in rec.log if e[0] == 'delete'] == [h['pawn_path'], h['pawn_path']]


def test_a_delete_is_refused_when_something_outside_the_heros_folder_references_it(tmp_path, monkeypatch):
    deleted = []
    monkeypatch.setattr(ap, 'delete_asset', lambda ops, p: deleted.append(p))
    monkeypatch.setattr(ho, '_editor', lambda ctx, action, args=None, timeout=900: {
        'outside': {args['paths'][0]: ['/Game/Levels/Lvl_Show']}})
    with pytest.raises(RuntimeError, match='referenced outside') as caught:
        ho._clear(_ctx(tmp_path), [B + 'BP_MT_TestPawn_X'])
    assert deleted == []
    # the message names the referencer and the ways on
    msg = str(caught.value)
    assert '/Game/Levels/Lvl_Show' in msg and B + 'BP_MT_TestPawn_X' in msg
    assert '--allow-external-referencers' in msg and 're-save' in msg and 'delete the referencing package' in msg


def test_allow_external_referencers_deletes_and_names_them_in_the_stage_record(tmp_path, monkeypatch):
    h = _h()
    rec = Recorder(monkeypatch, {'pawn': lambda a: {'mesh': h['mesh'], 'anim_class': h['anim_class'],
                                                    'cloth_disabled': False}},
                   outside={h['pawn_path']: ['/Game/Levels/Lvl_Show']})
    ctx = _ctx(tmp_path, allow_external_referencers=True)
    stage = {s.name: s for s in ho.STAGES}['pawn']
    got = stage.run(ctx, {'inplace': {}})
    assert got['external_referencers'] == {h['pawn_path']: ['/Game/Levels/Lvl_Show']}
    assert [e[1] for e in rec.log if e[0] == 'delete'] == [h['pawn_path']]
    # without the flag the same stage refuses before deleting
    rec.log.clear()
    with pytest.raises(RuntimeError, match='--allow-external-referencers'):
        stage.run(_ctx(tmp_path), {'inplace': {}})
    assert not [e for e in rec.log if e[0] == 'delete']


def test_the_cli_passes_allow_external_referencers(monkeypatch):
    got = {}

    def run(rig, first, check, resume=False, allow_external_referencers=False):
        got.update(allow=allow_external_referencers)
        return {'status': 'PASS'}
    monkeypatch.setattr(ho, 'run', run)
    monkeypatch.setattr(ho.dyn_hero, 'hero_arg', lambda argv: ('RigX', argv))
    ho.main(['--allow-external-referencers'])
    assert got == {'allow': True}


def test_verify_checks_files_and_dirtiness_after_every_read_and_notes_abp_warnings(tmp_path, monkeypatch):
    h, order = _h(), []
    clip = B + 'InPlace/X_Idle_InPlace'
    monkeypatch.setattr(ho, 'describe_skeleton', lambda path, ops=None: {
        'ok': True, 'path': path, 'bones': [{'name': 'root', 'index': 0, 'parent': ''},
                                            {'name': 'pelvis', 'index': 1, 'parent': 'root'}]})
    monkeypatch.setattr(ho, '_has_ik', lambda h, skel: False)
    monkeypatch.setattr(ho.core, 'retarget_chains', lambda roles, parents: [])
    monkeypatch.setattr(ho, '_template', lambda ops=None: (_pre()['template']['cdo'], _pre()['template']['bs']))

    def ed(ctx, action, args=None, timeout=900):
        order.append(action)
        if action == 'dirty':
            return {'dirty': [h['abp_main']] if h['abp_main'] in args['paths'] else []}
        return {'ik_rig_describe': {'chains': [], 'root_motion': 'root', 'mesh': '/Game/T/SK_T',
                                    'validation': {'valid': True, 'errors': [], 'warnings': []}},
                'verify': {'gaps': [], 'pawn': {}, 'exists': {'pawn_path': False},
                           'clips': [{'clip': clip, 'duplicate': True}]}}[action]
    monkeypatch.setattr(ho, '_editor', ed)

    def checked(gaps, name, kind, ops, path, spec):
        order.append('describe ' + name)
        return {'status': 'BS_UpToDateWithWarnings'} if 'ABP' in name else {}
    monkeypatch.setattr(ho, '_checked', checked)
    monkeypatch.setattr(ho, '_physics_gaps', lambda h, parents: order.append('physics') or [])
    monkeypatch.setattr(ho, '_on_disk', lambda p: p != h['ik_rig'])
    got = ho.verify(_ctx(tmp_path), {})
    # the dirty check is the last read, after every describe (nothing here compiles: a read that dirtied is a gap)
    assert order[-1] == 'dirty' and order.index('physics') < order.index('dirty')
    assert 'not on disk: %s' % h['ik_rig'] in got['gaps']
    assert 'dirty after verify read it: %s' % h['abp_main'] in got['gaps']
    assert len(got['notes']) == 2 and all('compiler warnings' in n for n in got['notes'])
    read = ho.verified_assets(h, [{'clip': clip, 'duplicate': True}])
    assert clip in read and B + 'Retargeted/X_Idle' in read and h['pawn_path'] not in read


# --- the kinds through realize -------------------------------------------------------------------------------------

class EditorOps:
    """An editor that answers hero_onboard_editor actions from a table and records them."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def run_file(self, path, timeout=None, HOE_HERO=None, HOE_ACTION=None, HOE_ARGS=None):
        self.calls.append(HOE_ACTION)
        return dict(self.answers.get(HOE_ACTION, {}), action=HOE_ACTION)


def _realize(ops, kind, path, spec):
    deletes = []
    try:
        return ap.realize(ops, kind, path, spec, delete=lambda o, p: deletes.append(p), ignored=lambda p: None), deletes
    except Exception as exc:          # noqa: BLE001
        return exc, deletes


def test_the_abp_kind_builds_without_saving_and_a_cdo_that_did_not_take_is_deleted_not_saved():
    spec = core.abp_spec(B + 'ABP_MT_X', B + 'ABP_MT_X.ABP_MT_X_C', '/Game/MT/ABP_T.ABP_T_C', '/Game/X/SK_X',
                         {'Anim_Idle': B + 'Retargeted/X_Idle'})
    good = {'path': spec['path'], 'parent': spec['parent_class'], 'skeleton': '/Game/X/SK_X', 'compiled': True,
            'status': 'BS_UP_TO_DATE', 'cdo': dict(spec['cdo'])}
    ops = EditorOps({'abp_describe': good})
    rec, deletes = _realize(ops, kinds.abp('RigX'), spec['path'], spec)
    assert ops.calls == ['abp_create', 'abp_cdo', 'abp_describe', 'save'] and rec['kind'] == 'abp'
    ops = EditorOps({'abp_describe': dict(good, cdo={'Anim_Idle': None})})
    exc, deletes = _realize(ops, kinds.abp('RigX'), spec['path'], spec)
    assert isinstance(exc, ap.NotTheSpec) and 'save' not in ops.calls and deletes == [spec['path'], spec['path']]


def test_the_retargeter_kind_refuses_an_empty_fk_setup_before_mapping_chains():
    spec = core.retargeter_spec(B + 'RTG', '/Game/T/IK_T', B + 'IK', 'pelvis', ['Head'], ['Head'], 'root', '/Game/T/SK')
    ops = EditorOps({'fk_setup': {'chains': []}})
    exc, deletes = _realize(ops, kinds.retargeter('RigX'), B + 'RTG', spec)
    assert 'static' in str(exc) and ops.calls == ['retargeter_build', 'fk_setup'] and deletes == [B + 'RTG'] * 2


def test_the_physics_kind_has_no_save_and_relies_on_the_delete_when_the_built_asset_is_not_its_spec():
    class Ops:
        def __init__(self, described):
            self.described, self.scripts = described, []

        def py(self, script, timeout=None):
            self.scripts.append(script)
            if 'build_physics_asset' in script:
                return {'ok': True, 'collide': []}
            return self.described
    spec = {'bodies': [{'bone': 'pelvis', 'physics_type': 'Kinematic', 'elements': []}], 'constraints': [],
            'fixed_step_s': 0.01}
    built = {}
    exc, deletes = _realize(Ops({'ok': True, 'bodies': [], 'constraints': [], 'fixed_step_s': 0.01,
                                 'collisions': []}), kinds.physics('/Game/X/SK_X', built), B + 'PA_MT_X', spec)
    assert isinstance(exc, ap.NotTheSpec) and deletes == [B + 'PA_MT_X', B + 'PA_MT_X'] and built['ok'] is True
    assert kinds.physics('/Game/X/SK_X', {}).save('ops', B + 'PA_MT_X') is None


# --- readers of the onboard record ---------------------------------------------------------------------------------

def test_the_reaction_readers_take_the_stage_record_file_and_the_older_flat_one():
    spec, cloth = {'bodies': [1]}, {'bodies': [2]}
    staged = {'physics': {'status': 'done', 'key': 'k', 'record': {'spec': spec, 'built': {'collide': []},
                                                                    'cloth_collision': {'spec': cloth}}}}
    assert hit_reaction_core.onboard_physics(staged) == ({'spec': spec, 'built': {'collide': []}}, {'spec': cloth})
    flat = {'physics': {'spec': spec, 'built': {}}, 'cloth_collision': {'spec': cloth}}
    assert hit_reaction_core.onboard_physics(flat) == ({'spec': spec, 'built': {}}, {'spec': cloth})
    # a failed physics stage is no reaction body at all
    failed = {'physics': {'status': 'failed', 'key': 'k', 'error': 'x'}}
    assert hit_reaction_core.onboard_physics(failed) == ({}, {})
    assert hit_reaction_core.onboard_physics({}) == ({}, {})
