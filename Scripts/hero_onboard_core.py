"""Hero onboarding, the decisions: pure Python (no editor), unit-tested in test_hero_onboard_core.py.

Spec: docs/specs/2026-10-29-hero-onboarding-design.md. Everything per hero comes from its profile roles and
skeleton; no rig name appears here.

  retarget_chains(roles, parents)   the nine retarget chains of the target IK rig, from body roles
  source_clips(cdo, bs_samples)     the clips the template plays, derived from its CDO and blendspace (never listed)
  travel(points)                    a clip's net horizontal pelvis travel: (distance, unit direction)
  pin_travel(points, d)             the pelvis track with its travel along d removed, sway / bob kept

Every generated asset is declared here as a spec, built in the editor, read back by a SEPARATE describe, and accepted
only when the read-back matches (the build's own reply is never the evidence):

  ik_rig_spec / ik_rig_mismatch               the target IK rig (pelvis, root motion, mesh, chains, validation)
  source_rig_problems / source_rig_notes      the template's IK rig: what refuses it, what is only reported
  retargeter_spec / retargeter_mismatch       the retargeter (op stack, per-op chain maps, FK chains and their
                                              settings, op pelvis, Root Motion op bones, validation per op type)
  blend_space_spec / blend_space_mismatch     the locomotion blend space (path, axes, samples, triangulation)
  abp_spec / abp_mismatch                     a locomotion ABP (parent, skeleton, compile status, CDO clips)
  physics_mismatch                            a physics asset against its spec (bodies, shapes, pairs, collisions)
  bake_destinations / batch_problems          the bake's outputs, known before it runs, and every batch entry
  bake_inventory / bake_leftovers             the bake folder before and after, and what a failed bake deletes
  additive_problems                           template clips the bake refuses, read in preflight
  missing_files                               verify's assets whose package file is not on disk
  source_root_motion                          the Root Motion op's source root, as the op picks it
  vary_pairs                                  the bone pairs the bake's motion check compares (one per chain)
  inplace_outputs                             the in-place stage's outputs, known before it runs
  skeleton_parents / translation_modes        DescribeSkeleton's reply, read
"""
import math
import re

# The mannequin IK rig's chain names (/Game/Spike/IK_Manny, read 2026-10-29): exact auto-map pairs a target chain with
# the source chain of the same name, so the target rig must use these names.
CHAINS = ('Spine', 'Neck', 'Head', 'LeftClavicle', 'RightClavicle', 'LeftArm', 'RightArm', 'LeftLeg', 'RightLeg')
TEMPLATE_CLIP_VARS = ('Anim_Idle', 'Anim_Jump', 'Anim_Fall', 'Anim_Land')
BS_VAR = 'BS_Locomotion'
# A clip whose pelvis ends this far (horizontally) from where it started is LOCOMOTION and is pinned in place. The
# locomotion clips travel 578-1363 cm per loop; the one-shots (idle, jump, fall, land) END within a few cm of their
# start: their spans (up to 18 cm forward on a jump's crouch and launch) are authored pose, not travel (measured on the
# first hero, khai_inplace_author.py header). 30 cm sits between.
TRAVEL_MIN_CM = 30.0


def retarget_chains(roles, parents):
    """[(chain, start bone, end bone)] for the target IK rig. roles: the profile's body roles; parents: {bone: parent}.
    Neck runs from the neck role to the head's parent (Paragon rigs have neck_01..neck_02 above the head); a rig with
    no neck gets no Neck chain (exact auto-map then leaves the source's Neck unmapped, reported by the caller).
    Raises when a required role is missing: a chain silently left out bakes that limb static."""
    need = ['pelvis', 'head'] + [r + '_' + s for s in 'lr' for r in ('clavicle', 'upperarm', 'hand', 'thigh', 'foot')]
    missing = [r for r in need if not roles.get(r)] + ([] if roles.get('spine') else ['spine'])
    if missing:
        raise ValueError('retargeting roles unresolved: %s' % ', '.join(missing))
    out = [('Spine', roles['spine'][0], roles['spine'][-1])]
    out += _limbs(roles, parents)
    for name, start, end in out:
        b = end
        while b and b != start:
            b = parents.get(b)
        if parents and b != start:
            raise ValueError('chain %s: %s does not descend from %s on this skeleton' % (name, end, start))
    return out


def _limbs(roles, parents):
    out = []
    if roles.get('neck'):
        end = parents.get(roles['head'])
        b = end
        while b and b != roles['neck']:          # the head's parent must descend from the neck role
            b = parents.get(b)
        if b != roles['neck']:
            raise ValueError('head %s does not hang from neck %s' % (roles['head'], roles['neck']))
        out.append(('Neck', roles['neck'], end))
    out.append(('Head', roles['head'], roles['head']))
    for side, name in (('l', 'Left'), ('r', 'Right')):
        out.append((name + 'Clavicle', roles['clavicle_' + side], roles['clavicle_' + side]))
        out.append((name + 'Arm', roles['upperarm_' + side], roles['hand_' + side]))
        out.append((name + 'Leg', roles['thigh_' + side], roles.get('ball_' + side) or roles['foot_' + side]))
    return out


def reachable_samples(bs_samples):
    """The template's blendspace samples its graph can reach. It binds only Y <- Speed and leaves Direction (X) at 0
    (Plugins/MotionTerminal/Tests/Baselines/abp_mt_locomotion.json:390), so
    only the X = 0 samples ever play: onboarding supports forward locomotion. Mirroring the
    strafe and backward samples would let a directional drive play forward gait while every asset check passes."""
    out = [s for s in bs_samples if abs(s['x']) < 1e-6]
    if not out:
        raise ValueError('the template blendspace has no sample on Direction = 0')
    return out


