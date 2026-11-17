"""hero_onboard's generated assets, without the editor: each spec, its comparison with a read-back, the batch
results, the skeleton read, and the driver's build-describe-accept order (a mismatch deletes the build)."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hero_onboard  # noqa: E402
import hero_onboard_core as core  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CHAINS = [('Spine', 'spine_01', 'spine_03'), ('Head', 'head', 'head'), ('LeftLeg', 'thigh_l', 'ball_l')]
RIG = '/Game/MT/X/IK_MT_X'
RTG = '/Game/MT/X/RTG_MT_T_to_X'
SRC_RIG = '/Game/T/IK_T'


def _skel():
    return {'ok': True, 'path': '/Game/X/SK_X_Skeleton', 'bones': [
        {'name': 'pelvis', 'index': 1, 'parent': 'root', 'translation_retargeting': 'Skeleton'},
        {'name': 'root', 'index': 0, 'parent': '', 'translation_retargeting': 'Animation'},
        {'name': 'thigh_l', 'index': 2, 'parent': 'pelvis', 'translation_retargeting': 'AnimationScaled'}]}


def test_the_skeleton_read_gives_parents_and_modes_and_refuses_a_bad_read():
    assert core.skeleton_parents(_skel()) == {'root': None, 'pelvis': 'root', 'thigh_l': 'pelvis'}
    modes, missing = core.translation_modes(_skel(), ['root', 'thigh_l', 'ik_foot_l', 'root'])
    assert modes == {'root': 'Animation', 'thigh_l': 'AnimationScaled'} and missing == ['ik_foot_l']
    with pytest.raises(RuntimeError, match='no such skeleton'):
        core.skeleton_parents({'ok': False, 'error': 'no such skeleton'})
    twice = _skel()
    twice['bones'].append(dict(twice['bones'][0], index=3))
    with pytest.raises(RuntimeError, match='twice'):
        core.skeleton_parents(twice)


def _rig_read(**over):
    d = {'path': RIG, 'mesh': '/Game/X/SK_X.SK_X', 'pelvis': 'pelvis', 'root_motion': 'root',
         'chains': [list(c) for c in reversed(CHAINS)], 'validation': {'valid': True, 'errors': [], 'warnings': []}}
    d.update(over)
    return d


def test_ik_rig_read_back_must_match_its_spec_and_validate():
    spec = core.ik_rig_spec(RIG, '/Game/X/SK_X', 'pelvis', CHAINS, 'root')
    assert core.ik_rig_mismatch(spec, _rig_read()) == []
    # the pelvis is the retarget root; a rig rooted at the root-motion bone is a different rig
    assert any('pelvis' in d for d in core.ik_rig_mismatch(spec, _rig_read(pelvis='root')))
    # the root-motion bone is read back too: the Root Motion op's target root comes from it
    assert core.ik_rig_mismatch(spec, _rig_read(root_motion=None)) == ['IK rig root-motion bone None, wanted root']
    assert core.ik_rig_mismatch(core.ik_rig_spec(RIG, '/Game/X/SK_X', 'pelvis', CHAINS), _rig_read(root_motion=None)) \
        == []
    assert any('chains' in d for d in core.ik_rig_mismatch(spec, _rig_read(chains=[list(c) for c in CHAINS[:2]])))
    # the factory uniquifies a taken name instead of failing
    assert any('uniquifies' in d for d in core.ik_rig_mismatch(spec, _rig_read(path=RIG + '_1')))
    bad = _rig_read(validation={'valid': True, 'errors': [], 'warnings': ['Invalid Retarget Chain: LeftLeg']})
    assert core.ik_rig_mismatch(spec, bad) == ['IK rig validation warning: Invalid Retarget Chain: LeftLeg']
    assert any('does not validate' in d for d in core.ik_rig_mismatch(
        spec, _rig_read(validation={'valid': False, 'errors': ['no mesh'], 'warnings': []})))
    assert any('no validation' in d for d in core.ik_rig_mismatch(spec, _rig_read(validation=None)))
    # the SOURCE rig: one broken chain would pass the retargeter checks (the FK op runs on one good pair)
    assert core.source_rig_problems(_rig_read()) == []
    assert core.source_rig_problems(_rig_read(chains=[['LeftArm', 'upperarm_l', None]])) == [
        'source IK rig chain LeftArm has no start or end bone']
    assert core.source_rig_problems(bad) == ['source IK rig validation warning: Invalid Retarget Chain: LeftLeg']
    assert core.source_rig_problems(_rig_read(validation={'valid': True, 'errors': ['no mesh'], 'warnings': []}))


def test_the_source_rig_is_refused_only_for_errors_and_chain_problems_and_its_other_warnings_are_noted():
    goal = "IK Rig, 'IK_T' has a Goal, 'hand_l_goal' that is not connected to any solvers. It will have no effect."
    root = "IK Rig, 'IK_T' has retarget chains but no retarget root, so a retargeter that uses it cannot retarget"
    rig = _rig_read(validation={'valid': True, 'errors': [], 'warnings': [goal]})
    assert core.source_rig_problems(rig) == [] and core.source_rig_notes(rig) == [goal]
    rig = _rig_read(validation={'valid': True, 'errors': [], 'warnings': [goal, root]})
    assert core.source_rig_problems(rig) == ['source IK rig validation warning: ' + root]
    assert core.source_rig_notes(rig) == [goal]


SETTINGS = dict(core.FK_CHAIN_SETTINGS)


def _rtg_read(**over):
    names = ['Pelvis Motion', 'FK Chains', 'Run IK Rig', 'Root Motion', 'Curve Remap']
    exact = {c[0]: c[0] for c in CHAINS}
    d = {'path': RTG, 'source_ik_rig': SRC_RIG + '.IK_T', 'target_ik_rig': RIG,
         'ops': [{'index': i, 'name': n, 'type': t, 'enabled': True}
                 for i, (n, t) in enumerate(zip(names, core.DEFAULT_OPS))],
         'chain_maps': {'FK Chains': dict(exact), 'Run IK Rig': dict(exact)},
         'fk_chains': {'FK Chains': sorted(exact)},
         'fk_chain_settings': {'FK Chains': {c: dict(SETTINGS) for c in exact}},
         'op_pelvis': {'Pelvis Motion': 'pelvis'},
         'op_root_motion': {'Root Motion': {'source_root': 'root', 'target_root': 'root', 'target_pelvis': 'pelvis'}},
         'validation': {'ready': True, 'errors': [], 'warnings': [], 'cannot_run': []}}
    d.update(over)
    return d


def _rspec(sources=None, source_root='root'):
    targets = [c[0] for c in CHAINS]
    return core.retargeter_spec(RTG, SRC_RIG, RIG, 'pelvis', targets, sources or targets + ['Neck'], 'root',
                                '/Game/T/SK_T', source_root)


def test_retargeter_read_back_must_match_op_by_op():
    spec = _rspec()
    assert core.retargeter_mismatch(spec, _rtg_read()) == []
    # each op's map on its own: a wrong map on the SECOND op with a chain map must not hide behind the first
    maps = {'FK Chains': {c[0]: c[0] for c in CHAINS}, 'Run IK Rig': {'Spine': 'Spine', 'Head': 'Neck', 'LeftLeg': None}}
    assert [d for d in core.retargeter_mismatch(spec, _rtg_read(chain_maps=maps)) if 'Run IK Rig' in d]
    # the empty FK chain list bakes static clips
    assert any('static' in d for d in core.retargeter_mismatch(spec, _rtg_read(fk_chains={'FK Chains': []})))
    # the stack the controller builds, in order
    ops = _rtg_read()['ops'][:2]
    assert any('retargeter ops' in d for d in core.retargeter_mismatch(spec, _rtg_read(ops=ops)))
    # the op's pelvis is a field of its own, read back
    assert any('Pelvis Motion op' in d for d in core.retargeter_mismatch(
        spec, _rtg_read(op_pelvis={'Pelvis Motion': 'root'})))
    # a target chain with no same-named source chain is refused, not left unmapped
    assert any('no same-named source chain' in d for d in core.retargeter_mismatch(_rspec(['Spine', 'Head']),
                                                                                   _rtg_read()))


def test_retargeter_validation_needs_every_op_to_run_and_refuses_every_warning():
    spec = _rspec()
    ok = {'ready': True, 'errors': [], 'cannot_run': [], 'warnings': []}
    assert core.retargeter_mismatch(spec, _rtg_read(validation=ok)) == []
    # every op must run, each refused by its type; Run IK Rig too: a rig with zero solvers and goals initialises
    # (IKRigProcessor.cpp), so its report is a fault, not an expected state
    for name in ('FK Chains', 'Pelvis Motion', 'Root Motion', 'Curve Remap', 'Run IK Rig'):
        dead = core.retargeter_mismatch(spec, _rtg_read(validation=dict(ok, cannot_run=[name])))
        assert any('cannot run' in d and name in d for d in dead), name
    assert set(core.REQUIRED_OPS) == set(core.DEFAULT_OPS)
    assert not hasattr(core, 'OPTIONAL_OPS') and not hasattr(core, 'OPTIONAL_OP_WARNINGS')
    # an op name the read cannot place in the stack is refused, not waved through
    assert any('not in the stack' in d for d in core.retargeter_mismatch(
        spec, _rtg_read(validation=dict(ok, cannot_run=['Mystery']))))
    assert any('not ready' in d for d in core.retargeter_mismatch(spec, _rtg_read(validation=dict(ok, ready=False))))
    # every warning refuses it, the Run IK Rig op's own as much as an FK chain that cannot be retargeted
    run_ik = "Run IK Rig Op: unable to initialize the IK Rig, IK_MT_X for the Skeletal Mesh SK_X. See previous warnings."
    fk = "FK Chain Op unable to retarget source chain, LeftLeg to target chain, LeftLeg.'"
    assert core.retargeter_mismatch(spec, _rtg_read(validation=dict(ok, warnings=[run_ik, fk]))) == [
        'retargeter validation warning: ' + run_ik, 'retargeter validation warning: ' + fk]
    assert core.retargeter_mismatch(spec, _rtg_read(validation=dict(ok, errors=['bad pelvis height']))) == [
        "retargeter validation errors: ['bad pelvis height']"]


def test_the_single_bone_chain_warning_is_accepted_only_for_chains_declared_with_one_bone():
    target = [('Spine', 'spine_01', 'spine_05'), ('Head', 'head', 'head')]
    source = [('Spine', 'spine_01', 'spine_05'), ('Head', 'head', 'head'), ('Neck', 'neck_01', 'neck_02')]
    assert core.single_bone_chains(target, source) == ['Head']
    ok = {'ready': True, 'errors': [], 'cannot_run': [], 'warnings': []}
    head, spine = core.SINGLE_BONE_WARNING % 'Head', core.SINGLE_BONE_WARNING % 'Spine'
    spec = dict(_rspec(), single_bone_chains=['Head'])
    assert core.retargeter_mismatch(spec, _rtg_read(validation=dict(ok, warnings=[head]))) == []
    # the same text for a chain declared with several bones is a real fault (its bones sit on each other)
    assert core.retargeter_mismatch(spec, _rtg_read(validation=dict(ok, warnings=[head, spine]))) == [
        'retargeter validation warning: ' + spine]
    # and a spec that declares no single-bone chain accepts none
    assert core.retargeter_mismatch(_rspec(), _rtg_read(validation=dict(ok, warnings=[head]))) == [
        'retargeter validation warning: ' + head]


def test_retargeter_fk_chain_settings_root_motion_and_every_chain_map_are_read_back():
    spec = _rspec()
    # a chain whose FK is off keeps its name and bakes the retarget pose
    off = _rtg_read()
    off['fk_chain_settings']['FK Chains']['LeftLeg'] = dict(SETTINGS, enable_fk=False)
    assert any('LeftLeg enable_fk' in d and 'retarget pose' in d for d in core.retargeter_mismatch(spec, off))
    for k, v in (('rotation_mode', 'ONE_TO_ONE'), ('translation_mode', 'ABSOLUTE'), ('rotation_alpha', 0.5),
                 ('translation_alpha', 0.0)):
        bad = _rtg_read()
        bad['fk_chain_settings']['FK Chains']['Head'] = dict(SETTINGS, **{k: v})
        assert any('Head %s' % k in d for d in core.retargeter_mismatch(spec, bad)), k
    # the enum as the editor names it (the read keeps the name) is the same setting
    named = _rtg_read()
    named['fk_chain_settings']['FK Chains']['Head'] = dict(SETTINGS, rotation_mode='FKChainRotationMode.INTERPOLATED')
    assert core.retargeter_mismatch(spec, named) == []
    missing = _rtg_read()
    del missing['fk_chain_settings']['FK Chains']['Spine']
    assert any('no settings read back for chain Spine' in d for d in core.retargeter_mismatch(spec, missing))
    # every op that keeps a chain map must read one back, not only the FK op
    assert any('Run IK Rig' in d and 'no chain map' in d for d in core.retargeter_mismatch(
        spec, _rtg_read(chain_maps={'FK Chains': {c[0]: c[0] for c in CHAINS}})))
    # the Root Motion op's bones
    assert any('Root Motion op target_root' in d for d in core.retargeter_mismatch(spec, _rtg_read(
        op_root_motion={'Root Motion': {'source_root': 'root', 'target_root': 'pelvis', 'target_pelvis': 'pelvis'}})))
    # the source root is the bone the op defaults to (source_root_motion), not merely any bone
    assert core.retargeter_mismatch(spec, _rtg_read(
        op_root_motion={'Root Motion': {'source_root': None, 'target_root': 'root', 'target_pelvis': 'pelvis'}})) == [
        'Root Motion op source_root None, wanted root']
    assert core.retargeter_mismatch(spec, _rtg_read(
        op_root_motion={'Root Motion': {'source_root': 'pelvis', 'target_root': 'root', 'target_pelvis': 'pelvis'}})) \
        == ['Root Motion op source_root pelvis, wanted root']
    assert any('names no source root' in d for d in core.retargeter_mismatch(_rspec(source_root=None), _rtg_read()))


def test_the_root_motion_source_root_is_the_source_rigs_bone_then_the_source_mesh_bone_0():
    skel = {'ok': True, 'bones': [{'name': 'pelvis', 'index': 1}, {'name': 'root', 'index': 0}]}
    bone, how = core.source_root_motion({'root_motion': 'root_motion_bone'}, skel)
    assert bone == 'root_motion_bone' and 'source IK rig' in how
    bone, how = core.source_root_motion({'root_motion': None, 'path': SRC_RIG}, skel)
    assert bone == 'root' and 'bone 0' in how
    with pytest.raises(RuntimeError, match='no root-motion bone'):
        core.source_root_motion({'root_motion': None, 'path': SRC_RIG}, None)


AXIS_X = {'name': 'Direction', 'min': -180.0, 'max': 180.0, 'grid_divisions': 4, 'snap_to_grid': False,
          'wrap_input': True}
AXIS_Y = {'name': 'Speed', 'min': 0.0, 'max': 600.0, 'grid_divisions': 4, 'snap_to_grid': False, 'wrap_input': False}
TEMPLATE = {'ok': True, 'skeleton': '/Game/T/SK_T.SK_T', 'axis_x': dict(AXIS_X, extra=1), 'axis_y': AXIS_Y,
            'samples': [{'animation': '/Game/T/Idle.Idle', 'x': 0.0, 'y': 0.0, 'valid': True},
                        {'animation': '/Game/T/Jog_Fwd.Jog_Fwd', 'x': 0.0, 'y': 500.0, 'valid': True},
                        {'animation': '/Game/T/Jog_Left.Jog_Left', 'x': -90.0, 'y': 500.0, 'valid': True}],
            'triangulation_baked': True}


def _twin(p):
    return '/Game/MT/X/Retargeted/X_' + core.clip_name(p)


BS = '/Game/MT/X/X_BS_Locomotion_2D'


def _bs_read(spec, **over):
    """A read-back as the describe tool makes it: triangles exist exactly when the asset does not interpolate on its
    grid (AnimAssetTools.cpp: a grid-interpolated blend space keeps none by design)."""
    d = {'ok': True, 'path': BS, 'skeleton': spec['skeleton'] + '.SK_X', 'axis_x': dict(AXIS_X),
         'axis_y': dict(AXIS_Y),
         'samples': [dict(s, animation=s['animation'] + '.' + core.clip_name(s['animation']), valid=True)
                     for s in spec['samples']]}
    d.update(over)
    d.setdefault('triangulation_baked', d.get('interpolate_using_grid') is not True)
    return d


def test_blend_space_path_and_positions_are_compared_without_rounding():
    spec = core.blend_space_spec('/Game/X/SK_X', TEMPLATE, _twin, BS)
    assert any('blend space is' in d for d in core.blend_space_mismatch(spec, _bs_read(spec, path=BS + '_1')))
    # within the absolute tolerance passes; 0.0004 off 0 is inside it by decision (POSITION_TOL), 0.002 is not
    near = _bs_read(spec)
    near['samples'][0]['x'] = 0.0004
    assert core.blend_space_mismatch(spec, near) == []
    far = _bs_read(spec)
    far['samples'][0]['x'] = 0.002
    assert any('missing' in d for d in core.blend_space_mismatch(spec, far))
    # two samples swapping clips is not the spec, though the clip set and the position set both are
    swapped = _bs_read(spec)
    a, b = swapped['samples']
    a['animation'], b['animation'] = b['animation'], a['animation']
    assert core.blend_space_mismatch(spec, swapped)


def test_blend_space_optional_keys_are_carried_when_the_template_has_them_and_tolerated_when_not():
    assert 'rate_scale' not in core.blend_space_spec('/Game/X/SK_X', TEMPLATE, _twin, BS)['samples'][0]
    tpl = dict(TEMPLATE, interpolate_using_grid=True,
               samples=[dict(s, rate_scale=1.0 if s['y'] else 0.5) for s in TEMPLATE['samples']])
    spec = core.blend_space_spec('/Game/X/SK_X', tpl, _twin, BS)
    assert [s['rate_scale'] for s in spec['samples']] == [0.5, 1.0] and spec['interpolate_using_grid'] is True
    read = _bs_read(spec, interpolate_using_grid=True)
    assert core.blend_space_mismatch(spec, read) == []
    slow = _bs_read(spec, interpolate_using_grid=True)
    slow['samples'][1]['rate_scale'] = 0.9
    assert any('missing' in d for d in core.blend_space_mismatch(spec, slow))
    assert any('interpolate_using_grid' in d for d in core.blend_space_mismatch(spec, _bs_read(spec)))


def test_a_grid_interpolated_blend_space_keeps_no_triangles_and_any_other_must():
    grid_tpl = dict(TEMPLATE, interpolate_using_grid=True)
    spec = core.blend_space_spec('/Game/X/SK_X', grid_tpl, _twin, BS)
    # interpolating on its grid with no triangles: accepted (the builder accepts it too)
    grid = _bs_read(spec, interpolate_using_grid=True, triangulation_baked=False)
    assert core.blend_space_mismatch(spec, grid) == []
    # not on its grid and no triangles: refused, whether or not the spec carries the flag
    tri_spec = core.blend_space_spec('/Game/X/SK_X', dict(TEMPLATE, interpolate_using_grid=False), _twin, BS)
    flat = _bs_read(tri_spec, interpolate_using_grid=False, triangulation_baked=False)
    assert [d for d in core.blend_space_mismatch(tri_spec, flat)] == [
        'blend space triangulation not baked (False) and it does not interpolate on its grid (False)']
    plain = core.blend_space_spec('/Game/X/SK_X', TEMPLATE, _twin, BS)
    assert any('triangulation' in d for d in core.blend_space_mismatch(
        plain, _bs_read(plain, triangulation_baked=False)))
    # the flag itself is compared whenever the spec carries it: a grid spec read back off the grid is refused
    assert any('interpolate_using_grid' in d for d in core.blend_space_mismatch(
        spec, _bs_read(spec, interpolate_using_grid=False)))


def test_blend_space_spec_mirrors_the_reachable_template_and_its_read_back_must_match():
    spec = core.blend_space_spec('/Game/X/SK_X', TEMPLATE, _twin, BS)
    assert spec['axis_x'] == AXIS_X and spec['axis_y'] == AXIS_Y and spec['path'] == BS
    assert [s['animation'] for s in spec['samples']] == ['/Game/MT/X/Retargeted/X_Idle', '/Game/MT/X/Retargeted/X_Jog_Fwd']
    assert core.blend_space_mismatch(spec, _bs_read(spec)) == []
    assert core.blend_space_mismatch(spec, {'ok': False, 'error': 'exists'}) == ['blend space: exists']
    assert any('samples' in d for d in core.blend_space_mismatch(spec, _bs_read(spec, samples=_bs_read(spec)['samples'][:1])))
    assert any('triangulation' in d for d in core.blend_space_mismatch(spec, _bs_read(spec, triangulation_baked=False)))
    # 1 is not True: an axis flag read back as a number is a different read
    assert any('snap_to_grid' in d for d in core.blend_space_mismatch(
        spec, _bs_read(spec, axis_y=dict(AXIS_Y, snap_to_grid=0))))
    assert any('max' in d for d in core.blend_space_mismatch(spec, _bs_read(spec, axis_y=dict(AXIS_Y, max=601.0))))
    bad = _bs_read(spec)
    bad['samples'][0]['valid'] = False
    assert any('not valid' in d for d in core.blend_space_mismatch(spec, bad))


def _row(src, dst, status='Retargeted', requested=True, reason=''):
    return {'source': src, 'retargeted': dst, 'status': status, 'reason': reason, 'requested': requested}


def test_every_batch_result_is_checked_even_when_the_call_did_not_raise():
    wanted = {'/Game/T/Idle': '/Game/MT/X/R/X_Idle', '/Game/T/Jog': '/Game/MT/X/R/X_Jog'}
    ok = [_row('/Game/T/Idle', '/Game/MT/X/R/X_Idle'), _row('/Game/T/Jog', '/Game/MT/X/R/X_Jog')]
    assert core.batch_problems(wanted, ok) == []
    # an entry for an asset nobody requested (a referenced asset the batch pulled in) is a problem: nothing expects,
    # saves or deletes it; batch_unexpected names its output for the cleanup
    ref = _row('/Game/T/Ref', '/Game/MT/X/R/X_Ref', requested=False)
    assert core.batch_problems(wanted, ok + [ref]) == [
        '/Game/T/Ref: a result for a clip that was not requested (made /Game/MT/X/R/X_Ref)']
    assert core.batch_unexpected(wanted, ok + [ref]) == ['/Game/MT/X/R/X_Ref']
    assert core.batch_unexpected(wanted, ok) == []
    skipped = [ok[0], _row('/Game/T/Jog', 'None', 'SKIPPED', reason='not an animation asset')]
    assert core.batch_problems(wanted, skipped) == ['/Game/T/Jog: SKIPPED (not an animation asset)']
    failed_ref = ok + [_row('/Game/T/Ref', 'None', 'IKRetargetBatchAssetStatus.FAILED', requested=False)]
    assert core.batch_problems(wanted, failed_ref) == [
        '/Game/T/Ref: FAILED (no reason given)', '/Game/T/Ref: a result for a clip that was not requested (made nothing)']
    assert core.batch_problems(wanted, ok[:1]) == ['/Game/T/Jog: 0 batch results, wanted 1']
    moved = [ok[0], _row('/Game/T/Jog', '/Game/MT/X/R/X_Jog_1')]
    assert core.batch_problems(wanted, moved) == ['/Game/T/Jog: retargeted to /Game/MT/X/R/X_Jog_1, wanted '
                                                  '/Game/MT/X/R/X_Jog']
    assert core.batch_unexpected(wanted, moved) == ['/Game/MT/X/R/X_Jog_1']


def test_the_bake_and_in_place_outputs_are_known_before_they_run():
    cdo = {'Anim_Idle': '/Game/T/Idle.Idle', 'Anim_Jump': None, 'Anim_Fall': None, 'Anim_Land': '/Game/T/Land'}
    clips = core.source_clips(cdo, TEMPLATE['samples'])
    assert core.bake_destinations(clips, '/Game/MT/X/Retargeted/', 'X_') == {
        '/Game/T/Idle': '/Game/MT/X/Retargeted/X_Idle', '/Game/T/Land': '/Game/MT/X/Retargeted/X_Land',
        '/Game/T/Jog_Fwd': '/Game/MT/X/Retargeted/X_Jog_Fwd'}
    assert core.inplace_outputs(cdo, TEMPLATE['samples'], '/Game/MT/X/Retargeted/', 'X_', '/Game/MT/X/InPlace/') == {
        '/Game/MT/X/Retargeted/X_Idle': '/Game/MT/X/InPlace/X_Idle_InPlace',
        '/Game/MT/X/Retargeted/X_Land': '/Game/MT/X/InPlace/X_Land_InPlace',
        '/Game/MT/X/Retargeted/X_Jog_Fwd': '/Game/MT/X/InPlace/X_Jog_Fwd_InPlace'}


def test_the_motion_check_has_one_bone_pair_per_chain_on_both_rigs():
    src = [['Spine', 'spine_01', 'spine_05'], ['Head', 'head', 'head'], ['LeftLeg', 'thigh_l', 'ball_l'],
           ['LeftThumb', 'thumb_01_l', 'thumb_03_l']]
    tgt = [('Spine', 'spine_01', 'spine_03'), ('Head', 'head', 'head'), ('LeftLeg', 'thigh_l', 'foot_l'),
           ('RightArm', 'upperarm_r', 'hand_r')]
    # every chain on both rigs by its END bone, each rig its own names, plus the left thigh; a chain on one rig only
    # has no pair (the retargeter check refuses a target chain with no source)
    assert core.vary_pairs(src, tgt) == [('head', 'head'), ('ball_l', 'foot_l'), ('spine_05', 'spine_03'),
                                         ('thigh_l', 'thigh_l')]
    # the right leg's start bone gets the same extra witness the left thigh has
    src.append(['RightLeg', 'thigh_r', 'ball_r'])
    tgt.append(('RightLeg', 'Thigh_R', 'Foot_R'))
    assert core.vary_pairs(src, tgt)[-2:] == [('thigh_l', 'thigh_l'), ('thigh_r', 'Thigh_R')]
    assert ('ball_r', 'Foot_R') in core.vary_pairs(src, tgt)


def test_a_failed_bake_deletes_what_it_made_named_or_only_inventoried_and_nothing_older():
    exp = ['/Game/MT/X/R/X_Idle', '/Game/MT/X/R/X_Jog']
    before = core.bake_inventory(['/Game/MT/X/R/Old.Old'], ['/Game/MT/X/R/OnDiskOnly'])
    assert before == ['/Game/MT/X/R/Old', '/Game/MT/X/R/OnDiskOnly']
    # registry (unsaved in-memory) and disk together; the destinations and what the reply named always go
    after = core.bake_inventory(['/Game/MT/X/R/Old', '/Game/MT/X/R/X_Idle', '/Game/MT/X/R/InMemory'],
                                ['/Game/MT/X/R/OnDiskOnly', '/Game/MT/X/R/X_Half'])
    assert core.bake_leftovers(exp, before, after, ['/Game/MT/X/R/X_Ref']) == [
        '/Game/MT/X/R/InMemory', '/Game/MT/X/R/X_Half', '/Game/MT/X/R/X_Idle', '/Game/MT/X/R/X_Jog',
        '/Game/MT/X/R/X_Ref']
    # nothing found after: still the destinations
    assert core.bake_leftovers(exp, before, before) == exp


def test_additive_template_clips_are_refused_by_name_and_plain_ones_pass():
    assert core.additive_problems({'/Game/T/Idle': 'AAT_NONE', '/Game/T/Jog': 'AdditiveAnimationType.AAT_NONE'}) == []
    bad = core.additive_problems({'/Game/T/Idle': 'AAT_NONE', '/Game/T/Aim': 'AAT_ROTATION_OFFSET_MESH_SPACE',
                                  '/Game/T/Gone': None})
    assert len(bad) == 1 and '/Game/T/Aim (AAT_ROTATION_OFFSET_MESH_SPACE)' in bad[0] and '/Game/T/Gone' in bad[0]


def test_missing_package_files_are_gaps():
    assert core.missing_files(['/Game/A', '/Game/B', '/Game/A', None], lambda p: p == '/Game/A') == [
        'not on disk: /Game/B']


def _abp_read(spec, **over):
    d = {'path': spec['path'], 'parent': spec['parent_class'], 'skeleton': spec['skeleton'] + '.SK_X',
         'compiled': True, 'status': 'BS_UP_TO_DATE', 'cdo': {v: p + '.' + core.clip_name(p)
                                                               for v, p in spec['cdo'].items()}}
    d.update(over)
    return d


def test_an_abp_read_back_must_match_its_parent_skeleton_clips_and_have_compiled_clean():
    spec = core.abp_spec('/Game/MT/X/ABP_MT_X', '/Game/MT/X/ABP_MT_X.ABP_MT_X_C', '/Game/MT/ABP_T.ABP_T_C',
                         '/Game/X/SK_X', {'Anim_Idle': '/Game/MT/X/Retargeted/X_Idle', core.BS_VAR: BS})
    assert core.abp_mismatch(spec, _abp_read(spec)) == []
    assert core.abp_mismatch(spec, _abp_read(spec, status='BlueprintStatus.BS_UP_TO_DATE')) == []
    # up to date with warnings is compiled too (UBlueprint::IsUpToDate), accepted and reported
    warned = _abp_read(spec, status='BS_UP_TO_DATE_WITH_WARNINGS')
    assert core.abp_mismatch(spec, warned) == [] and core.abp_compiled_with_warnings(warned)
    assert not core.abp_compiled_with_warnings(_abp_read(spec))
    for status in ('BS_ERROR', 'BS_DIRTY', 'BS_UNKNOWN', 'BS_BEING_CREATED'):
        assert any('compile status' in d for d in core.abp_mismatch(spec, _abp_read(spec, status=status))), status
    assert any('did not compile' in d for d in core.abp_mismatch(spec, _abp_read(spec, compiled=False)))
    assert any('parent' in d for d in core.abp_mismatch(spec, _abp_read(spec, parent='/Script/Engine.AnimInstance')))
    # a CDO value that did not take is a mismatch (realize then deletes the ABP, unsaved)
    assert core.abp_mismatch(spec, _abp_read(spec, cdo={'Anim_Idle': None, core.BS_VAR: BS})) == [
        'ABP Anim_Idle is None, wanted /Game/MT/X/Retargeted/X_Idle']
    assert core.blueprint_status('BS_UpToDate') == core.blueprint_status('BS_UP_TO_DATE') == 'UPTODATE'


def test_every_editor_action_the_driver_runs_exists_and_nothing_reaches_the_old_plugin():
    driver = open(os.path.join(HERE, 'hero_onboard.py')).read() + open(os.path.join(HERE, 'hero_kinds.py')).read()
    editor = open(os.path.join(HERE, 'hero_onboard_editor.py')).read()
    used = set(re.findall(r"_?editor\((?:ctx|self|ops, rig), '(\w+)'", driver))
    table = re.search(r'ACTIONS = \{(.*?)\}', editor, re.S).group(1)
    have = set(re.findall(r"'(\w+)': \w+", table))
    assert len(used) >= 12 and used <= have, sorted(used - have)
    assert 'MT_RESULT = ACTIONS[_action]' in editor
    # nothing in the editor half deletes: the host's one delete is asset_pipeline.delete_asset
    assert 'delete_asset(' not in editor and "'delete'" not in table and "'load'" not in table
