"""Intake decisions (hero_intake_core.py): role classifier, chain discovery, primary mesh, determinism, rig-name grep.
Synthetic skeletons cover the naming conventions; the Khaimera ground truth (the Dynamics spike's hand-picked chain
roots) runs when its machine-local facts file exists, and is skipped with the reason otherwise."""
import glob
import json
import os
import re

import pytest

import hero_intake_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _skel(pairs):
    """[(bone, parent), ...] in hierarchy order -> {'bones', 'parents'}."""
    return {'bones': [b for b, _ in pairs], 'parents': {b: p for b, p in pairs}}


def _biped(prefix='', names=None):
    n = names or {}
    g = lambda k: prefix + n.get(k, k)  # noqa: E731
    pairs = [(g('root'), None), (g('pelvis'), g('root')), (g('spine_01'), g('pelvis')), (g('spine_02'), g('spine_01')),
             (g('neck_01'), g('spine_02')), (g('head'), g('neck_01'))]
    for s in 'lr':
        pairs += [(g('clavicle_' + s), g('spine_02')), (g('upperarm_' + s), g('clavicle_' + s)),
                  (g('lowerarm_' + s), g('upperarm_' + s)), (g('hand_' + s), g('lowerarm_' + s)),
                  (g('index_01_' + s), g('hand_' + s)), (g('upperarm_twist_01_' + s), g('upperarm_' + s)),
                  (g('thigh_' + s), g('pelvis')), (g('calf_' + s), g('thigh_' + s)), (g('foot_' + s), g('calf_' + s)),
                  (g('ball_' + s), g('foot_' + s))]
    return pairs


MIXAMO = {'pelvis': 'Hips', 'spine_01': 'Spine', 'spine_02': 'Spine1', 'neck_01': 'Neck', 'head': 'Head',
          'clavicle_l': 'LeftShoulder', 'upperarm_l': 'LeftArm', 'lowerarm_l': 'LeftForeArm', 'hand_l': 'LeftHand',
          'index_01_l': 'LeftHandIndex1', 'upperarm_twist_01_l': 'LeftArmTwist', 'thigh_l': 'LeftUpLeg',
          'calf_l': 'LeftLeg', 'foot_l': 'LeftFoot', 'ball_l': 'LeftToeBase',
          'clavicle_r': 'RightShoulder', 'upperarm_r': 'RightArm', 'lowerarm_r': 'RightForeArm', 'hand_r': 'RightHand',
          'index_01_r': 'RightHandIndex1', 'upperarm_twist_01_r': 'RightArmTwist', 'thigh_r': 'RightUpLeg',
          'calf_r': 'RightLeg', 'foot_r': 'RightFoot', 'ball_r': 'RightToeBase', 'root': 'Root'}


def test_tokens_split_camel_namespace_and_digits():
    assert core.tokens('mixamorig:LeftUpLeg') == ['left', 'up', 'leg']
    assert core.tokens('loin_cloth_bk_01') == ['loin', 'cloth', 'bk', '01']
    assert core.tokens('Spine1') == ['spine', '1']


@pytest.mark.parametrize('name,expected', [
    ('upperarm_l', ('upperarm', 'l')), ('LeftForeArm', ('lowerarm', 'l')), ('LeftArm', ('upperarm', 'l')),
    ('LeftUpLeg', ('thigh', 'l')), ('RightLeg', ('calf', 'r')), ('Hips', ('pelvis', None)),
    ('upperarm_twist_01_l', (None, None)), ('LeftHandIndex1', (None, None)), ('HeadTop_End', (None, None)),
    ('chest_cloth_01', (None, None)), ('hip_l', (None, None)), ('thigh', (None, None)),
    # accessories, quadruped sides and two-sided names never claim a body role
    ('arm_guard_l', (None, None)), ('LeftArmArmor', (None, None)), ('hip_bl', (None, None)),
    ('thigh_fl', (None, None)), ('hand_l_r', (None, None)),
])
def test_role_of(name, expected):
    assert core.role_of(name) == expected


