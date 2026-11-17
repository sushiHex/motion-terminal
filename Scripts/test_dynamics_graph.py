"""dynamics_graph.dynamics_spec: the dynamics ABP's whole AnimGraph as one animgraph_spec spec, without the editor."""
import json

import pytest

import animgraph_spec as ag
import dyn_hero
import hit_reaction_core as hrc
import dynamics_graph as abp
import test_hit_reaction_core as thr

ROOTS = ['index_01_r', 'weapon_r']


def _hero():
    return dyn_hero.resolve('ParagonKhaimera')


def _reaction(h, legs):
    """The hero's reaction body spec, with the leg IK (legs) or with the thighs anchored."""
    prof = h['profile']
    par = thr._legs(thr._parents(prof), prof['roles']) if legs else thr._parents(prof)
    spec = hrc.physics_spec(prof, par)
    assert bool(spec['leg_ik']) == legs
    return spec


def _spec(grip=False, reaction=False, legs=False, over=None):
    h = _hero()
    r = _reaction(h, legs) if reaction else None
    return h, r, abp.dynamics_spec(h, '/Game/H/Anims/Jog_Fwd' if grip else None, ROOTS if grip else (), r,
                                   (r or {}).get('leg_ik') or [], over)


def _classes(spec):
    return {n['id']: n['class'] for n in spec['nodes']}


CASES = [dict(), dict(grip=True), dict(reaction=True), dict(grip=True, reaction=True, legs=True)]


def test_without_grip_or_reaction_the_input_feeds_the_dynamics_rig():
    h, _r, spec = _spec()
    assert _classes(spec) == {'input': abp.INPUT_NODE, 'dynamics': abp.CONTROL_RIG_NODE}
    assert spec['variable_gets'] == [{'id': 'dynamics_alpha', 'variable': abp.ALPHA_VAR}]
    assert spec['variables'] == [{'name': abp.ALPHA_VAR, 'type': 'float', 'default': '0.0', 'instance_editable': True}]
    assert spec['links'] == [['input', 'Pose', 'dynamics', 'Source'],
                             ['dynamics_alpha', abp.ALPHA_VAR, 'dynamics', abp.ALPHA_VAR]]
    assert spec['output'] == ['dynamics', 'Pose'] and spec['skeleton'] == h['skeleton']
    rig = spec['nodes'][-1]
    assert rig['exposed'] == [abp.ALPHA_VAR]
    assert rig['properties'] == [['ControlRigAssetReference',
                                  '(BlueprintRigClass="%s",ControlRigAsset=None)' % h['cr_class']]]


def test_a_grip_layers_the_hand_subtrees_of_a_held_pose_over_the_input():
    _h, _r, spec = _spec(grip=True)
    assert _classes(spec) == {'input': abp.INPUT_NODE, 'grip': abp.SEQUENCE_NODE, 'layer': abp.LAYER_NODE,
                              'dynamics': abp.CONTROL_RIG_NODE}
    layer = next(n for n in spec['nodes'] if n['id'] == 'layer')
    assert layer['poses'] == 1
    # the depth is written even at 0: it is a decision (GRIP_DEPTH), and the description reads every member back
    assert layer['properties'] == [['LayerSetup', '((BranchFilters=((BoneName="index_01_r",BlendDepth=0),'
                                                  '(BoneName="weapon_r",BlendDepth=0))))'],
                                   ['CurveBlendOption', 'UseBasePose']]
    assert ag.parse_export(layer['properties'][0][1]) == [{'BranchFilters': [
        {'BoneName': 'index_01_r', 'BlendDepth': '0'}, {'BoneName': 'weapon_r', 'BlendDepth': '0'}]}]
    grip = next(n for n in spec['nodes'] if n['id'] == 'grip')
    assert grip['properties'] == [['Sequence', '/Game/H/Anims/Jog_Fwd.Jog_Fwd']]
    assert ['input', 'Pose', 'layer', 'BasePose'] in spec['links']
    assert ['grip', 'Pose', 'layer', 'BlendPoses_0'] in spec['links']
    assert ['layer', 'Pose', 'dynamics', 'Source'] in spec['links']
    assert abp.layer_setup(['a'], 8) == '((BranchFilters=((BoneName="a",BlendDepth=8))))'
    with pytest.raises(ValueError):
        abp.layer_setup([])


