"""Hero intake, the decisions: pure Python over the facts hero_intake_gather.py collects in the editor. No editor import,
so every rule here is unit-tested (Scripts/test_hero_intake_core.py).

The rules: everything derived, never hand-tuned, and no rig name in generator code; every hand override
counted in the profile; and the chain and role classifier.

  primary(facts)            which skeletal mesh is the hero: the mesh on the most-used skeleton, shallowest path
  classify_roles(skel)      pelvis / spine / neck / head / limbs, by name tokens validated by the bone hierarchy
  discover_chains(...)      secondary-motion chains: rooted subgraphs of weighted, non-body bones
  profile(facts, ...)       the whole HeroProfiles/<rig>.json document, deterministic (no timestamps), so a
                            second run is a byte-identical no-op
"""
import re

import collider_autofit

SCHEMA = 1
MIN_CHAIN_VERTS = 20        # a chain bone must move at least this many LOD0 vertices (weight >= the gather's cut)
BAKED_DEG = 15.0            # a chain whose stock animation rotates it more than this already has baked motion
HEIGHT_OK_CM = (80.0, 450.0)

# ---------------------------------------------------------------- names

_CAMEL = re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+')


def tokens(name):
    """'mixamorig:LeftUpLeg' -> ['left', 'up', 'leg']; 'loin_cloth_bk_01' -> ['loin', 'cloth', 'bk', '01']."""
    name = name.split(':')[-1]
    out = []
    for part in re.split(r'[^A-Za-z0-9]+', name):
        out.extend(t.lower() for t in _CAMEL.findall(part))
    return out


QUAD_SIDES = {'fl', 'fr', 'bl', 'br'}       # front/back-left/right: a quadruped's limbs, never a biped role


def side(toks):
    """'l', 'r', None (no side), or '?' (both sides, or a quadruped side): '?' blocks every role."""
    t = set(toks)
    if t & QUAD_SIDES:
        return '?'
    left, right = bool(t & {'l', 'left'}), bool(t & {'r', 'right'})
    return '?' if left and right else 'l' if left else 'r' if right else None


# Role patterns: a bone takes the FIRST role whose any token-sequence occurs in its tokens. Order matters where one
# pattern contains another ('forearm' before 'arm', 'upleg' before 'leg'). Accessory words keep an 'arm_guard_l'
# under the upper arm from proposing itself as one.
_EXCLUDE_ROLE = {'twist', 'ik', 'end', 'nub', 'top', 'tip', 'roll', 'corrective', 'helper', 'socket', 'attach', 'vb',
                 'guard', 'armor', 'armour', 'pad', 'plate', 'bracer', 'gauntlet', 'sleeve', 'holster'}
_FINGERS = {'thumb', 'index', 'middle', 'ring', 'pinky', 'finger', 'fingers'}
ROLE_PATTERNS = [
    ('lowerarm', [['lowerarm'], ['lower', 'arm'], ['forearm'], ['fore', 'arm']]),
    ('upperarm', [['upperarm'], ['upper', 'arm'], ['arm']]),
    ('clavicle', [['clavicle'], ['shoulder'], ['collar']]),
    ('hand', [['hand'], ['wrist']]),
    ('thigh', [['thigh'], ['upleg'], ['up', 'leg'], ['upper', 'leg']]),
    ('calf', [['calf'], ['shin'], ['lower', 'leg'], ['leg']]),
    ('foot', [['foot'], ['ankle']]),
    ('ball', [['ball'], ['toebase'], ['toe', 'base']]),
    ('pelvis', [['pelvis'], ['hips'], ['hip']]),
    ('spine', [['spine'], ['chest']]),
    ('neck', [['neck']]),
    ('head', [['head']]),
]
SIDED = {'lowerarm', 'upperarm', 'clavicle', 'hand', 'thigh', 'calf', 'foot', 'ball'}
REQUIRED = (['pelvis', 'head'] + [r + '_' + s for s in 'lr' for r in ('thigh', 'calf', 'foot', 'upperarm',
                                                                         'lowerarm', 'hand')])


def _has_seq(toks, seq):
    n = len(seq)
    return any(toks[i:i + n] == seq for i in range(len(toks) - n + 1))