@pytest.mark.parametrize('prefix,names', [('', None), ('mixamorig:', MIXAMO)])
def test_classify_roles_resolves_ue_and_mixamo_bipeds(prefix, names):
    skel = _skel(_biped(prefix, names))
    roles, unresolved = core.classify_roles(skel)
    assert unresolved == []
    want = lambda k: prefix + (names or {}).get(k, k)  # noqa: E731
    assert roles['pelvis'] == want('pelvis') and roles['head'] == want('head')
    assert roles['spine'] == [want('spine_01'), want('spine_02')]
    assert roles['hand_l'] == want('hand_l') and roles['foot_r'] == want('foot_r')


def test_spine_is_topology_not_names():
    """A 'chest'/'spine'-named bone hanging off the torso must not become the spine top (seen on Gideon/Rampage)."""
    pairs = _biped()
    pairs.insert(4, ('chest_flap_01', 'spine_02'))
    pairs.insert(5, ('fin_spine_a_01', 'chest_flap_01'))
    roles, unresolved = core.classify_roles(_skel(pairs))
    assert unresolved == [] and roles['spine'] == ['spine_01', 'spine_02']


def test_missing_limb_is_unresolved_not_guessed():
    pairs = [p for p in _biped() if not p[0].endswith('_r') or p[0].startswith('clavicle')]
    roles, unresolved = core.classify_roles(_skel(pairs))
    assert {'thigh_r', 'hand_r'} <= set(unresolved)


def _weights(skel, n=100):
    return {b: n for b in skel['bones']}


def test_chains_rooted_subgraph_preset_and_exclusions():
    pairs = _biped() + [('cape_01', 'spine_02'), ('cape_02', 'cape_01'), ('cape_feather', 'cape_02'),
                        ('weapon_r', 'hand_r'), ('weapon_tail_01', 'weapon_r'), ('weapon_tail_02', 'weapon_tail_01'),
                        ('gear_01', 'pelvis'), ('gear_02', 'gear_01'),
                        ('jaw', 'head'), ('hair_01', 'head'), ('hair_02', 'hair_01'),
                        ('flap', 'pelvis')]
    i = pairs.index(('head', 'neck_01'))       # a second neck bone between neck_01 and the head, as on the Paragon rigs
    pairs[i:i + 1] = [('neck_02', 'neck_01'), ('head', 'neck_02')]
    skel = _skel(pairs)
    roles, _ = core.classify_roles(skel)
    chains, unclassified, single = core.discover_chains(skel, roles, _weights(skel))
    by_root = {c['root']: c for c in chains}
    # a tail hanging from the weapon in the hand is in (held-prop rule); the weapon bone itself and the jaw are out
    assert set(by_root) == {'cape_01', 'hair_01', 'weapon_tail_01'}
    assert by_root['weapon_tail_01']['bones'] == ['weapon_tail_01', 'weapon_tail_02']
    assert by_root['cape_01']['bones'] == ['cape_01', 'cape_02', 'cape_feather'] and by_root['cape_01']['preset'] == 'cloth'
    assert by_root['hair_01']['preset'] == 'hair'
    assert [c['root'] for c in unclassified] == ['gear_01']             # no preset token: reported, not simulated
    assert [s['bone'] for s in single] == ['flap']                       # childless: cannot swing in this solver
    assert all(c['root'] != 'neck_02' for c in chains + unclassified)    # holds no body: a body ancestor never is