def test_a_reaction_runs_the_rigid_body_in_component_space_with_its_alpha_linked():
    h, r, spec = _spec(reaction=True, over={'strength': 12.0})
    assert _classes(spec) == {'input': abp.INPUT_NODE, 'l2c': abp.L2C_NODE, 'rb': abp.RBWC_NODE,
                              'c2l': abp.C2L_NODE, 'dynamics': abp.CONTROL_RIG_NODE}
    assert [n['id'] for n in spec['nodes']] == ['input', 'l2c', 'rb', 'c2l', 'dynamics']
    assert spec['links'] == [['input', 'Pose', 'l2c', 'LocalPose'], ['l2c', 'ComponentPose', 'rb', 'ComponentPose'],
                             ['rb', 'Pose', 'c2l', 'ComponentPose'],
                             ['reaction_alpha', abp.REACTION_ALPHA_VAR, 'rb', 'Alpha'],
                             ['c2l', 'Pose', 'dynamics', 'Source'],
                             ['dynamics_alpha', abp.ALPHA_VAR, 'dynamics', abp.ALPHA_VAR]]
    assert [v['name'] for v in spec['variables']] == [abp.ALPHA_VAR, abp.REACTION_ALPHA_VAR]
    rb = dict(next(n for n in spec['nodes'] if n['id'] == 'rb')['properties'])
    # hit_reaction_core's text, unchanged, the override included
    assert rb == {'OverridePhysicsAsset': h['physics_asset'] + '.' + h['physics_asset'].rsplit('/', 1)[1],
                  'bEnableControls': 'True', 'bEnableWorldGeometry': 'False',
                  'CharacterSetupData.LimbSetupData': hrc.limb_setup(h['profile'], r),
                  'CharacterSetupData.DefaultParentSpaceControlData': hrc.parent_space_control(12.0),
                  'CharacterSetupData.DefaultWorldSpaceControlData': hrc.world_space_control(),
                  'InitialControlAndBodyModifierUpdates': hrc.initial_updates(h['profile'], r),
                  'CharacterSetupData.DefaultBodyModifierData': hrc.body_modifier()}
    assert 'Alpha' not in rb


def test_leg_ik_copies_the_feet_before_the_reaction_and_plants_them_after_through_the_reach_rig():
    h, r, spec = _spec(grip=True, reaction=True, legs=True)
    assert [n['id'] for n in spec['nodes']] == ['input', 'grip', 'layer', 'l2c', 'copy_0', 'copy_1', 'rb',
                                                'reach_c2l', 'reach', 'reach_l2c', 'leg_ik', 'c2l', 'dynamics']
    c = _classes(spec)
    assert (c['copy_0'], c['copy_1'], c['leg_ik'], c['reach'], c['reach_c2l'], c['reach_l2c']) == (
        abp.COPY_BONE_NODE, abp.COPY_BONE_NODE, abp.LEG_IK_NODE, abp.CONTROL_RIG_NODE, abp.C2L_NODE, abp.L2C_NODE)
    pose = [link for link in spec['links'] if link[0] not in ('reaction_alpha', 'dynamics_alpha')]
    assert pose == [['input', 'Pose', 'layer', 'BasePose'], ['grip', 'Pose', 'layer', 'BlendPoses_0'],
                    ['layer', 'Pose', 'l2c', 'LocalPose'], ['l2c', 'ComponentPose', 'copy_0', 'ComponentPose'],
                    ['copy_0', 'Pose', 'copy_1', 'ComponentPose'], ['copy_1', 'Pose', 'rb', 'ComponentPose'],
                    ['rb', 'Pose', 'reach_c2l', 'ComponentPose'], ['reach_c2l', 'Pose', 'reach', 'Source'],
                    ['reach', 'Pose', 'reach_l2c', 'LocalPose'], ['reach_l2c', 'ComponentPose', 'leg_ik', 'ComponentPose'],
                    ['leg_ik', 'Pose', 'c2l', 'ComponentPose'], ['c2l', 'Pose', 'dynamics', 'Source']]
    copies, definition = hrc.leg_ik_nodes(r['leg_ik'])
    copy0 = dict(next(n for n in spec['nodes'] if n['id'] == 'copy_0')['properties'])
    assert copy0 == {'SourceBone': '(BoneName="%s")' % copies[0][0], 'TargetBone': '(BoneName="%s")' % copies[0][1],
                     'bCopyTranslation': 'True', 'bCopyRotation': 'True', 'ControlSpace': 'BCS_ComponentSpace'}
    assert next(n for n in spec['nodes'] if n['id'] == 'leg_ik')['properties'] == [['LegsDefinition', definition]]
    reach = next(n for n in spec['nodes'] if n['id'] == 'reach')
    assert reach['properties'][0] == ['ControlRigAssetReference', abp.rig_reference(h['reach_cr_class'])]
    assert reach['exposed'] == []