def source_clips(cdo, bs_samples):
    """The template's clips, in a stable order: its CDO clip variables, then every reachable blendspace sample's clip,
    each once. cdo: {var: clip path or None}; bs_samples: [{'animation': path, 'x', 'y'}]."""
    bs_samples = reachable_samples(bs_samples)
    seen, out = set(), []
    for path in [cdo.get(v) for v in TEMPLATE_CLIP_VARS] + [s['animation'] for s in bs_samples]:
        path = (path or '').split('.')[0]
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    if not any(cdo.get(v) for v in TEMPLATE_CLIP_VARS) or not bs_samples:
        raise ValueError('the template supplies no clips (CDO %s, %d blendspace samples)' % (cdo, len(bs_samples)))
    return out


def clip_name(path):
    return path.rstrip('/').rsplit('/', 1)[-1].split('.')[0]


def twin(retarget_dir, prefix, template_clip):
    """The hero's retarget of a template clip: <retarget_dir><prefix><template clip name> (the batch's naming rule:
    the prefix in front of the source asset's name, in the target folder)."""
    return retarget_dir + prefix + clip_name(template_clip)


def inplace_clip(inplace_dir, hero_clip):
    """The in-place duplicate of a hero clip: <inplace_dir><clip name>_InPlace."""
    return inplace_dir + clip_name(hero_clip) + '_InPlace'


def bake_destinations(clips, retarget_dir, prefix):
    """{template clip: the destination its bake must produce}: every output of the bake, computed BEFORE it runs, so
    the host can delete them first and again after any failure (the editor action can raise mid-way, after some clips
    were made)."""
    return {c: twin(retarget_dir, prefix, c) for c in clips}


def inplace_outputs(cdo, bs_samples, retarget_dir, prefix, inplace_dir):
    """{hero clip: its in-place duplicate} for every clip the hero's locomotion ABP plays: the template's clip
    variables and reachable blend-space samples (source_clips), each through its hero twin."""
    return {twin(retarget_dir, prefix, c): inplace_clip(inplace_dir, twin(retarget_dir, prefix, c))
            for c in source_clips(cdo, bs_samples)}


def travel(points):
    """(net horizontal distance from the first to the last key, unit direction or None). points: [[x, y, z], ...]."""
    dx, dy = points[-1][0] - points[0][0], points[-1][1] - points[0][1]
    dist = math.hypot(dx, dy)
    return dist, ([dx / dist, dy / dist, 0.0] if dist > 1e-6 else None)


def pin_travel(points, d):
    """Remove the motion along horizontal unit vector d, relative to the first key: p' = p - ((p - p0).d) d. Motion
    across d (sway), vertical bob and every other channel are untouched; the net travel becomes 0 by construction, so
    the loop joins seamlessly. On a clip travelling along +Y this pins Y to its first key exactly."""
    p0 = points[0]
    out = []
    for p in points:
        along = sum((p[k] - p0[k]) * d[k] for k in range(3))
        out.append([p[k] - along * d[k] for k in range(3)])
    return out


# A clip whose path bends away from its net direction by more than this is not straight-line locomotion: pinning along
# one direction would leave it wandering (a curve out and back closes its endpoints and still sweeps 100 cm),
# so it is refused. The forward gait's own sway across the line measured 6.2 cm (jog) and
# 4.9 cm (walk) on the first hero.
EXCURSION_MAX_CM = 20.0


def excursion(points):
    """The largest horizontal distance of any key from the first: the motion a pinned clip still makes in place."""
    return max(math.hypot(p[0] - points[0][0], p[1] - points[0][1]) for p in points)


def across(points, d):
    """The largest horizontal distance of any key from the straight line through the first key along d."""
    return max(abs((p[0] - points[0][0]) * -d[1] + (p[1] - points[0][1]) * d[0]) for p in points)


# ------------------------------------------------------------------------------------------------ skeleton

def package(path):
    """An object path's package (/Game/A/B.B -> /Game/A/B); '' for none ('None' is how the editor names no asset)."""
    path = '' if path in (None, 'None') else str(path)
    return path.split('.')[0]


def skeleton_parents(described):
    """{bone: parent or None} from MotionTerminal.AnimAssetTools.DescribeSkeleton's reply
    ({"ok", "path", "bones": [{"name", "index", "parent" ("" for the root), "translation_retargeting"}]}).
    Raises on a failed read, no bones, or a bone named twice (a dict would keep one silently)."""
    if not isinstance(described, dict) or not described.get('ok'):
        raise RuntimeError('DescribeSkeleton failed: %r' % ((described or {}).get('error') if isinstance(described, dict)
                                                            else described,))
    bones = described.get('bones') or []
    if not bones:
        raise RuntimeError('DescribeSkeleton of %s: no bones' % described.get('path'))
    out = {}
    for b in sorted(bones, key=lambda b: b['index']):
        if b['name'] in out:
            raise RuntimeError('DescribeSkeleton of %s: bone %s listed twice' % (described.get('path'), b['name']))
        out[b['name']] = b.get('parent') or None
    return out


def translation_modes(described, bones):
    """({bone: translation-retargeting mode} for the bones asked, [bones the skeleton does not have])."""
    have = {b['name']: b.get('translation_retargeting') for b in described.get('bones') or []}
    want = list(dict.fromkeys(bones))
    return {b: have[b] for b in want if b in have}, [b for b in want if b not in have]


# ------------------------------------------------------------------------------------------------ IK rig

