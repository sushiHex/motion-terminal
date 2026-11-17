"""A held prop gets a grip with no hand-set data: intake finds the prop, dyn_hero derives the hands, the ABP build picks
the hero's own non-additive jog pose."""
import hero_intake_core as core
import dynamics_graph as abp
import dyn_hero


def test_held_props_are_prop_bones_under_a_hand():
    skel = {'bones': ['root', 'hand_r', 'weapon', 'hand_l', 'index_01_l', 'sword_tip', 'belt_sword'],
            'parents': {'hand_r': 'root', 'weapon': 'hand_r', 'hand_l': 'root', 'index_01_l': 'hand_l',
                        'sword_tip': 'index_01_l', 'belt_sword': 'root'}}
    roles = {'hand_l': 'hand_l', 'hand_r': 'hand_r'}
    got = core.held_props(skel, roles, {'weapon': 725, 'sword_tip': 30, 'belt_sword': 400, 'index_01_l': 200})
    assert got == [{'bone': 'weapon', 'hand': 'hand_r', 'verts': 725},
                   {'bone': 'sword_tip', 'hand': 'hand_l', 'verts': 30}]          # a sheathed sword is not held
    assert core.held_props(skel, roles, {'weapon': 5}) == []                     # moves no geometry


def test_a_held_prop_derives_a_grip_and_hand_set_data_wins():
    g = dyn_hero._derived_grip({'held_props': [{'bone': 'weapon', 'hand': 'hand_r', 'verts': 725}]})
    assert g['hands'] == ['hand_r'] and g['pose'] is None and g['derived'] and 'weapon in hand_r' in g['why']
    assert dyn_hero._derived_grip({}) is None
    k = dyn_hero.resolve('ParagonKhaimera')
    assert k['grip']['pose'].endswith('/Jog_Fwd') and 'derived' not in k['grip']       # his extras


def test_grip_pose_is_the_heros_own_non_additive_jog():
    seqs = [('/Game/H/Anims/Idle', 'AAT_LocalSpaceBase'), ('/Game/H/Anims/Jog_Fwd_Combat', 'AAT_None'),
            ('/Game/H/Anims/Jog_Fwd', 'AAT_None'), ('/Game/H/Anims/Jog_Fwd_Additive', 'AAT_LocalSpaceBase')]
    assert abp.pick_grip_pose(seqs) == '/Game/H/Anims/Jog_Fwd'
    assert abp.pick_grip_pose([('/Game/H/Idle_Relaxed', 'AAT_None')]) == '/Game/H/Idle_Relaxed'
    assert abp.pick_grip_pose([('/Game/H/Idle', 'AAT_LocalSpaceBase')]) is None
    # a generated retarget never supplies the grip it is supposed to be repaired by
    assert abp.pick_grip_pose([('/Game/MT/H/Retargeted/H_MF_Jog_Fwd', 'AAT_NONE'),
                               ('/Game/H/Anims/Walk_Combat_Idle', 'AAT_NONE')]) == '/Game/H/Anims/Walk_Combat_Idle'