def test_parts_tip_ends_the_unbranched_run_and_topology_is_declared():
    """The harness tracks a part's tip: where a branching chain forks (loin_cloth_bk_03 -> two feathers), the fork bone,
    not a branch; a backend that takes only linear chains reads `topology`."""
    pairs = [('pelvis', None), ('loin_01', 'pelvis'), ('loin_02', 'loin_01'), ('loin_03', 'loin_02'),
             ('feather_l', 'loin_03'), ('feather_r', 'loin_03'), ('strip_01', 'pelvis'), ('strip_02', 'strip_01')]
    parents = _skel(pairs)['parents']
    got = core.parts([{'root': 'loin_01', 'bones': ['loin_01', 'loin_02', 'loin_03', 'feather_l', 'feather_r'],
                       'preset': 'cloth'},
                      {'root': 'strip_01', 'bones': ['strip_01', 'strip_02'], 'preset': 'cloth'}], parents)
    loin, strip = got
    assert (loin['tip'], loin['topology'], loin['parent']) == ('loin_03', 'branching', 'pelvis')
    assert (strip['tip'], strip['topology']) == ('strip_02', 'linear')
    assert {p['backend'] for p in got} == {'cr_dynamics'} and loin['id'] == loin['root'] == 'loin_01'


def test_a_chain_branching_at_its_root_tracks_its_deepest_leaf():
    """The root is kinematic: a tip there reads 0.0 cm whatever the chain does (seen on Aurora's hair_mid_l_01)."""
    pairs = [('head', None), ('hair_01', 'head'), ('hair_02', 'hair_01'), ('hair_03', 'hair_02'),
             ('strand_01', 'hair_01'), ('strand_02', 'strand_01')]
    parents = _skel(pairs)['parents']
    bones = ['hair_01', 'hair_02', 'hair_03', 'strand_01', 'strand_02']
    assert core.spine_tip(parents, bones) == 'hair_03'                  # deepest branch; ties go to the first
    assert core.spine_tip(parents, ['hair_01']) == 'hair_01'


def _held_roots(extra):
    skel = _skel(_biped() + extra)
    roles, _ = core.classify_roles(skel)
    chains, unclassified, single = core.discover_chains(skel, roles, _weights(skel))
    return {c['root'] for c in chains + unclassified} | {s['bone'] for s in single}


def test_held_prop_rule_admits_prop_tails_but_not_fingers_or_props():
    roots = _held_roots([('weapon_r', 'hand_r'), ('weapon_tail_01', 'weapon_r'), ('weapon_tail_02', 'weapon_tail_01'),
                         ('sword_l', 'hand_l'), ('sword_gem', 'sword_l')])
    assert 'weapon_tail_01' in roots                # tail under a held weapon
    assert not roots & {'weapon_r', 'sword_l', 'sword_gem', 'index_01_r', 'index_01_l'}   # props, presetless, fingers
    # a prop parent named for the weapon kind counts too ('axe_r', 'blade' were once missed)
    assert 'tail_01' in _held_roots([('axe_r', 'hand_r'), ('tail_01', 'axe_r'), ('tail_02', 'tail_01')])


def test_held_prop_rule_rejects_self_certified_props_and_finger_descendants():
    # Both cases reproduced: a bone cannot qualify by its OWN prop token (no prop between it and the
    # hand), and an admitted root must not drag denied bones (fingers) into the simulation with its subtree.
    assert 'weapon_quiver_strap_01' not in _held_roots([('weapon_quiver_strap_01', 'hand_r'),
                                                        ('weapon_quiver_strap_02', 'weapon_quiver_strap_01')])
    assert 'tail_01' not in _held_roots([('weapon_r', 'hand_r'), ('tail_01', 'weapon_r'),
                                         ('index_tail_01', 'tail_01'), ('index_tail_02', 'index_tail_01')])
    # ...but a marker bone at the end of a tail is not anatomy: the tail stays (both names)
    for marker in ('tail_socket', 'tail_attach'):
        assert 'tail_01' in _held_roots([('weapon_r', 'hand_r'), ('tail_01', 'weapon_r'), ('tail_02', 'tail_01'),
                                         (marker, 'tail_02')]), marker