def ik_rig_spec(path, mesh, pelvis, chains, root_motion=None):
    """The target IK rig: its asset path, preview mesh, retarget root (the PELVIS: SetRetargetRoot writes
    FRetargetDefinition::PelvisBone, not RootBone, which is the root-motion bone), root-motion bone and chains
    [(name, start, end)].

    root_motion: the skeleton's root (the bone with no parent). The Root Motion op takes its TargetRoot from the
    target rig's GetRoot() (RootMotionGeneratorOp.cpp OnAddedToStack) and falls back to the target mesh's bone 0 only
    when the rig names none; set explicitly, the op's root is a read-back fact (retargeter_mismatch compares the op's
    own bones too) instead of a fallback. None: the rig must name no root-motion bone."""
    return {'path': path, 'mesh': mesh, 'pelvis': pelvis, 'root_motion': root_motion,
            'chains': sorted([list(c) for c in chains])}


def ik_rig_mismatch(spec, described):
    """[difference] between an IK rig spec and its read-back ({'path', 'mesh', 'pelvis', 'root_motion', 'chains':
    [[name, start, end]], 'validation': {'valid', 'errors', 'warnings'}}). ValidateIKRig's warnings count too: a rig
    with no goals and no solvers warns only for a chain whose end does not descend from its start, or for chains with
    no retarget root (IKRigController.cpp ValidateIKRig, IKRigProcessor.cpp chain validation), both real faults here."""
    out = []
    if package(described.get('path')) != spec['path']:
        out.append('IK rig is %s, wanted %s (CreateNewIKRigAsset uniquifies a taken name)'
                   % (described.get('path'), spec['path']))
    if package(described.get('mesh')) != package(spec['mesh']):
        out.append('IK rig mesh %s, wanted %s' % (described.get('mesh'), spec['mesh']))
    if described.get('pelvis') != spec['pelvis']:
        out.append('IK rig retarget root (pelvis) %s, wanted %s' % (described.get('pelvis'), spec['pelvis']))
    if described.get('root_motion') != spec.get('root_motion'):
        out.append('IK rig root-motion bone %s, wanted %s' % (described.get('root_motion'), spec.get('root_motion')))
    got = sorted([list(c) for c in described.get('chains') or []])
    if got != spec['chains']:
        out.append('IK rig chains %s, wanted %s' % (got, spec['chains']))
    v = described.get('validation')
    if not isinstance(v, dict):
        out.append('IK rig: no validation report read back')
    else:
        if not v.get('valid'):
            out.append('IK rig does not validate: %s' % (v.get('errors') or ['(no error given)']))
        out += ['IK rig validation warning: %s' % w for w in v.get('warnings') or []]
    return out


# The template rig's validation warnings that are CHAIN problems, and so refuse it: a chain whose end bone does not
# descend from its start (IKRigProcessor.cpp "Invalid Retarget Chain") and chains with no retarget root
# (IKRigController.cpp ValidateIKRig). Every other warning ValidateIKRig gives is about goals and solvers (a goal no
# solver uses, an unknown solver, a bone's parent differing) that the retargeter never runs on the SOURCE side: the
# FK, Pelvis and Root Motion ops read the source rig only for its chains and bones (IKRetargeterController.cpp
# ValidateRetargeter: "the retargeter's own source and target IK Rigs are only read for their chains").
SOURCE_CHAIN_WARNINGS = ('invalid retarget chain', 'no retarget root')


def _chain_warning(w):
    return any(k in str(w).lower() for k in SOURCE_CHAIN_WARNINGS)


def source_rig_problems(described):
    """[problem] with the template's (SOURCE) IK rig as read back: it must validate (no error), no warning may be a
    chain problem (SOURCE_CHAIN_WARNINGS), and every chain must name both bones. The FK op counts as initialised when
    ONE chain pair resolves (FKChainsOp.cpp Initialize), so a broken source chain would otherwise pass every retargeter
    check while its limb bakes static. Other warnings are reported (source_rig_notes), not refused."""
    out = []
    v = described.get('validation')
    if not isinstance(v, dict) or not v.get('valid') or v.get('errors'):
        out.append('source IK rig %s does not validate: %s' % (described.get('path'), (v or {}).get('errors')))
    else:
        out += ['source IK rig validation warning: %s' % w for w in v.get('warnings') or [] if _chain_warning(w)]
    out += ['source IK rig chain %s has no start or end bone' % c[0] for c in described.get('chains') or []
            if not (c[1] and c[2])]
    return out


def source_rig_notes(described):
    """The template rig's warnings that do not refuse it (about goals and solvers the retargeter does not use)."""
    v = described.get('validation') if isinstance(described.get('validation'), dict) else {}
    return [str(w) for w in v.get('warnings') or [] if not _chain_warning(w)]


# ------------------------------------------------------------------------------------------------ retargeter