def role_of(name):
    """(role, side) a bone's name proposes, or (None, None)."""
    toks = tokens(name)
    if _EXCLUDE_ROLE & set(toks) or _FINGERS & set(toks) or preset_of(name):
        return None, None                    # a 'chest_cloth' or 'hair_head' bone is secondary, never body
    s = side(toks)
    if s == '?':
        return None, None
    for role, seqs in ROLE_PATTERNS:
        if any(_has_seq(toks, seq) for seq in seqs):
            if (role in SIDED) != (s is not None):
                continue                     # a sided role needs a side; an unsided one must not have one
            return role, s
    return None, None


# ---------------------------------------------------------------- hierarchy

def ancestors(parents, b):
    out, p = [], parents.get(b)
    while p:
        out.append(p)
        p = parents.get(p)
    return out


def descendants(parents, bones, b):
    return sorted(collider_autofit._descendants(parents, bones, b), key=bones.index)


def classify_roles(skel):
    """skel = {'bones': [names in hierarchy order], 'parents': {bone: parent or None}}.
    Returns (roles, unresolved). roles maps 'pelvis', 'spine' (list, root to chest), 'neck', 'head',
    '<limb>_<l|r>' to bone names. Each limb bone must descend from its parent role within MAX_HOPS."""
    bones, parents = skel['bones'], skel['parents']
    depth = {b: len(ancestors(parents, b)) for b in bones}
    cand = {}
    for b in bones:
        role, s = role_of(b)
        if role:
            cand.setdefault(role + ('_' + s if s else ''), []).append(b)
    by_depth = {k: sorted(v, key=lambda b: (depth[b], bones.index(b))) for k, v in cand.items()}
    roles = {}

    def pick(key, under=None, max_hops=3):
        for b in by_depth.get(key, []):
            anc = ancestors(parents, b)
            if under is None or (under in anc and anc.index(under) < max_hops):
                return b
        return None

    roles['pelvis'] = pick('pelvis')
    pelvis = roles['pelvis']
    # The spine is TOPOLOGY, not names: the bones strictly between the pelvis and the neck (or, with no neck, the
    # clavicles' common ancestor), so a 'chest_cloth' or 'fin_spine' bone hanging off the torso cannot become its top.
    neck = pick('neck', pelvis, 12) if pelvis else pick('neck')
    if neck:
        path = ancestors(parents, neck)                       # neck's parent first, up to the root
    else:
        cl = [by_depth.get('clavicle_' + s, [None])[0] for s in 'lr']
        common = [a for a in ancestors(parents, cl[0]) if a in ancestors(parents, cl[1])] if all(cl) else []
        path = [common[0]] + ancestors(parents, common[0]) if common else []
    roles['spine'] = list(reversed(path[:path.index(pelvis)])) if pelvis in path else []
    top = roles['spine'][-1] if roles['spine'] else pelvis
    roles['neck'] = neck
    roles['head'] = pick('head', neck or top, 3) if (neck or top) else pick('head')
    for s in 'lr':
        clav = pick('clavicle_' + s, top, 3) if top else None
        roles['clavicle_' + s] = clav
        up = pick('upperarm_' + s, clav or top, 2 if clav else 4)
        roles['upperarm_' + s] = up
        lo = pick('lowerarm_' + s, up, 3) if up else None
        roles['lowerarm_' + s] = lo
        roles['hand_' + s] = pick('hand_' + s, lo, 3) if lo else None
        th = pick('thigh_' + s, pelvis, 3) if pelvis else None
        roles['thigh_' + s] = th
        ca = pick('calf_' + s, th, 3) if th else None
        roles['calf_' + s] = ca
        ft = pick('foot_' + s, ca, 3) if ca else None
        roles['foot_' + s] = ft
        roles['ball_' + s] = pick('ball_' + s, ft, 2) if ft else None
    unresolved = [k for k in REQUIRED if not roles.get(k)] + ([] if roles['spine'] else ['spine'])
    return roles, unresolved


def role_bones(roles):
    out = set()
    for v in roles.values():
        out.update(v if isinstance(v, list) else [v] if v else [])
    return out


# ---------------------------------------------------------------- chains

# Bones that are never secondary motion, by token. Face and fingers are excluded by token AND by position below.
_DENY = {'twist', 'ik', 'root', 'corrective', 'helper', 'attach', 'socket', 'prop', 'weapon', 'gun', 'sword',
         'shield', 'camera', 'cam', 'vb', 'jaw', 'eye', 'eyes', 'eyelid', 'lid', 'brow', 'lip', 'lips', 'cheek',
         'tongue', 'teeth', 'nose', 'chin', 'mouth', 'muzzle', 'roll'} | _FINGERS