def test_flaps_with_a_preset_become_parts_and_head_feathers_are_admitted():
    pairs = _biped() + [('feather_01_l', 'head'), ('hood_l', 'head'), ('gem', 'spine_02'), ('tassel', 'spine_02')]
    skel = _skel(pairs)
    roles, _ = core.classify_roles(skel)
    chains, _, single = core.discover_chains(skel, roles, _weights(skel))
    assert {s['bone'] for s in single} == {'feather_01_l', 'gem', 'tassel'}     # a hood shell under the head stays out
    got = core.parts(chains, skel['parents'], single)
    flaps = {p['id']: p for p in got if p['kind'] == 'flap'}
    assert set(flaps) == {'feather_01_l', 'tassel'}                              # 'gem' has no preset: not simulated
    assert flaps['tassel'] == {'id': 'tassel', 'kind': 'flap', 'root': 'tassel', 'bones': ['tassel'], 'tip': 'tassel',
                               'parent': 'spine_02', 'topology': 'single', 'preset': 'cloth', 'backend': 'cr_dynamics'}


def test_parts_follow_chain_overrides():
    """A hand-authored override of `chains` governs what is simulated: parts are derived after overrides."""
    pairs = _biped() + [('cape_01', 'spine_02'), ('cape_02', 'cape_01')]
    skel = _skel(pairs)
    mesh = {'path': '/Game/H/M', 'skeleton': '/Game/H/S', 'hierarchy': skel, 'weighted_bones': _weights(skel),
            'bounds_cm': [80, 60, 180], 'post_process_abp': None, 'physics_asset': None, 'clothing_assets': []}
    facts = {'root': '/Game/H', 'skeletal_meshes': [mesh], 'anim_blueprints': [], 'sequences_by_skeleton': {}}
    plain = core.profile('H', facts, {})
    assert [p['id'] for p in plain['secondary']['parts']] == ['cape_01']
    off = core.profile('H', facts, {}, {'chains': {'value': [], 'by': 'tuning', 'why': 'no cape'}})
    assert off['secondary']['parts'] == []
    whole = core.profile('H', facts, {}, {'secondary': {'value': {'parts': []}, 'by': 'tuning', 'why': 'none'}})
    assert whole['secondary']['parts'] == []                       # a whole-section override is not overwritten


def test_chain_needs_weights_and_preset_comes_from_root_only():
    pairs = _biped() + [('extra_pelvis', 'pelvis'), ('extra_ponytail', 'extra_pelvis'), ('extra_tip', 'extra_ponytail'),
                        ('skirt_01', 'pelvis'), ('skirt_02', 'skirt_01')]
    skel = _skel(pairs)
    roles, _ = core.classify_roles(skel)
    w = _weights(skel)
    w['skirt_01'] = w['skirt_02'] = core.MIN_CHAIN_VERTS - 1
    chains, unclassified, _ = core.discover_chains(skel, roles, w)
    assert chains == [] and [c['root'] for c in unclassified] == ['extra_pelvis']


def test_one_chain_across_an_unweighted_spacer():
    """cape_01 -> spacer (unweighted) -> cape_03 must be ONE chain, not two overlapping ones."""
    pairs = _biped() + [('cape_01', 'spine_02'), ('spacer', 'cape_01'), ('cape_03', 'spacer'), ('cape_04', 'cape_03')]
    skel = _skel(pairs)
    roles, _ = core.classify_roles(skel)
    w = _weights(skel)
    w['spacer'] = 0
    chains, _, _ = core.discover_chains(skel, roles, w)
    assert [c['root'] for c in chains] == ['cape_01']
    assert chains[0]['bones'] == ['cape_01', 'spacer', 'cape_03', 'cape_04']


def test_baked_motion_flag():
    pairs = _biped() + [('tail_01', 'pelvis'), ('tail_02', 'tail_01')]
    skel = _skel(pairs)
    roles, _ = core.classify_roles(skel)
    chains, _, _ = core.discover_chains(skel, roles, _weights(skel), {'tail_01': 40.0, 'tail_02': 2.0})
    assert chains[0]['baked'] and chains[0]['baked_deg'] == 40.0