# The stack UIKRetargeterController::AddDefaultOps builds, in order (IKRetargeterController.cpp AddDefaultOps: Pelvis
# Motion, FK Chains, Run IK Rig, Root Motion, Curve Remap; the header's comment, which lists IK Chains, is wrong). Types
# as GetOpTypeByName returns them, the UStruct path.
PELVIS_OP = '/Script/IKRig.IKRetargetPelvisMotionOp'
FK_OP = '/Script/IKRig.IKRetargetFKChainsOp'
RUN_IK_OP = '/Script/IKRig.IKRetargetRunIKRigOp'
ROOT_MOTION_OP = '/Script/IKRig.IKRetargetRootMotionOp'
CURVE_OP = '/Script/IKRig.IKRetargetCurveRemapOp'
DEFAULT_OPS = (PELVIS_OP, FK_OP, RUN_IK_OP, ROOT_MOTION_OP, CURVE_OP)
# The ops of the default stack that keep a chain map (each overrides GetChainMapping: FKChainsOp.h, RunIKRigOp.h;
# GetOpsWithChainMaps lists exactly those, IKRetargeterController.cpp). Each must read one back, exact.
CHAIN_MAP_OPS = (FK_OP, RUN_IK_OP)
# Which op types must be able to run (ValidateRetargeter's OpsThatCannotRun: an op runs when it and its parents are
# enabled and Initialize set bIsInitialized, IKRetargeterController.cpp ~438-485), each with its reason:
REQUIRED_OPS = {
    # bIsInitialized = bPelvisEncoderInit && bPelvisDecoderInit (PelvisMotionOp.cpp ~115): without it the body never
    # leaves the retarget pose's height and travel.
    PELVIS_OP: 'carries the pelvis translation and rotation',
    # bIsInitialized = !ChainPairsFK.IsEmpty() (FKChainsOp.cpp ~911): ONE resolved pair passes, which is why the FK
    # chain list and each chain's settings are compared on their own below.
    FK_OP: 'copies every limb',
    # bIsInitialized only when the source root, target root and target pelvis all resolve (RootMotionGeneratorOp.cpp
    # 79-131): otherwise the root bone keeps the retarget pose, and the in-place stage pins whatever travels.
    ROOT_MOTION_OP: 'moves the root bone',
    # Initialize sets bIsInitialized = true unconditionally (CurveRemapOp.h 67-76): it can only be reported as unable
    # to run when it is disabled or broken, which is a fault.
    CURVE_OP: 'carries the curves (always initialises, so a report against it is a fault)',
    # bIsInitialized = IKRigProcessor.IsInitialized() (Plugins/Animation/IKRig/Source/IKRig/Private/Retargeter/
    # RetargetOps/RunIKRigOp.cpp:274), and the processor initialises a rig with zero solvers and zero goals: with none,
    # IsIKRigCompatibleWithSkeleton has no required bone to miss (Private/Rig/IKRigProcessor.cpp:213-235) and Initialize
    # runs through to bInitialized = true (IKRigProcessor.cpp:14-210). The op's settings come from the target rig
    # (OnAddedToStack, RunIKRigOp.cpp:358-371, which is why retargeter_build sets the rigs before adding the ops), and
    # its chain list skips chains with no IK goal (bSkipNonIKChains, RunIKRigOp.cpp:702), so the hero's goal-less rig
    # logs none of the op's own warnings (RunIKRigOp.cpp:191, 202, 240). It solves nothing, but it runs: a report that
    # it cannot, or any warning it logs, is a fault (no rig asset, a rig that does not fit the mesh), so it is refused
    # like the others.
    RUN_IK_OP: 'runs the target IK rig (no solvers: nothing to solve, but it initialises, so a report is a fault)',
}
# Each FK chain's settings (FRetargetFKChainSettings, FKChainsOp.h 176-226). The build never sets them, so they are
# the engine's defaults: what the retargeter has always baked with, now read back. A chain with EnableFK off keeps
# its name and outputs the retarget pose (FKChainsOp.cpp ~173-187), so a name match alone is not a working chain.
FK_CHAIN_SETTINGS = {'enable_fk': True, 'rotation_mode': 'INTERPOLATED', 'rotation_alpha': 1.0,
                     'translation_mode': 'NONE', 'translation_alpha': 1.0}


# The one retargeter warning a correct build gives: a chain of ONE bone (start == end) has zero length, so the engine
# cannot normalise it and says so (IKRetargetProcessor.cpp:666-672). The chain stays valid (IsValid, :598-605, does not
# read its length; the divisor is clamped, :921) and its bone is retargeted. Expected exactly for the chains a rig
# declares with one bone, so it is accepted for those chains by its exact text and for no other chain.
SINGLE_BONE_WARNING = 'IK Retargeter bone chain, %s, was unable to be normalized. Chain too short.'


def single_bone_chains(*rigs_chains):
    """The names of the chains declared with one bone ([(name, start, end)], start == end) on any of the given rigs."""
    return sorted({c[0] for chains in rigs_chains for c in chains if c[1] and c[1] == c[2]})


def retargeter_spec(path, source_rig, target_rig, pelvis, target_chains, source_chains, root=None, source_mesh=None,
                    source_root=None, single_bone=()):
    """The retargeter: the default op stack, all enabled; every op with a chain map pairs each target chain with the
    SAME-NAMED source chain (the exact map; None where the source has no such chain, which retargeter_mismatch then
    refuses); the FK op retargets every target chain with the default chain settings; the Pelvis Motion op targets the
    rig's pelvis; the Root Motion op's target root is the rig's root-motion bone (root), its target pelvis the pelvis
    and its source root `source_root` (source_root_motion: the bone the op itself defaults to). source_mesh: what
    ValidateRetargeter runs the source side on (the template rig's preview mesh). single_bone: single_bone_chains of
    both rigs, the chains whose SINGLE_BONE_WARNING the build is expected to give."""
    src = set(source_chains)
    return {'path': path, 'source_ik_rig': source_rig, 'target_ik_rig': target_rig, 'op_types': list(DEFAULT_OPS),
            'chain_map': {t: (t if t in src else None) for t in target_chains},
            'chain_map_ops': list(CHAIN_MAP_OPS),
            'fk_chains': sorted(target_chains), 'fk_chain_settings': {t: dict(FK_CHAIN_SETTINGS) for t in target_chains},
            'op_pelvis': pelvis,
            'root_motion': ({'source_root': source_root, 'target_root': root, 'target_pelvis': pelvis}
                            if root is not None else None),
            'source_mesh': source_mesh, 'single_bone_chains': sorted(single_bone)}