@pytest.mark.parametrize('case', CASES, ids=lambda c: '+'.join(sorted(c)) or 'bare')
def test_every_spec_is_one_the_build_tool_accepts(case):
    _h, _r, spec = _spec(**case)
    ids = [n['id'] for n in spec['nodes']] + [g['id'] for g in spec['variable_gets']]
    assert len(ids) == len(set(ids)) and ag.OUTPUT not in ids
    for f, _fp, t, _tp in spec['links'] + [spec['output'] + [ag.OUTPUT, ag.OUTPUT_PIN]]:
        assert f in ids and (t in ids or t == ag.OUTPUT)
    inputs = [(t, tp) for _f, _fp, t, tp in spec['links']]
    assert len(inputs) == len(set(inputs))                                    # no input linked twice
    outputs = [(f, fp) for f, fp, _t, _tp in spec['links'] + [spec['output'] + [ag.OUTPUT, ag.OUTPUT_PIN]]
               if fp in ('Pose', 'ComponentPose')]
    assert len(outputs) == len(set(outputs))                                  # a pose feeds one consumer
    for n in spec['nodes']:                                                   # no property both set and linked
        assert not {p.split('.')[0].split('[')[0] for p, _v in n['properties']} & {tp for t, tp in inputs
                                                                                   if t == n['id']}
        if n['class'] == abp.CONTROL_RIG_NODE:                                # structure first
            assert n['properties'][0][0] == 'ControlRigAssetReference'
        for _p, v in n['properties']:
            ag.parse_export(v)                                                # every value reads to its end
    gets = {g['variable'] for g in spec['variable_gets']}
    assert gets <= {v['name'] for v in spec['variables']}
    assert json.loads(json.dumps(spec)) == spec
    assert spec == _spec(**case)[2]                                           # stable: the same hero, the same spec


def test_the_reaction_chain_links_each_node_in_its_own_space():
    # source -> L2C -> CopyBone -> RBwC -> C2L -> reach rig -> L2C -> LegIK -> C2L; the rig takes a local Source
    chain = ['src', 'l2c', 'copy', 'rb', 'mid', 'rig', 'back', 'ik', 'c2l']
    assert abp.pose_links(chain, ['l2c', 'back'], 'rig') == [
        ('src', 'Pose', 'l2c', 'LocalPose'), ('l2c', 'ComponentPose', 'copy', 'ComponentPose'),
        ('copy', 'Pose', 'rb', 'ComponentPose'), ('rb', 'Pose', 'mid', 'ComponentPose'),
        ('mid', 'Pose', 'rig', 'Source'),
        ('rig', 'Pose', 'back', 'LocalPose'), ('back', 'ComponentPose', 'ik', 'ComponentPose'),
        ('ik', 'Pose', 'c2l', 'ComponentPose')]
    assert abp.pose_links(['src', 'l2c', 'rb', 'c2l'], ['l2c']) == [            # no leg IK: the reaction alone
        ('src', 'Pose', 'l2c', 'LocalPose'), ('l2c', 'ComponentPose', 'rb', 'ComponentPose'),
        ('rb', 'Pose', 'c2l', 'ComponentPose')]


def test_bones_read_with_their_parents_give_the_grip_roots():
    pairs = [['root', None], ['hand_r', 'root'], ['index_01_r', 'hand_r'], ['weapon_r', 'hand_r'],
             ['hand_twist_r', 'hand_r'], ['index_02_r', 'index_01_r']]
    bones = abp.bones_from_parents(pairs)
    assert bones[2] == {'index': 2, 'name': 'index_01_r', 'parent_index': 1} and bones[0]['parent_index'] is None
    assert abp.grip_roots(bones, ('hand_r',)) == ['index_01_r', 'weapon_r']
    with pytest.raises(RuntimeError):
        abp.grip_roots(bones, ('hand_l',))
    assert abp.object_path('/Game/A/B') == '/Game/A/B.B' and abp.object_path('/Game/A/B.B') == '/Game/A/B.B'