# Held-prop tokens: a bone with only these deny tokens, hanging from a prop under a hand, is admitted (axe tails).
# Body parts a held chain must never pull into the simulation through its subtree. Markers (socket, attach, helper,
# ik, ...) are not anatomy: a tail ending in an unweighted `tail_socket` is still a tail.
_ANATOMY = _FINGERS | {'jaw', 'eye', 'eyes', 'eyelid', 'lid', 'brow', 'lip', 'lips', 'cheek', 'tongue', 'teeth', 'nose',
                       'chin', 'mouth'}
_PROPS = {'weapon', 'prop', 'gun', 'sword', 'shield', 'axe', 'blade', 'bow', 'staff', 'spear', 'hammer', 'mace',
          'club', 'dagger', 'knife', 'scythe', 'lance', 'polearm', 'rifle', 'pistol'}
# Name tokens pick the preset. A chain with no preset token
# is reported as unclassified, never simulated by default: a mechanical part skinned like cloth must not flop.
PRESETS = [
    ('hair', {'hair', 'ponytail', 'braid', 'pigtail', 'bang', 'bangs', 'dread', 'dreads', 'mane'}),
    ('tail', {'tail'}),
    ('cloth', {'cloth', 'cape', 'skirt', 'loin', 'robe', 'coat', 'tabard', 'banner', 'flag', 'scarf', 'sash',
               'apron', 'cloak', 'hood', 'sleeve', 'fur', 'feather', 'feathers', 'kilt', 'drape', 'tassel', 'cord'}),
    ('strap', {'strap', 'belt', 'rope', 'ribbon', 'chain', 'necklace', 'lanyard', 'pouch', 'bag'}),
    # Soft tissue: a jiggle bone skinned into the body surface (every simulatable part is
    # simulated). Its behaviour deforms the host by design, so the painted-on rule does not drop it.
    ('soft', {'breast', 'breasts', 'boob', 'boobs', 'belly', 'stomach', 'glute', 'glutes', 'butt', 'buttock', 'jiggle',
              'flab'}),
]


def preset_of(name):
    toks = set(tokens(name))
    for preset, words in PRESETS:
        if toks & words:
            return preset
    return None


def discover_chains(skel, roles, weighted, motion=None):
    """weighted = {bone: LOD0 vertex count} on the primary mesh; motion = {bone: max stock rotation from ref, deg}.
    A candidate is a bone that is not a body role nor an ancestor of one (a chain never carries the body), not denied
    by token, not under a hand or foot (fingers, toes,
    held props), not above the pelvis, not under the head unless its preset is hair, and moves >= MIN_CHAIN_VERTS
    vertices. A chain is a candidate whose parent is not one, with ALL its descendants (the engine spawns the rooted
    subgraph, RigDynamicsExecutionHelpers.cpp:169-182), and needs at least two bones to swing.
    Returns (chains with a preset, chains without one, single-bone flaps)."""
    bones, parents = skel['bones'], skel['parents']
    body = role_bones(roles)
    under = {k: set(descendants(parents, bones, roles[k])) for k in roles
             if not isinstance(roles[k], list) and roles[k] and k.split('_')[0] in ('hand', 'foot', 'head')}
    pelvis = roles.get('pelvis')
    above_pelvis = set(ancestors(parents, pelvis)) if pelvis else set()

    holds_body = {a for b in body for a in ancestors(parents, b)}   # e.g. an extra neck bone above the head

    hand_bones = {roles[k] for k in under if k.startswith('hand')}

    def held(b):
        """Hangs from a prop held in a hand (an axe's tail): b has a preset, its only deny tokens are prop tokens, a
        prop bone sits STRICTLY between it and its nearest hand (a bone cannot certify itself by its own name), and
        nothing below it is a finger or face part (a chain spawns its whole subtree)."""
        if not preset_of(b) or not set(tokens(b)) & _DENY <= _PROPS:
            return False
        path = []
        for a in ancestors(parents, b):
            if a in hand_bones:
                break
            path.append(a)
        else:
            return False                                    # not under a hand
        if not any(set(tokens(a)) & _PROPS for a in path):
            return False
        return not any(set(tokens(d)) & _ANATOMY for d in descendants(parents, bones, b))

    def candidate(b):
        if b in body or b in above_pelvis or b in holds_body:
            return False
        if set(tokens(b)) & _DENY and not held(b):
            return False
        for k, sub in under.items():
            # Hair and feathers grow from the head; everything else under it (jaw, eyes, a hood shell) is the face.
            if b in sub and not (k == 'head' and (preset_of(b) == 'hair' or 'feather' in tokens(b))) \
                    and not (k.startswith('hand') and held(b)):
                return False
        return weighted.get(b, 0) >= MIN_CHAIN_VERTS

    cands = [b for b in bones if candidate(b)]
    cset = set(cands)
    chains, unclassified, single = [], [], []
    for root in cands:
        if any(a in cset for a in ancestors(parents, root)):
            continue      # inside another chain, even across an unweighted spacer bone: one chain, never two
        #                   overlapping ones
        sub = [root] + descendants(parents, bones, root)
        if len(sub) < 2:
            # The root is a particle (RigDynamicsExecutionHelpers.cpp:169-182) but KINEMATIC: it has no parent
            # particle (RigDynamicsSolver.cpp:301-306). Readback orients a bone from its child particles (the readback
            # loop from RigDynamicsSolver.cpp:826), so a childless flap gets no simulated rotation. Reported, so the dynamics build can add a
            # tip particle or route it elsewhere.
            single.append({'bone': root, 'preset': preset_of(root), 'verts': weighted.get(root, 0)})
            continue
        preset = preset_of(root)    # the root's own name only: a ponytail deep inside an embedded second body must
        #                             not make that whole body 'hair' (feathers under a 'loin_cloth' root still ride in)
        entry = {'root': root, 'bones': sub, 'preset': preset,
                 'verts': sum(weighted.get(b, 0) for b in sub),
                 'mixed': sorted(b for b in sub if b not in cset and set(tokens(b)) & _DENY),
                 'baked_deg': round(max((motion or {}).get(b, 0.0) for b in sub), 1) if motion else None}
        entry['baked'] = entry['baked_deg'] is not None and entry['baked_deg'] > BAKED_DEG
        (chains if preset else unclassified).append(entry)
    return chains, unclassified, single