def source_root_motion(source_rig, source_skeleton=None):
    """(bone, where it comes from): the Root Motion op's source root, the way the op picks it when added to the stack
    (Plugins/Animation/IKRig/Source/IKRig/Private/Retargeter/RetargetOps/RootMotionGeneratorOp.cpp:197-220): the SOURCE
    IK rig's root-motion bone (GetRoot, FRetargetDefinition::RootBone), and only when that rig names none, bone 0 of
    the source preview mesh (the retargeter's source preview mesh, which falls back to the source rig's,
    IKRetargeter.cpp:71-93). source_rig: ik_rig_describe's reply; source_skeleton: DescribeSkeleton of that preview
    mesh's skeleton (bone 0 of a mesh and of its skeleton are the same root), needed only for the fallback. Raises
    when neither names a bone: the op would then not initialise."""
    if source_rig.get('root_motion'):
        return source_rig['root_motion'], 'the source IK rig root-motion bone'
    bones = (source_skeleton.get('bones') or []) if isinstance(source_skeleton, dict) else []
    root =[b['name'] for b in bones if b.get('index') == 0]
    if not root:
        raise RuntimeError('the source IK rig %s names no root-motion bone and its preview mesh skeleton gave no bone 0'
                           % source_rig.get('path'))
    return root[0], 'bone 0 of the source preview mesh (the source IK rig names no root-motion bone)'


def _setting_same(want, got):
    if isinstance(want, bool) or isinstance(got, bool):
        return want is got
    if isinstance(want, float):
        try:
            return abs(float(got) - want) <= 1e-6
        except (TypeError, ValueError):
            return False
    return str(got).rsplit('.', 1)[-1].upper() == str(want).upper()


def retargeter_mismatch(spec, described):
    """[difference] between a retargeter spec and its read-back:
    {'path', 'source_ik_rig', 'target_ik_rig', 'ops': [{'index', 'name', 'type', 'enabled'}],
     'chain_maps': {op name: {target chain: source chain or None}}, 'fk_chains': {op name: [chain]},
     'fk_chain_settings': {op name: {chain: {enable_fk, rotation_mode, rotation_alpha, translation_mode,
     translation_alpha}}}, 'op_pelvis': {op name: bone},
     'op_root_motion': {op name: {'source_root', 'target_root', 'target_pelvis'}},
     'validation': {'ready', 'errors', 'warnings', 'cannot_run': [op name]}}.
    Each op's chain map is compared on its own: GetChainMap() with no op name reads only the first op that has one."""
    out = []
    if package(described.get('path')) != spec['path']:
        out.append('retargeter is %s, wanted %s' % (described.get('path'), spec['path']))
    for key in ('source_ik_rig', 'target_ik_rig'):
        if package(described.get(key)) != package(spec[key]):
            out.append('retargeter %s %s, wanted %s' % (key, described.get(key), spec[key]))
    ops = described.get('ops') or []
    types = [o.get('type') for o in ops]
    if types != spec['op_types']:
        out.append('retargeter ops %s, wanted %s' % (types, spec['op_types']))
    off = [o.get('name') for o in ops if not o.get('enabled')]
    if off:
        out.append('retargeter ops disabled: %s' % off)
    unmatched = sorted(t for t, s in spec['chain_map'].items() if s is None)
    if unmatched:
        out.append('target chains with no same-named source chain: %s' % unmatched)
    maps = described.get('chain_maps') or {}
    want = {t: s for t, s in spec['chain_map'].items()}
    for op, got in sorted(maps.items()):
        got = {t: (s if s not in ('', 'None') else None) for t, s in (got or {}).items()}
        if got != want:
            out.append('op %s chain map %s, wanted %s' % (op, got, want))
    # a map must be read back for EVERY op that keeps one, not only the FK op's (a missing map is not an exact one)
    for o in ops:
        if o.get('type') in spec.get('chain_map_ops', [FK_OP]) and o.get('name') not in maps:
            out.append('op %s (%s) has no chain map read back' % (o.get('name'), o.get('type')))
    fk = [o.get('name') for o in ops if o.get('type') == FK_OP]
    pel = [o.get('name') for o in ops if o.get('type') == PELVIS_OP]
    if len(fk) != 1:
        out.append('retargeter has %d FK Chains ops' % len(fk))
    for name in fk:
        chains = sorted((described.get('fk_chains') or {}).get(name) or [])
        if chains != spec['fk_chains']:
            out.append('FK op retargets %s, wanted %s (an empty list bakes static clips)' % (chains, spec['fk_chains']))
        settings = (described.get('fk_chain_settings') or {}).get(name) or {}
        for chain, want_s in sorted((spec.get('fk_chain_settings') or {}).items()):
            got_s = settings.get(chain)
            if not isinstance(got_s, dict):
                out.append('FK op %s: no settings read back for chain %s' % (name, chain))
                continue
            for k, w in sorted(want_s.items()):
                if not _setting_same(w, got_s.get(k)):
                    out.append('FK op %s chain %s %s %r, wanted %r%s' % (
                        name, chain, k, got_s.get(k), w,
                        ' (a disabled chain outputs the retarget pose)' if k == 'enable_fk' else ''))
    # the rig's retarget root and the Pelvis Motion op's target pelvis are two settings: this is the op's
    op_pelvis = [(described.get('op_pelvis') or {}).get(n) for n in pel]
    if op_pelvis != [spec['op_pelvis']]:
        out.append('Pelvis Motion op targets %s, wanted [%s]' % (op_pelvis, spec['op_pelvis']))
    want_rm = spec.get('root_motion')
    if want_rm:
        rm = [o.get('name') for o in ops if o.get('type') == ROOT_MOTION_OP]
        got_rm = [(described.get('op_root_motion') or {}).get(n) or {} for n in rm]
        if len(got_rm) != 1:
            out.append('retargeter has %d Root Motion ops read back' % len(got_rm))
        for g in got_rm:
            # the source root against the bone the op defaults to (source_root_motion), not merely any bone
            if not want_rm.get('source_root'):
                out.append('Root Motion op: the spec names no source root to compare %r with' % g.get('source_root'))
            elif g.get('source_root') != want_rm['source_root']:
                out.append('Root Motion op source_root %s, wanted %s' % (g.get('source_root'), want_rm['source_root']))
            for k in ('target_root', 'target_pelvis'):
                if g.get(k) != want_rm[k]:
                    out.append('Root Motion op %s %s, wanted %s' % (k, g.get(k), want_rm[k]))
    v = described.get('validation')
    if not isinstance(v, dict):
        out.append('retargeter: no validation report read back')
    else:
        if not v.get('ready'):
            out.append('retargeter is not ready to run: %s' % (v.get('errors') or ['(no error given)']))
        elif v.get('errors'):
            out.append('retargeter validation errors: %s' % v['errors'])
        # every warning refuses but the one a declared single-bone chain gives (SINGLE_BONE_WARNING), by exact text
        expected = {SINGLE_BONE_WARNING % c for c in spec.get('single_bone_chains') or ()}
        out += ['retargeter validation warning: %s' % w for w in v.get('warnings') or [] if w not in expected]
        # every op must run, each named with its reason (REQUIRED_OPS); an op the read cannot place (a name not in the
        # stack) is refused too
        types = {o.get('name'): o.get('type') for o in ops}
        dead = sorted(v.get('cannot_run') or [])
        if dead:
            out.append('retargeter ops that cannot run: %s (%s)' % (
                ['%s (%s)' % (n, REQUIRED_OPS.get(types.get(n), 'not in the stack read back')) for n in dead],
                v.get('errors')))
    return out