def _facts():
    skel = _skel(_biped() + [('cape_01', 'spine_02'), ('cape_02', 'cape_01')])
    mesh = lambda p, sk: {'path': p, 'skeleton': sk, 'physics_asset': None, 'post_process_abp': None,  # noqa: E731
                          'clothing_assets': [], 'bounds_cm': [80.0, 60.0, 180.0], 'hierarchy': skel,
                          'weighted_bones': _weights(skel)}
    return {'root': '/Game/Pack', 'anim_blueprints': [{'path': '/Game/Pack/ABP', 'target_skeleton': '/Game/Pack/SK'}],
            'sequences_by_skeleton': {'/Game/Pack/SK': 3, '/Game/Pack/Other_SK': 9},
            'skeletal_meshes': [mesh('/Game/Pack/Skins/A/Meshes/Hero_A', '/Game/Pack/SK'),
                                mesh('/Game/Pack/Meshes/Hero', '/Game/Pack/SK'),
                                mesh('/Game/Pack/Meshes/Prop', '/Game/Pack/Other_SK')],
            'skeletons': {}, 'motion': {}}


def test_primary_prefers_animbp_skeleton_then_shallowest_path():
    mesh, rule = core.primary(_facts())
    assert mesh['path'] == '/Game/Pack/Meshes/Hero' and '1 AnimBP' in rule


def test_profile_is_deterministic_and_counts_overrides():
    graph = '[AnimGraphNode_Root] "Output"\n  - [AnimGraphNode_LegIK] "x"\n  - [AnimGraphNode_StateMachine] "y"'
    a = core.profile('Pack', _facts(), {'/Game/Pack/ABP': graph})
    b = core.profile('Pack', _facts(), {'/Game/Pack/ABP': graph})
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a['status'] == 'ok' and a['stock']['features']['leg_ik'] and not a['stock']['features']['control_rig']
    ov = {'primary.mesh': {'value': '/Game/Pack/Skins/A/Meshes/Hero_A', 'by': 'tuning', 'why': 'test'}}
    c = core.profile('Pack', _facts(), {'/Game/Pack/ABP': graph}, ov)
    assert c['primary']['mesh'] == '/Game/Pack/Skins/A/Meshes/Hero_A' and c['override_count'] == 1
    assert c['primary']['rule'] == 'override'          # applied BEFORE derivation, not pasted over a derived profile
    with pytest.raises(ValueError):
        core.profile('Pack', _facts(), {}, {'primary.mesh': {'value': 'x', 'by': '', 'why': ''}})


def test_failed_stock_audit_rejects_instead_of_reading_as_no_features():
    doc = core.profile('Pack', _facts(), {'/Game/Pack/ABP': 'ERROR: could not load /Game/Pack/ABP'})
    assert doc['status'] == 'rejected' and doc['stock']['audit_failed'] == ['/Game/Pack/ABP']


def test_second_body_is_reported():
    f = _facts()
    for m in f['skeletal_meshes']:
        m['hierarchy'] = _skel(_biped() + [('rider_pelvis', 'spine_02'), ('rider_spine', 'rider_pelvis')])
    doc = core.profile('Pack', f, {'/Game/Pack/ABP': '[AnimGraphNode_Root]'})
    assert doc['other_bodies'] == ['rider_pelvis'] and doc['roles']['pelvis'] == 'pelvis'


KHAIMERA_FACTS = os.path.join(REPO, 'Saved', 'MotionTerminalTests', 'intake', 'ParagonKhaimera_facts.json')
SPIKE_ROOTS = {'loin_cloth_bk_01', 'loin_cloth_fr_01', 'torso_cloth_bk_l_01', 'torso_cloth_bk_r_01',
               'torso_cloth_fr_l_01', 'torso_cloth_fr_r_01'}   # the Dynamics spike's hand-picked roots


@pytest.mark.skipif(not os.path.exists(KHAIMERA_FACTS),
                    reason='machine-local: run Scripts/hero_intake.py on the licensed Khaimera pack first')
def test_khaimera_ground_truth_matches_the_spike_rig():
    with open(KHAIMERA_FACTS) as f:
        facts = json.load(f)
    doc = core.profile('ParagonKhaimera', facts, {})
    assert doc['roles_unresolved'] == []
    # the spike's six, plus the axe tails the held-prop rule admits
    assert {c['root'] for c in doc['chains']} == SPIKE_ROOTS | {'weapon_tail_l_01', 'weapon_tail_r_01'}