def held_props(skel, roles, weighted):
    """Props the hero holds: a bone named by a prop token (weapon, sword, axe...) that moves geometry
    (>= MIN_CHAIN_VERTS vertices) and hangs under a hand role. A retarget from unarmed clips opens that hand around the
    prop, so the dynamics ABP layers a grip pose over it (dyn_hero: a derived grip). [{bone, hand, verts}]."""
    parents = skel['parents']
    hands = {roles.get('hand_l'), roles.get('hand_r')} - {None}
    out = []
    for b in skel['bones']:
        if not set(tokens(b)) & _PROPS or weighted.get(b, 0) < MIN_CHAIN_VERTS:
            continue
        hand = next((a for a in ancestors(parents, b) if a in hands), None)
        if hand:
            out.append({'bone': b, 'hand': hand, 'verts': weighted[b]})
    return out


# ---------------------------------------------------------------- secondary-motion parts

DEFAULT_BACKEND = 'cr_dynamics'     # the only backend built so far (docs/specs/2026-10-27-secondary-motion-backends-design.md §3.3)


def spine_tip(parents, bones):
    """The end of the unbranched run from the root: the tip the harness tracks (loin_cloth_bk_03, not one of the two
    feathers that branch off it)."""
    tip = bones[0]
    while True:
        kids = [b for b in bones if parents.get(b) == tip]
        if len(kids) != 1:
            break
        tip = kids[0]
    if tip != bones[0] or len(bones) == 1:
        return tip
    # The chain branches AT its root (a hair clump: two strands from one root, 2026-10-29). The root is a kinematic
    # particle, so a tip there reads 0.0 cm whatever the chain does: track the leaf of the deepest branch instead.
    def depth(b):
        kids = [k for k in bones if parents.get(k) == b]
        return 1 + max((depth(k) for k in kids), default=0)
    while True:
        kids = [b for b in bones if parents.get(b) == tip]
        if not kids:
            return tip
        tip = max(kids, key=lambda k: (depth(k), -bones.index(k)))