# ------------------------------------------------------------------------------------------------ blend space

AXIS_KEYS = ('name', 'min', 'max', 'grid_divisions', 'snap_to_grid', 'wrap_input')
VALUE_TOL = 1e-4
# A sample's position is compared to an ABSOLUTE 1e-3 (in the axis' own units: degrees of Direction, cm/s of Speed),
# not rounded: rounding to three decimals let 0.0004 pass as 0 by accident of where the rounding boundary fell, and
# a sample nudged off Direction = 0 by more than this is a different gait. The builder writes the template's value
# as a double into an FVector (AnimAssetTools.cpp Fill), so an exact build lands well inside it.
POSITION_TOL = 1e-3
# Template keys the describe tool reports once it is extended (per-sample rate_scale, top-level
# interpolate_using_grid): carried into the spec and compared only when the TEMPLATE's description has them.
SAMPLE_OPTIONAL = ('rate_scale',)
TOP_OPTIONAL = ('interpolate_using_grid',)


def blend_space_spec(skeleton, template, twin, path=None):
    """The hero's locomotion blend space mirroring the template's (DescribeBlendSpace's reply): its path, the same
    axes, and each REACHABLE sample with its clip replaced by twin(template clip), plus the optional keys the template's
    description carries (SAMPLE_OPTIONAL, TOP_OPTIONAL). The BuildBlendSpace SpecJson (unknown keys are ignored by the
    builder, so the path rides along)."""
    samples = reachable_samples(template['samples'])
    out = {'path': path, 'skeleton': skeleton,
           'axis_x': {k: template['axis_x'][k] for k in AXIS_KEYS},
           'axis_y': {k: template['axis_y'][k] for k in AXIS_KEYS},
           'samples': [dict({'animation': twin(s['animation']), 'x': s['x'], 'y': s['y']},
                            **{k: s[k] for k in SAMPLE_OPTIONAL if k in s}) for s in samples]}
    out.update({k: template[k] for k in TOP_OPTIONAL if k in template})
    return out


def _close(a, b, tol=VALUE_TOL):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _sample_gaps(want, have):
    """Match each wanted sample to one read-back sample: same clip package, x and y within POSITION_TOL, and every
    optional key the wanted one carries equal (rate_scale to VALUE_TOL). Returns (unmatched wanted, unmatched read)."""
    left = list(have)
    missing = []
    for w in want:
        hit = None
        for g in left:
            try:
                near = (abs(float(g.get('x')) - float(w['x'])) <= POSITION_TOL
                        and abs(float(g.get('y')) - float(w['y'])) <= POSITION_TOL)
            except (TypeError, ValueError):
                near = False
            if (near and package(g.get('animation')) == package(w['animation'])
                    and all(_close(g.get(k), w[k]) for k in SAMPLE_OPTIONAL if k in w)):
                hit = g
                break
        if hit is None:
            missing.append(w)
        else:
            left.remove(hit)
    return missing, left


def blend_space_mismatch(spec, described):
    """[difference] between a blend space spec and DescribeBlendSpace's reply: its path (when the spec names one),
    skeleton, every axis key, the samples (clip, x, y, and the optional keys) matched one to one, each sample valid,
    the optional top-level keys, and the triangulation baked unless the asset interpolates on its grid."""
    if not isinstance(described, dict) or not described.get('ok'):
        return ['blend space: %s' % ((described or {}).get('error') if isinstance(described, dict) else described)]
    out = []
    if spec.get('path') is not None and package(described.get('path')) != spec['path']:
        out.append('blend space is %s, wanted %s' % (described.get('path'), spec['path']))
    if package(described.get('skeleton')) != package(spec['skeleton']):
        out.append('blend space skeleton %s, wanted %s' % (described.get('skeleton'), spec['skeleton']))
    for axis in ('axis_x', 'axis_y'):
        got = described.get(axis) or {}
        for k in AXIS_KEYS:
            w, g = spec[axis][k], got.get(k)
            same = _close(g, w) if k in ('min', 'max') else (g == w and type(g) is type(w))
            if not same:
                out.append('blend space %s %s %r, wanted %r' % (axis, k, g, w))
    for k in TOP_OPTIONAL:
        if k in spec and (described.get(k) != spec[k] or type(described.get(k)) is not type(spec[k])):
            out.append('blend space %s %r, wanted %r' % (k, described.get(k), spec[k]))
    missing, extra = _sample_gaps(spec['samples'], described.get('samples') or [])
    if missing or extra:
        out.append('blend space samples: missing %s, unexpected %s' % (
            [(package(s['animation']), s['x'], s['y']) for s in missing],
            [(package(s.get('animation')), s.get('x'), s.get('y')) for s in extra]))
    bad = [package(s.get('animation')) for s in described.get('samples') or [] if not s.get('valid')]
    if bad:
        out.append('blend space samples not valid: %s' % bad)
    # Segments or triangles are required only of a blend space that does not interpolate on its grid: one that does
    # keeps none by design (the builder accepts that, AnimAssetTools.cpp BuildBlendSpace ~179-189). Asked of the
    # DESCRIBED asset; whether it should interpolate on its grid is the TOP_OPTIONAL comparison above.
    if described.get('interpolate_using_grid') is not True and described.get('triangulation_baked') is not True:
        out.append('blend space triangulation not baked (%r) and it does not interpolate on its grid (%r)'
                   % (described.get('triangulation_baked'), described.get('interpolate_using_grid')))
    return out