def test_no_rig_name_in_generator_code():
    """Hero-agnostic rule: per-rig values live in HeroProfiles/<rig>.json, never in generator code."""
    names = set()
    for p in glob.glob(os.path.join(REPO, 'HeroProfiles', '*.json')):
        rig = os.path.splitext(os.path.basename(p))[0]
        names.add(rig.lower())
        names.add(re.sub(r'(?i)^paragon', '', rig).lower())
    names.discard('')
    assert names, 'no HeroProfiles yet'
    for src in glob.glob(os.path.join(HERE, 'hero_intake*.py')):
        if os.path.basename(src).startswith('test_'):
            continue
        text = open(src).read().lower()
        found = sorted(n for n in names if re.search(r'\b%s\b' % re.escape(n), text))
        assert not found, '%s names rigs: %s' % (os.path.basename(src), found)


DYNAMICS_CODE = ('dynamics_rig.py', 'dynamics_graph.py', 'pie_dynamics_drive.py', 'dyn_run.py',
                 'dyn_analyze.py', 'dyn_gate.py', 'dyn_render.py', 'dyn_overlay.py',
                 'dyn_hero.py', 'dyn_behaviours.py', 'dyn_sweep.py', 'dyn_compare.py', 'collider_autofit.py',
                 'hero_onboard.py', 'hero_onboard_core.py', 'hero_onboard_editor.py', 'hero_kinds.py',
                 'hero_intake_fit.py',
                 'hit_reaction_core.py', 'hit_reaction_run.py', 'hit_reaction_analyze.py', 'hit_reaction_sweep.py')


def _executable_text(path):
    """A source's names and non-docstring string literals: comments and docstrings (the tuning history, which records
    where a value was measured) are dropped; asset paths and identifiers are kept."""
    import io
    import tokenize
    out, prev = [], None
    for tok in tokenize.generate_tokens(io.StringIO(open(path).read()).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev in (None, tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                                                   tokenize.NL):
            continue                                     # a docstring / bare string statement
        if tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            out.append(tok.string)
        prev = tok.type if tok.type != tokenize.NL else prev
    return ' '.join(out)


def test_no_hero_name_in_dynamics_code():
    """The hero-agnostic rule for the dynamics pipeline
    (docs/specs/2026-10-28-dynamics-hero-agnostic-pipeline-design.md): no hero is named in executable code, embedded
    names included (an asset path like /Game/MT/<Hero>/CR_MT_<Hero>Dynamics would match). Hero-specific values live in
    HeroProfiles/<rig>.json and HeroProfiles/dynamics/<rig>.json, resolved by dyn_hero."""
    heroes = set()
    for p in glob.glob(os.path.join(REPO, 'HeroProfiles', '*.json')):
        rig = os.path.splitext(os.path.basename(p))[0]
        heroes |= {rig, re.sub(r'^Paragon(?=[A-Z])', '', rig)}
    assert heroes
    for name in DYNAMICS_CODE:
        code = _executable_text(os.path.join(HERE, name))
        # Case-insensitive substring: KHAIMERA_MESH and CR_MT_khaimeraDynamics count too.
        low = code.lower()
        found = sorted(h for h in heroes if h.lower() in low)
        assert not found, '%s names heroes in code: %s' % (name, found)


def test_the_dynamics_check_sees_an_embedded_name(tmp_path):
    src = tmp_path / 'x.py'
    src.write_text('"""Docstring about TestHero is fine."""\n# so is a comment on TestHero\n'
                   "CR = '/Game/MT/TestHero/CR_MT_TestHeroDynamics'\n")
    code = _executable_text(str(src))
    assert 'TestHero' in code and 'Docstring' not in code and 'comment' not in code
    src.write_text("TESTHERO_MESH = '/Game/X'\nmesh = '/Game/X/CR_MT_testheroDynamics'\n")
    assert _executable_text(str(src)).lower().count('testhero') == 2