def parts(chains, parents, flaps=()):
    """docs/specs/2026-10-27-secondary-motion-backends-design.md §3.1: one part per simulated chain, and one per single-bone flap with a preset (it swings on a tip
    particle the fit measures from its own mesh; a flap that gets none is dropped by the collider build, with a reason).
    Backend-neutral identity only: the backend's own settings stay native, in its builder. Topology says whether a
    backend that takes only linear chains can have it."""
    out = [{'id': f['bone'], 'kind': 'flap', 'root': f['bone'], 'bones': [f['bone']], 'tip': f['bone'],
            'parent': parents.get(f['bone']), 'topology': 'single', 'preset': f['preset'], 'backend': DEFAULT_BACKEND}
           for f in flaps if f['preset']]
    for c in chains:
        bones = c['bones']
        branching = any(sum(1 for b in bones if parents.get(b) == x) > 1 for x in bones)
        out.append({'id': c['root'], 'kind': 'chain', 'root': c['root'], 'bones': bones,
                    'tip': spine_tip(parents, bones), 'parent': parents.get(c['root']),
                    'topology': 'branching' if branching else 'linear', 'preset': c['preset'],
                    'backend': DEFAULT_BACKEND})
    return sorted(out, key=lambda p: p['id'])


# ---------------------------------------------------------------- primary mesh

def primary(facts):
    """The hero's mesh: on the skeleton with the most AnimBlueprints targeting it (then the most sequences), the mesh
    with the shallowest package path (base mesh before skins), then the shortest name, then alphabetical."""
    meshes = facts['skeletal_meshes']
    if not meshes:
        return None, 'no skeletal mesh under the root'
    score = {}
    for m in meshes:
        sk = m['skeleton']
        score[sk] = (sum(1 for a in facts['anim_blueprints'] if a['target_skeleton'] == sk),
                     facts['sequences_by_skeleton'].get(sk, 0))
    skel = max(score, key=lambda s: (score[s], s))
    best = min((m for m in meshes if m['skeleton'] == skel),
               key=lambda m: (m['path'].count('/'), len(m['path'].rsplit('/', 1)[-1]), m['path']))
    return best, ('skeleton targeted by %d AnimBP(s), %d sequence(s); shallowest of %d mesh(es) on it'
                  % (score[skel][0], score[skel][1], sum(1 for m in meshes if m['skeleton'] == skel)))


# ---------------------------------------------------------------- profile

FEATURE_NODES = {   # AnimGraph node classes (ReadAnimGraph) that mean a stock rig already has the feature
    'leg_ik': ('AnimGraphNode_LegIK',), 'control_rig': ('AnimGraphNode_ControlRig',),
    'anim_dynamics': ('AnimGraphNode_AnimDynamics',), 'rigid_body': ('AnimGraphNode_RigidBody',),
    'foot_placement': ('AnimGraphNode_FootPlacement',), 'look_at': ('AnimGraphNode_LookAt',),
    'spring_bone': ('AnimGraphNode_SpringBone',), 'two_bone_ik': ('AnimGraphNode_TwoBoneIK',),
    'aim_offset': ('AnimGraphNode_RotationOffsetBlendSpace', 'AnimGraphNode_AimOffsetLookAt'),
    'blend_space': ('AnimGraphNode_BlendSpacePlayer',), 'state_machine': ('AnimGraphNode_StateMachine',),
}


def node_counts(read_anim_graph_text):
    """Count '[AnimGraphNode_X]' occurrences in MotionTerminal.AnimGraphTools.ReadAnimGraph output."""
    counts = {}
    for cls in re.findall(r'\[(\w+)\]', read_anim_graph_text or ''):
        counts[cls] = counts.get(cls, 0) + 1
    return dict(sorted(counts.items()))


def _check_overrides(overrides):
    for key, ov in overrides.items():
        if not (isinstance(ov, dict) and 'value' in ov and ov.get('by') and ov.get('why')):
            raise ValueError("override %r needs 'value', a non-empty 'by' and a non-empty 'why'" % key)


def _apply_overrides(doc, overrides):
    """Counted overrides: overrides = {'dotted.key': {'value': v, 'by': who, 'why': reason}}; applied after derivation, kept in the
    document and counted, so a hand decision is never silent. 'primary.mesh' is the exception: profile() applies it
    BEFORE deriving, since everything else is derived from that mesh."""
    for key, ov in sorted(overrides.items()):
        node, parts = doc, key.split('.')
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = ov['value']
    doc['overrides'] = overrides
    doc['override_count'] = len(overrides)
    return doc