# ------------------------------------------------------------------------------------------------ locomotion ABP

def abp_spec(path, cls, parent_class, skeleton, cdo):
    """A locomotion ABP: a child of the template class `parent_class` on `skeleton`, whose generated class `cls` holds
    the clip variables `cdo` {var: asset path}. The same spec describes the in-place ABP (a duplicate)."""
    return {'path': path, 'class': cls, 'parent_class': parent_class, 'skeleton': skeleton, 'cdo': dict(cdo)}


def blueprint_status(s):
    """EBlueprintStatus by name, normalised: 'BS_UpToDate', 'BS_UP_TO_DATE' and 'BlueprintStatus.BS_UP_TO_DATE' all
    give 'UPTODATE'."""
    s = re.sub(r'[^A-Z]', '', str(s).rsplit('.', 1)[-1].upper())
    return s[2:] if s.startswith('BS') else s


# The compiled statuses: the engine's own UBlueprint::IsUpToDate accepts exactly these two
# (Engine/Source/Runtime/Engine/Classes/Engine/Blueprint.h:778-781). Error, dirty, unknown and being-created refuse.
COMPILED_STATUSES = ('UPTODATE', 'UPTODATEWITHWARNINGS')


def abp_compiled_with_warnings(described):
    """Whether an ABP read-back is compiled with warnings: accepted, and stated in the stage record."""
    return blueprint_status(described.get('status')) == 'UPTODATEWITHWARNINGS'


def abp_mismatch(spec, described):
    """[difference] between an ABP spec and its read-back ({'path', 'parent', 'skeleton', 'compiled', 'status',
    'cdo': {var: path}}). The status must be one the engine treats as compiled (COMPILED_STATUSES: up to date, with or
    without warnings; the warnings are reported by abp_compiled_with_warnings, not refused); error, dirty, unknown and
    being-created are not accepted. The read compiles nothing (hero_onboard_editor.abp_describe): the build compiles.
    The compiler's messages themselves are not readable from editor Python; the status is what stands for them."""
    out = []
    if package(described.get('path')) != spec['path']:
        out.append('ABP is %s, wanted %s' % (described.get('path'), spec['path']))
    if described.get('parent') != spec['parent_class']:
        out.append('ABP parent %s, wanted %s' % (described.get('parent'), spec['parent_class']))
    if package(described.get('skeleton')) != package(spec['skeleton']):
        out.append('ABP skeleton %s, wanted %s' % (described.get('skeleton'), spec['skeleton']))
    if described.get('compiled') is not True:
        out.append('ABP did not compile (%r)' % described.get('compiled'))
    if blueprint_status(described.get('status')) not in COMPILED_STATUSES:
        out.append('ABP compile status %s, wanted BS_UpToDate or BS_UpToDateWithWarnings' % described.get('status'))
    got = described.get('cdo') or {}
    for var, want in sorted(spec['cdo'].items()):
        if package(got.get(var)) != package(want):
            out.append('ABP %s is %s, wanted %s' % (var, got.get(var), want))
    return out


# ------------------------------------------------------------------------------------------------ physics asset

def physics_mismatch(spec, built):
    """[difference, ...] between a physics spec and an asset's read-back: bodies, their types and SHAPES, constraint
    pairs and angular freedom, the enabled collisions. An asset rebuilt or edited at the same path must not pass on its
    name alone, nor on its body names: a body resized or re-aimed, or a pair enabled or
    disabled since, is a different asset."""
    import hit_reaction_core
    if not built.get('ok'):
        return ['physics asset: %s' % built.get('error')]
    out = []
    want = {b['bone']: b['physics_type'] for b in spec['bodies']}
    got = {b['bone']: b.get('physics_type') for b in built['bodies']}
    if got != want:
        out.append('physics bodies %s, wanted %s' % (got, want))
    shapes = {b['bone']: b.get('shapes') for b in built['bodies']}
    for b in spec['bodies']:
        if b['bone'] in shapes:
            out += hit_reaction_core.shapes_mismatch(b['bone'], b['elements'], shapes[b['bone']])
    wpairs = sorted((c['child'], c['parent'], c.get('angular') == 'free') for c in spec['constraints'])
    gpairs = sorted((c['child'], c['parent'], bool(c.get('angular_free'))) for c in built['constraints'])
    if gpairs != wpairs:
        out.append('physics constraints %s, wanted %s' % (gpairs, wpairs))
    if abs(built.get('fixed_step_s', spec['fixed_step_s']) - spec['fixed_step_s']) > 1e-6:
        out.append('physics fixed step %s, wanted %s' % (built.get('fixed_step_s'), spec['fixed_step_s']))
    # collision exactly where the spec says (its 'collide' list is exact: idle and reference pose both checked, and the
    # builder fails on a pair that does not clear), a missing pair as much as an extra one
    if 'collisions' not in built:
        out.append('physics collisions: the read-back has none (a builder from before the collision describe)')
    else:
        wset = {frozenset((c['a'], c['b'])) for c in spec.get('collide') or []}
        gset = {frozenset(p) for p in built['collisions']}
        if gset != wset:
            out.append('physics collision enabled between %s, wanted exactly %s (extra %s, missing %s)' % (
                sorted(sorted(p) for p in gset), sorted(sorted(p) for p in wset),
                sorted(sorted(p) for p in gset - wset), sorted(sorted(p) for p in wset - gset)))
    if spec.get('position_iterations') and built.get('position_iterations') != spec['position_iterations']:
        out.append('physics position iterations %s, wanted %s' % (built.get('position_iterations'),
                                                                  spec['position_iterations']))
    return out


