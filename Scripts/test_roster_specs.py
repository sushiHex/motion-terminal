"""Every hero profile in the repository goes through every pure spec builder: the pipeline is the same code for
each hero, and this is where that is shown rather than claimed.

A profile records a hero's roles (which bone is the pelvis, the left hand, ...), not its bone hierarchy, which only
the editor has. So each hero gets the plain biped its own roles imply (its own bone names, the usual parenting), and
everything that is pure runs on that: the retarget chains, the IK rig and retargeter specs, the reaction body, the
whole dynamics graph spec, and the engine names that graph uses. What needs the real hierarchy (shapes, twist bones,
a second body) is the editor's to check, in the onboarding stages.
"""
import json
import os

import pytest

import animgraph_spec as ag
import dyn_hero
import dynamics_graph
import engine_contract as ec
import hero_onboard_core as core
import hit_reaction_core as hrc

HEROES = sorted(f[:-5] for f in os.listdir(dyn_hero.PROFILES) if f.endswith('.json'))
SOURCE_CHAINS = ['Spine', 'Neck', 'Head', 'LeftClavicle', 'RightClavicle', 'LeftArm', 'RightArm', 'LeftLeg', 'RightLeg']


def biped(roles):
    """{bone: parent} for the biped these roles imply, in the hero's own bone names, with the foot-IK bones every
    onboarded hero has."""
    spine = roles['spine']
    par = {roles['pelvis']: 'root', spine[0]: roles['pelvis'], 'ik_foot_root': 'root'}
    for a, b in zip(spine, spine[1:]):
        par[b] = a
    if roles.get('neck'):
        par[roles['neck']] = spine[-1]
    par[roles['head']] = roles.get('neck') or spine[-1]
    for s in ('l', 'r'):
        par[roles['clavicle_' + s]] = spine[-1]
        par[roles['upperarm_' + s]] = roles['clavicle_' + s]
        par[roles['lowerarm_' + s]] = roles['upperarm_' + s]
        par[roles['hand_' + s]] = roles['lowerarm_' + s]
        par[roles['thigh_' + s]] = roles['pelvis']
        par[roles['calf_' + s]] = roles['thigh_' + s]
        par[roles['foot_' + s]] = roles['calf_' + s]
        if roles.get('ball_' + s):
            par[roles['ball_' + s]] = roles['foot_' + s]
        par['ik_foot_' + s] = 'ik_foot_root'
    return par


def test_the_repository_has_a_roster_to_run():
    assert len(HEROES) >= 5, HEROES


@pytest.mark.parametrize('rig', HEROES)
def test_every_pure_builder_runs_for_the_hero(rig):
    h = dyn_hero.resolve(rig)
    prof = h['profile']
    assert prof.get('status') == 'ok', (rig, prof.get('reasons'))
    parents = biped(prof['roles'])

    chains = core.retarget_chains(prof['roles'], parents)
    names = [c[0] for c in chains]
    assert len(names) == len(set(names)) and {'Spine', 'LeftArm', 'RightArm', 'LeftLeg', 'RightLeg'} <= set(names)
    for _name, start, end in chains:
        assert start in parents and end in parents, (rig, start, end)

    rig_spec = core.ik_rig_spec(h['ik_rig'], h['mesh'], prof['roles']['pelvis'], chains, root_motion='root')
    rtg_spec = core.retargeter_spec(h['retargeter'], dyn_hero.SOURCE_IK_RIG, h['ik_rig'], prof['roles']['pelvis'],
                                    names, SOURCE_CHAINS)
    json.dumps([rig_spec, rtg_spec])                     # both go to the editor as JSON

    reaction = hrc.physics_spec(prof, parents)
    assert reaction['bodies'] and reaction['constraints']
    bones = {b['bone'] for b in reaction['bodies']}
    assert all(c['child'] in bones and c['parent'] in bones for c in reaction['constraints']), rig

    spec = dynamics_graph.dynamics_spec(h, None, (), reaction, reaction.get('leg_ik') or [], None)
    ids = [n['id'] for n in spec['nodes']] + [g['id'] for g in spec['variable_gets']]
    assert len(ids) == len(set(ids))
    fed, consumers = set(), {}
    for src, src_pin, dst, dst_pin in spec['links']:
        assert src in ids and dst in ids, (rig, src, dst)
        assert (dst, dst_pin) not in fed, 'an input is linked twice: %s.%s' % (dst, dst_pin)
        fed.add((dst, dst_pin))
        if src_pin in ('Pose', 'ComponentPose'):
            consumers[(src, src_pin)] = consumers.get((src, src_pin), 0) + 1
    assert spec['output'][0] in ids and max(consumers.values()) == 1, rig
    for node in spec['nodes']:
        for path, text in node['properties']:
            ag.parse_export(text)                        # every value is export text the comparison can read
            assert (node['id'], path.split('.')[0]) not in fed, 'set and linked: %s.%s' % (node['id'], path)

    # whatever engine names this hero's graph uses are in the checked contract: no hero reaches a name the engine
    # test does not cover
    committed = ec.read()['anim_nodes']
    for cls, paths in ec.from_spec(spec).items():
        assert cls in committed, (rig, cls)
        assert paths <= set(committed[cls]), (rig, cls, sorted(paths - set(committed[cls])))