def profile(rig, facts, graphs, overrides=None):
    """facts: hero_intake_gather output; graphs: {animbp path: ReadAnimGraph text}. Returns the profile dict."""
    overrides = overrides or {}
    _check_overrides(overrides)
    mesh, rule = primary(facts)
    if 'primary.mesh' in overrides:
        want = overrides['primary.mesh']['value']
        mesh = next((m for m in facts['skeletal_meshes'] if m['path'] == want), None)
        rule = 'override' if mesh else 'override names a mesh not under the root: %s' % want
    doc = {'schema': SCHEMA, 'rig': rig, 'root': facts['root'], 'status': 'ok', 'reasons': []}
    if mesh is None:
        doc.update(status='rejected', reasons=[rule])
        return _apply_overrides(doc, overrides)
    skel = mesh['hierarchy']
    roles, unresolved = classify_roles(skel)
    # A second role-named pelvis outside the chosen body (an embedded rider or ghost form): the body chosen is the
    # shallowest one, and the others are only reported, so a two-character rig is never silently treated as one.
    others = [b for b in skel['bones'] if role_of(b) == ('pelvis', None) and b != roles.get('pelvis')]
    motion = facts.get('motion', {}).get(mesh['skeleton'], {})
    chains, unclassified, single = discover_chains(skel, roles, mesh['weighted_bones'], motion.get('max_deg'))
    height = round(mesh['bounds_cm'][2], 1)
    abps = sorted(a['path'] for a in facts['anim_blueprints'] if a['target_skeleton'] == mesh['skeleton'])
    nodes, audit_failed = {}, []
    for a in abps:
        text = graphs.get(a)
        if not text or text.lstrip().startswith('ERROR'):   # ReadAnimGraph reports a load failure as text
            audit_failed.append(a)
            continue
        for cls, n in node_counts(text).items():
            nodes[cls] = nodes.get(cls, 0) + n
    simulated = {b for c in chains for b in c['bones']}
    held = [p for p in held_props(skel, roles, mesh['weighted_bones']) if p['bone'] not in simulated]
    doc.update({
        'held_props': held,
        'primary': {'mesh': mesh['path'], 'skeleton': mesh['skeleton'], 'rule': rule,
                    'meshes_under_root': len(facts['skeletal_meshes'])},
        'scale': {'height_cm': height, 'ok': HEIGHT_OK_CM[0] <= height <= HEIGHT_OK_CM[1]},
        'skeleton': {'bones': len(skel['bones']), 'weighted_bones': len(mesh['weighted_bones'])},
        'stock': {
            'anim_blueprints': abps,
            'animbp_nodes': dict(sorted(nodes.items())),
            'features': {f: any(nodes.get(c, 0) for c in cls) for f, cls in sorted(FEATURE_NODES.items())},
            'post_process_abp': mesh['post_process_abp'], 'physics_asset': mesh['physics_asset'],
            'physics_constraints': mesh.get('physics_constraints', 0),
            'clothing_assets': mesh['clothing_assets'], 'sequences': facts['sequences_by_skeleton'].get(mesh['skeleton'], 0),
            'motion_sampled_from': motion.get('sequences', []),
            'audit_failed': audit_failed,
        },
        'roles': roles, 'roles_unresolved': unresolved, 'other_bodies': others,
        'chains': chains, 'chains_unclassified': unclassified, 'single_bone_flaps': single,
    })
    if unresolved:
        doc['status'] = 'rejected'
        doc['reasons'].append('biped roles unresolved: ' + ', '.join(unresolved))
    if audit_failed:   # absent features would be indistinguishable from an unread AnimBP
        doc['status'] = 'rejected'
        doc['reasons'].append('stock AnimBP audit failed: ' + ', '.join(audit_failed))
    if not doc['scale']['ok']:
        doc['reasons'].append('height %.1f cm outside %s' % (height, HEIGHT_OK_CM))
    if others:
        doc['reasons'].append('other bodies in the rig (reported, not classified): ' + ', '.join(others))
    doc = _apply_overrides(doc, overrides)
    # Parts come from the chains AFTER overrides, so a chain override governs what is simulated (derived
    # before them, an override emptying `chains` would still leave a simulated part). An override of
    # `secondary` or `secondary.parts` itself wins; any other `secondary.*` override (a per-part behaviour pick,
    # `secondary.behaviours.<id>`) is kept beside the derived parts (it emptied the parts).
    if not any(k in ('secondary', 'secondary.parts') or k.startswith('secondary.parts.') for k in overrides):
        doc.setdefault('secondary', {})['parts'] = parts(doc['chains'], skel['parents'], doc['single_bone_flaps'])
    return doc