# ------------------------------------------------------------------------------------------------ bake

def _status(s):
    return str(s).rsplit('.', 1)[-1].upper()


def batch_problems(wanted, rows):
    """[problem] in UIKRetargetBatchOperation.RunBatchRetargetWithResults' entries, which can be Skipped or Failed
    without the call raising. wanted: {source clip: the destination it must produce}; rows: [{'source', 'retargeted',
    'status', 'reason', 'requested'}] (package paths, the status by name). Every entry must be Retargeted, every
    requested clip must have exactly one, at its destination, and ANY entry that is not a requested clip is a problem:
    the bake runs with referenced assets off, so an extra entry is an output nothing expects, saves or cleans up
    (batch_unexpected names its asset for the cleanup)."""
    out = []
    for r in rows:
        if _status(r.get('status')) != 'RETARGETED':
            out.append('%s: %s (%s)' % (r.get('source'), _status(r.get('status')), r.get('reason') or 'no reason given'))
    requested = {}
    wanted_src = {package(s) for s in wanted}
    for r in rows:
        if r.get('requested') and package(r.get('source')) in wanted_src:
            requested.setdefault(package(r.get('source')), []).append(r)
        else:
            out.append('%s: a result for a clip that was not requested (made %s)' % (
                r.get('source'), package(r.get('retargeted')) or 'nothing'))
    for src, dst in wanted.items():
        got = requested.pop(package(src), [])
        if len(got) != 1:
            out.append('%s: %d batch results, wanted 1' % (src, len(got)))
        elif _status(got[0].get('status')) == 'RETARGETED' and package(got[0].get('retargeted')) != package(dst):
            out.append('%s: retargeted to %s, wanted %s' % (src, got[0].get('retargeted'), dst))
    return out


def batch_unexpected(wanted, rows):
    """The assets a batch made that are not a wanted destination (sorted packages): deleted with the rest when the
    bake fails, so nothing the host did not ask for survives."""
    dests = {package(d) for d in wanted.values()}
    return sorted({package(r.get('retargeted')) for r in rows} - dests - {''})


def bake_leftovers(expected, before, after, made=()):
    """What a failed bake must delete (sorted packages): every expected destination, every asset the batch reported
    that was not asked for (made), and every asset present in the folder AFTER the failure that was not there BEFORE
    the bake (before / after: inventories, registry and disk together, bake_inventory). The inventory covers what the
    batch's reply cannot: a call that died, or raised while its results were being decoded. The caller still applies
    the "inside the hero's folder and git-ignored" rule to everything that is not an expected destination."""
    return sorted(({package(p) for p in expected} | {package(p) for p in made}
                   | ({package(p) for p in after} - {package(p) for p in before})) - {''})


def bake_inventory(registry, disk):
    """One inventory of a folder (sorted packages): the asset registry's listing (unsaved in-memory assets included)
    and the .uasset files on disk, together: an asset only in memory or only on disk is still there."""
    return sorted(({package(p) for p in registry} | {package(p) for p in disk}) - {''})


def additive_problems(kinds):
    """[problem] for template clips that are not plain sequences, from {clip: additive anim type by name} (or None
    for a clip that does not load). The bake runs with referenced assets off, so an additive clip's ref-pose clip
    would stay the source's: refused in preflight, before anything is deleted."""
    out = []
    for clip, kind in sorted(kinds.items()):
        name = str(kind).rsplit('.', 1)[-1].upper() if kind is not None else 'MISSING'
        if name not in ('NONE', 'AAT_NONE'):
            out.append('%s (%s)' % (clip, kind))
    return ['template clips that are not plain sequences (the bake runs with referenced assets off): %s' % out] \
        if out else []


def missing_files(paths, exists):
    """[gap] for each asset whose package file is not on disk (exists(path) -> bool, the host's
    asset_pipeline.content_file check): an asset the editor reads only in memory vanishes on the next start."""
    return ['not on disk: %s' % p for p in dict.fromkeys(paths) if p and not exists(p)]


def vary_pairs(source_chains, target_chains):
    """[(source bone, target bone)] the bake's motion check compares: the END bone of every retarget chain present on
    both rigs (by chain name; each skeleton's own bone names), plus each leg's START (the thigh), which the leg's end
    bone (ball or foot) alone would not cover. source_chains / target_chains: [(name, start, end)].

    A lower bound against the source's own swing, per pair (hero_onboard_editor._swing), applied only where the
    source swings more than VARY_MIN_DEG (a bone the source leaves at its reference pose, tracked or not, is not
    compared). What it cannot see: a limb that swings at the wrong time or in the wrong direction, a target that
    swings MORE than its source, any bone between a chain's start and end (the thighs aside), and any limb whose
    source swings less than VARY_MIN_DEG."""
    src = {c[0]: (c[1], c[2]) for c in source_chains}
    out = []
    for name, start, end in sorted(target_chains):
        if name in src:
            out.append((src[name][1], end))
    tgt = {c[0]: (c[1], c[2]) for c in target_chains}
    for leg in ('LeftLeg', 'RightLeg'):
        if leg in src and leg in tgt:
            out.append((src[leg][0], tgt[leg][0]))
    return list(dict.fromkeys(out))
