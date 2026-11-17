"""Build ABP_MT_KhaimeraDynamics, the post-process AnimBP that runs CR_MT_KhaimeraDynamics.

    Input Pose -+-> Layered Blend Per Bone (hand subtrees from GRIP_POSE) -> Control Rig (CR_MT_KhaimeraDynamics,
    GRIP_POSE --+                                                            DynamicsAlpha pin LINKED) -> Output

The grip: the locomotion is retargeted from the mannequin's UNARMED clips, so every bone under a hand (fingers and
the weapon_l/weapon_r bones the axes are skinned to) carries an open hand around nothing. Khaimera's own clips hold
the grip: index_01_r is curled -80 deg in Idle and Jog_Fwd frame 0, -23 deg in every retargeted clip, and weapon_r
sits ~3 cm / 20 deg off. The layer takes each hand's subtree (local space) from his own Jog_Fwd frame 0, so fingers close on
the haft where the weapon bone actually puts it; arms, hands, curves and everything else stay the retargeted animation.
The grip is unconditional (no equip/action gate) and Khaimera-specific (GRIP_POSE); a hero whose native clips open
the hand or drop the weapon needs that gate before this layer.

Run from a normal shell with the editor up. It rebuilds BOTH assets in dependency order: deletes this ABP,
re-authors the rig through dynamics_rig.py, then builds this ABP against it:

    python Scripts/dynamics_graph.py --hero <Rig>                  # the shipped rig
    python Scripts/dynamics_graph.py --hero <Rig> --no-colliders   # the proven-red variant, colliders removed

How: the whole graph is one declarative spec (dynamics_spec, pure, host-tested), built in one call by
MotionTerminal.AnimGraphBuildTools.BuildAnimBlueprint and accepted only when an independent read
(AnimGraphTools.DescribeAnimGraph) matches it (animgraph_spec.build, design in
docs/specs/2026-11-12-animgraph-build-describe-design.md). Every other editor step goes through editor_ops (Epic's MCP
server). Like the rig, this deletes and rebuilds the asset, so the result is reproducible rather than accreted. The
asset binds Khaimera's skeleton, so it is git-ignored.

Decisions docs/specs/2026-10-06-khaimera-cr-dynamics-design.md pins and this file enforces:
- the alpha pin is LINKED to a variable get, never property-bound (a binding on an unexposed pin is silently
  ignored at runtime); the rig node exposes DynamicsAlpha as a pin and the spec links it;
- curves leave the rig because the node's OutputSettings.bUpdateCurves is true, and nothing here can change that: the
  member is transient on the node (AnimNode_ControlRigBase.h:126-127) and FControlRigIOSettings defaults it to true
  (Tools/ControlRigIOSettings.h:13-16, AnimNode_ControlRigBase.cpp:42). The earlier build wrote it; a transient member
  is not saved, so that write never reached the asset and is not repeated;
- it is applied per component with SetOverridePostProcessAnimBP by the harness, never on the mesh asset.
Prints one `KHAIDYN_ABP <json>` line: the description of what was built.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import animgraph_spec as ag  # noqa: E402
import dyn_hero  # noqa: E402
import asset_pipeline as ap  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ALPHA_VAR = 'DynamicsAlpha'   # same name as the rig variable (dynamics_rig.ALPHA_VAR)
# The grip (HeroProfiles/dynamics/<rig>.json 'grip', optional): a pose that is not additive (Paragon's Idle is a
# LocalSpaceBase additive, and an additive evaluated alone collapses the hands) and the hand bones whose subtrees it
# supplies. A hero with no grip gets Input Pose -> Control Rig.
# BlendDepth is a weight RAMP, not a depth limit: weight = (depth below root + 1) / BlendDepth, every descendant
# included (AnimationRuntime.cpp:2444-2459). 0 means full weight on the whole subtree; 8 gave the finger roots 12.5%.
GRIP_DEPTH = 0


def grip_roots(bones, hands):
    """The direct children of each hand that are not twist helpers (twist bones parent to the lower arm on Paragon
    rigs, but a hand-parented one would be driven by the forearm roll and must keep the retargeted value)."""
    names = {b['index']: b['name'] for b in bones}
    found = {i for i, n in names.items() if n in hands}
    if len(found) != len(hands):
        raise RuntimeError('KHAIDYN_ABP hand bones %s not all on the skeleton' % (hands,))
    return sorted(b['name'] for b in bones if b.get('parent_index') in found and 'twist' not in b['name'])


REPO = os.path.dirname(HERE)
# Everything a build is made from; the gate's identity row compares these against disk (dyn_gate.source_digest).
SOURCES = ('dynamics_rig.py', 'dynamics_graph.py', 'collider_autofit.py', 'dyn_behaviours.py',
           'dynamics_behaviours.json', 'dyn_hero.py', 'hit_reaction_core.py', 'leg_reach.py')


def write_manifest(h, with_colliders, behaviours=None, air=None, reaction=None):
    """What the rig and ABP now in the editor were built from (a record must say what produced it,
    not what is on disk when it finishes). The driver copies this into every record at the start of a drive."""
    import dyn_gate
    prof = h['profile']
    scripts = {}
    for name in SOURCES:
        with open(os.path.join(REPO, 'Scripts', name), 'rb') as f:
            scripts[name] = dyn_gate.digest_bytes(f.read())
    extras = os.path.join(dyn_hero.EXTRAS_DIR, h['rig'] + '.json')
    if os.path.exists(extras):
        with open(extras, 'rb') as f:
            scripts['extras:' + h['rig']] = dyn_gate.digest_bytes(f.read())
    doc = {'hero': h['rig'], 'mesh': h['mesh'], 'cr': h['cr_path'], 'abp': h['abp_path'],
           'with_colliders': with_colliders, 'behaviours': behaviours, 'air': air,
           'grip_pose': (h['grip'] or {}).get('pose'), 'scripts': scripts, 'reaction': reaction,
           'parts': dyn_gate.digest(prof['secondary']['parts']),
           'colliders': dyn_gate.digest(prof['colliders']),
           'backends': sorted({p['backend'] for p in prof['secondary']['parts']})}
    os.makedirs(os.path.dirname(h['manifest']), exist_ok=True)
    with open(h['manifest'], 'w') as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    return doc


def _ops():
    import editor_ops
    return editor_ops.shared()


# A derived grip's pose (dyn_hero._derived_grip): the hero's OWN clip in the stance the drive shows, first match wins,
# shortest name first (Jog_Fwd before Jog_Fwd_Combat). Paragon idles are additive, which the build refuses.
GRIP_POSE_TOKENS = (('jog', 'fwd'), ('run', 'fwd'), ('idle',))
NOT_ADDITIVE = ('NONE', 'AAT_NONE')     # AdditiveAnimationType.AAT_NONE as the editor's Python names it


def pick_grip_pose(sequences):
    """sequences: [(path, additive_type)] on the hero's skeleton -> the grip pose path, or None."""
    import hero_intake_core as core
    # Never a generated clip (/Game/MT/...: the retargeted unarmed set the grip exists to
    # repair): only the hero's own authored content.
    usable = sorted((p for p, add in sequences if str(add).upper() in NOT_ADDITIVE
                     and not p.startswith('/Game/MT/')),
                    key=lambda p: (len(p.rsplit('/', 1)[-1]), p))
    for want in GRIP_POSE_TOKENS:
        for p in usable:
            if set(want) <= set(core.tokens(p.rsplit('/', 1)[-1])):
                return p
    return None


# --- editor reads (each one editor_ops.py script whose final expression is the value) ---------------------------
# No comprehension or lambda in these scripts: the server may run a script with separate globals and locals, where a
# comprehension cannot see the script's own top-level names.

def _find_grip_pose(ops, skeleton):
    """[[package path, additive type name]] of every AnimSequence on `skeleton`."""
    got = ops.py("import json\n"
                 "ar = unreal.AssetRegistryHelpers.get_asset_registry()\n"
                 "out = []\n"
                 "for d in ar.get_assets_by_class(unreal.TopLevelAssetPath('/Script/Engine', 'AnimSequence')):\n"
                 "    if str(d.get_tag_value('Skeleton') or '').split(\"'\")[-2:-1] == [%r + '.' + %r]:\n"
                 "        a = d.get_asset()\n"
                 "        out.append([str(d.package_name), a.get_editor_property('additive_anim_type').name])\n"
                 "json.dumps(out)\n" % (skeleton, skeleton.rsplit('/', 1)[1]), timeout=600)
    if not isinstance(got, list):
        raise RuntimeError('KHAIDYN_ABP the sequence listing returned %r, not a list' % (got,))
    return got


def bones_from_parents(pairs):
    """[[bone, parent or None]] in the skeleton's order -> [{'index', 'name', 'parent_index'}] (grip_roots' input)."""
    at = {name: i for i, (name, _p) in enumerate(pairs)}
    return [{'index': i, 'name': name, 'parent_index': at.get(parent) if parent else None}
            for i, (name, parent) in enumerate(pairs)]


def _skeleton_bones(ops, h):
    """The skeleton's bones with their parents, in the skeleton's order, from one
    MotionTerminal.AnimAssetTools.DescribeSkeleton read (the same read hero_onboard makes; "" is the root's parent)."""
    got = ops.call('MotionTerminal.AnimAssetTools', 'DescribeSkeleton', SkeletonPath=h['skeleton'])
    if not got.get('ok') or not got.get('bones'):
        raise RuntimeError('KHAIDYN_ABP the bones of %s read back as %r' % (h['skeleton'], str(got)[:300]))
    pairs = [[b['name'], b.get('parent') or None] for b in sorted(got['bones'], key=lambda b: b['index'])]
    return bones_from_parents(pairs)


def _sequence_info(ops, path):
    """{'additive_type', 'skeleton'} of an animation sequence ('' / None when it does not load)."""
    got = ops.py("import json\n"
                 "a = unreal.load_asset(%r)\n"
                 "s = a.get_editor_property('skeleton') if a else None\n"
                 "json.dumps({'additive_type': a.get_editor_property('additive_anim_type').name if a else None, "
                 "'skeleton': s.get_path_name() if s else ''})\n" % path)
    if not isinstance(got, dict):
        raise RuntimeError('KHAIDYN_ABP the sequence %s read back as %r' % (path, got))
    return got


def _exists(ops, path):
    got = ops.py('import json\njson.dumps(bool(unreal.EditorAssetLibrary.does_asset_exist(%r)))\n' % path)
    if not isinstance(got, bool):
        raise RuntimeError('KHAIDYN_ABP does_asset_exist(%s) read back as %r' % (path, got))
    return got


REACTION_ALPHA_VAR = 'HitReactionAlpha'
INPUT_NODE = '/Script/AnimGraph.AnimGraphNode_LinkedInputPose'
SEQUENCE_NODE = '/Script/AnimGraph.AnimGraphNode_SequenceEvaluator'
LAYER_NODE = '/Script/AnimGraph.AnimGraphNode_LayeredBoneBlend'
CONTROL_RIG_NODE = '/Script/ControlRigDeveloper.AnimGraphNode_ControlRig'
RBWC_NODE = '/Script/PhysicsControlUncookedOnly.AnimGraphNode_RigidBodyWithControl'
COPY_BONE_NODE = '/Script/AnimGraph.AnimGraphNode_CopyBone'
LEG_IK_NODE = '/Script/AnimGraph.AnimGraphNode_LegIK'
L2C_NODE = '/Script/AnimGraph.AnimGraphNode_LocalToComponentSpace'
C2L_NODE = '/Script/AnimGraph.AnimGraphNode_ComponentToLocalSpace'
# Spec paths whose COMPILED value the describe tool cannot read: a constant the compiler folded out of the node struct,
# which the engine lets no outside reader at. Each is named, and only that reason is waived (animgraph_spec.mismatch).
# SequenceEvaluator's Sequence is a FoldProperty (AnimNode_SequenceEvaluator.h:125); no other path set here is folded.
# What stands in for the unread value: the editor node's Sequence is still compared, and a grip that did not compile
# in shows as open hands in the first drive.
FOLDED_OK = ('grip:Sequence',)


def pose_links(chain, l2cs, rig=None):
    """[(node, pin, next node, pin)] along a pose chain, the pins by the space each node works in: an L2C (in l2cs)
    turns a local Pose into a ComponentPose, the rig (a Control Rig node) takes a local Source and gives a local Pose,
    and every other node here is in component space (ComponentPose in, Pose out)."""
    out = lambda a: 'ComponentPose' if a in l2cs else 'Pose'                               # noqa: E731
    into = lambda b: 'LocalPose' if b in l2cs else 'Source' if b == rig else 'ComponentPose'   # noqa: E731
    return [(a, out(a), b, into(b)) for a, b in zip(chain, chain[1:])]


def object_path(path):
    """/Game/A/B -> /Game/A/B.B (an object path names the asset whatever the importer does with a package path)."""
    return path if '.' in path.rsplit('/', 1)[-1] else '%s.%s' % (path, path.rsplit('/', 1)[-1])


def rig_reference(rig_class):
    """FAnimNode_ControlRig::ControlRigAssetReference as export text for a blueprint rig: FControlRigAssetStrongReference
    holds the legacy BlueprintRigClass or a ControlRigAsset, and setting one clears the other
    (ControlRigAssetReference.h:26-31, 43)."""
    return '(BlueprintRigClass="%s",ControlRigAsset=None)' % rig_class


def layer_setup(roots, depth=GRIP_DEPTH):
    """LayeredBoneBlend LayerSetup for its one blend pose: TArray<FInputBlendPose> (AnimNode_LayeredBoneBlend.h:50-51),
    each with BranchFilters, each FBranchFilter a BoneName and a BlendDepth (BoneMaskFilter.h:16-36). The depth is
    always written, 0 included: it is the decision GRIP_DEPTH records, and the description reads every member back."""
    if not roots:
        raise ValueError('a grip layer needs at least one bone')
    filters = ','.join('(BoneName="%s",BlendDepth=%d)' % (b, depth) for b in roots)
    return '((BranchFilters=(%s)))' % filters


def reaction_values(h, reaction, over=None):
    """The RigidBodyWithControl's settings as engine export text (hit_reaction_core), for the onboarded reaction body
    spec `reaction` and the experiment overrides `over` (KDYN_REACTION_JSON)."""
    import hit_reaction_core as hrc
    over = over or {}
    return {'limb': hrc.limb_setup(h['profile'], reaction),
            'control': hrc.parent_space_control(over.get('strength'), over.get('damping')),
            'world_control': hrc.world_space_control(over.get('pelvis_linear'), over.get('pelvis_angular'),
                                                     over.get('pelvis_damping')),
            'initial': hrc.initial_updates(h['profile'], reaction, over.get('arm_multiplier'), over.get('arm_joints'),
                                           over.get('arm_damping'), over.get('arm_target_hz'),
                                           over.get('arm_target_zeta')),
            'modifier': hrc.body_modifier(over.get('gravity'))}


def dynamics_spec(h, grip_pose=None, roots=(), reaction=None, legs=(), over=None):
    """The ABP's whole AnimGraph as an animgraph_spec spec. Pure: no editor.

    grip_pose: the grip's sequence (None: no grip layer) and roots its blended bones (grip_roots). reaction: the
    onboarded reaction body spec (hit_reaction_core.onboarded_spec), None without one; legs its leg IK
    (hit_reaction_core.leg_ik, [] for none); over: KDYN_REACTION_JSON. Ids are stable: the same hero gives the same
    ids, so descriptions compare across builds.

        input -> [layer <- grip] -> [l2c -> copy_<i>... -> rb (Alpha <- reaction_alpha)
              -> [reach_c2l -> reach -> reach_l2c -> leg_ik] -> c2l] -> dynamics (DynamicsAlpha <- dynamics_alpha)

    Under the reaction the pose passes through Physics Control's RigidBodyWithControl BEFORE the Dynamics rig, so
    cloth and hair see a hit (docs/specs/2026-10-29-hit-reaction-design.md); at alpha 0 the node is a pose
    pass-through (AnimNode_SkeletalControlBase.cpp:164). Under leg IK the pose leaves component space for the reach
    rig (a Control Rig node takes and gives a local pose, AnimNode_ControlRigBase.cpp:176) and comes back for the leg
    IK. One CopyBone per leg BEFORE the reaction copies the incoming foot (translation and rotation, component space)
    into its IK target bone, so the target is whatever pose arrives; the LegIK AFTER it runs at full alpha (the
    reaction's alpha is already in the pose it corrects; at alpha 0 the foot is its own target, an identity)."""
    import hit_reaction_core as hrc
    nodes, links = [ag.node('input', INPUT_NODE)], []
    variables, gets = [(ALPHA_VAR, 'float', '0.0')], []
    source = 'input'
    if grip_pose:
        # A SequenceEvaluator, not a player: defaults teleport to ExplicitTime 0 every update, so the grip is a held
        # pose with no clock, no notifies and no root motion of its own (AnimNode_SequenceEvaluator.h:148/152).
        # Curves stay the main graph's (UseBasePose): the default Override would lay the grip clip's curves over them.
        # Not for a curve whose metadata links it to a grip bone: the node hands those to the branch before the option
        # applies (AnimNode_LayeredBoneBlend.cpp:287); the first hero's skeleton was not checked for such curves.
        nodes += [ag.node('grip', SEQUENCE_NODE, [('Sequence', object_path(grip_pose))]),
                  ag.node('layer', LAYER_NODE, [('LayerSetup', layer_setup(roots)),
                                                ('CurveBlendOption', 'UseBasePose')], poses=1)]
        links += [('input', 'Pose', 'layer', 'BasePose'), ('grip', 'Pose', 'layer', 'BlendPoses_0')]
        source = 'layer'
    if reaction is not None:
        v = reaction_values(h, reaction, over)
        variables.append((REACTION_ALPHA_VAR, 'float', '0.0'))
        gets.append(('reaction_alpha', REACTION_ALPHA_VAR))
        copies, definition = hrc.leg_ik_nodes(legs) if legs else ([], None)
        nodes.append(ag.node('l2c', L2C_NODE))
        chain, l2cs, rig = [source, 'l2c'], ['l2c'], None
        for i, (foot, target) in enumerate(copies):
            nodes.append(ag.node('copy_%d' % i, COPY_BONE_NODE, [
                ('SourceBone', '(BoneName="%s")' % foot), ('TargetBone', '(BoneName="%s")' % target),
                ('bCopyTranslation', 'True'), ('bCopyRotation', 'True'), ('ControlSpace', 'BCS_ComponentSpace')]))
            chain.append('copy_%d' % i)
        # Explicit, never defaults: controls OFF and zero strength by default. Alpha is linked, so never set here.
        nodes.append(ag.node('rb', RBWC_NODE, [
            ('OverridePhysicsAsset', object_path(h['physics_asset'])),
            ('bEnableControls', 'True'), ('bEnableWorldGeometry', 'False'),
            ('CharacterSetupData.LimbSetupData', v['limb']),
            ('CharacterSetupData.DefaultParentSpaceControlData', v['control']),
            ('CharacterSetupData.DefaultWorldSpaceControlData', v['world_control']),
            ('InitialControlAndBodyModifierUpdates', v['initial']),
            ('CharacterSetupData.DefaultBodyModifierData', v['modifier'])]))
        chain.append('rb')
        if legs:
            nodes += [ag.node('reach_c2l', C2L_NODE),
                      ag.node('reach', CONTROL_RIG_NODE, [('ControlRigAssetReference',
                                                           rig_reference(h['reach_cr_class']))]),
                      ag.node('reach_l2c', L2C_NODE),
                      ag.node('leg_ik', LEG_IK_NODE, [('LegsDefinition', definition)])]
            chain += ['reach_c2l', 'reach', 'reach_l2c', 'leg_ik']
            l2cs.append('reach_l2c')
            rig = 'reach'
        nodes.append(ag.node('c2l', C2L_NODE))
        chain.append('c2l')
        links += pose_links(chain, l2cs, rig)
        links.append(('reaction_alpha', REACTION_ALPHA_VAR, 'rb', 'Alpha'))
        source = 'c2l'
    nodes.append(ag.node('dynamics', CONTROL_RIG_NODE, [('ControlRigAssetReference', rig_reference(h['cr_class']))],
                         exposed=[ALPHA_VAR]))
    gets.append(('dynamics_alpha', ALPHA_VAR))
    links += [(source, 'Pose', 'dynamics', 'Source'), ('dynamics_alpha', ALPHA_VAR, 'dynamics', ALPHA_VAR)]
    return ag.spec(h['skeleton'], nodes, links, ('dynamics', 'Pose'), variables, gets)


def _save_checked(ops, path, who):
    ops.py("import importlib, sys\nsys.path.insert(0, r'%s')\n"
           "importlib.reload(importlib.import_module('mt_drive')).require_saved_gitignored(%r, %r)\n"
           % (HERE, path, who))


def _build_reach(ops, h, legs):
    """The leg-reach rig (leg_reach.py), rebuilt from the hero's skeleton and the spec's legs. The ABP that
    referenced the old rig was deleted first (build). Returns its node count."""
    ap.delete_asset(ops, h['reach_cr_path'])
    n = ops.py("import importlib, json, sys\nsys.path.insert(0, r'%s')\n"
               "n = importlib.reload(importlib.import_module('leg_reach')).author(unreal, %r, %r, %r, %r)\n"
               "importlib.reload(importlib.import_module('mt_drive')).require_saved_gitignored(%r, 'leg_reach')\n"
               "json.dumps(n)\n"
               % (HERE, h['reach_cr_path'], h['skeleton'], h['profile']['roles']['pelvis'], legs,
                  h['reach_cr_path']), timeout=300)
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise RuntimeError('KHAIDYN_ABP the leg-reach rig was not built: %r' % (n,))
    return n


def _reaction_record(h, reaction, legs, over):
    """What the manifest records of the reaction: its values and the asset's CONTENTS and the builder's version, not
    just its path (a reaction body rebuilt or edited at the same path must change the build identity)."""
    import hero_onboard
    import hit_reaction_core as hrc
    import dyn_gate
    described = hero_onboard.describe_physics(h['physics_asset'])
    v = reaction_values(h, reaction, over)
    return {'node': 'rb', 'out': 'c2l', 'physics_asset': h['physics_asset'], 'limb': v['limb'], 'leg_ik': legs,
            'reach_rig': h['reach_cr_path'] if legs else None,
            'control': v['control'], 'world_control': v['world_control'], 'modifier': v['modifier'],
            'initial': v['initial'], 'override': over or None,
            'physics_digest': dyn_gate.digest({k: described.get(k) for k in ('bodies', 'constraints',
                                                                                   'fixed_step_s',
                                                                                   'position_iterations',
                                                                                   'collisions')}),
            'builder': hrc.BUILDER_VERSION}


def build(h, with_colliders=True):
    ops = _ops()
    ABP_PATH, SKELETON = h['abp_path'], h['skeleton']
    # The ABP references the rig's generated class, so it goes first: then the rig can be deleted and rebuilt,
    # and the ABP is rebuilt against the new class (a stale class reference leaves a pawn with no anim instance
    # and no error; khai_inplace_author.py's header records that silent failure).
    # The delete waits out the asset registry's startup scan first: on a fresh editor, does_asset_exist answered False
    # for an ABP that was on disk, the delete was skipped and the create then failed with "already exists" (seen on
    # two fresh starts, 2026-10-26).
    ap.delete_asset(ops, ABP_PATH)
    # KDYN_BEHAVIOURS_JSON (environment, experiments only): merged into the behaviour library, e.g.
    # {"pendant": {"native": {"Strength": 0.5}}} or {"feather": {"flutter": {"FlutterRate": 4}}}
    behaviours = json.loads(os.environ['KDYN_BEHAVIOURS_JSON']) if os.environ.get('KDYN_BEHAVIOURS_JSON') else None
    # KDYN_AIR_JSON (environment, experiments only): air/motion-scale input defaults, e.g. {"MotionScale": 0.5}
    air = json.loads(os.environ['KDYN_AIR_JSON']) if os.environ.get('KDYN_AIR_JSON') else None
    ops.py('unreal.SystemLibrary.collect_garbage()')
    ops.run_file(os.path.join(HERE, 'dynamics_rig.py').replace('\\', '/'), timeout=900,   # ~7 min (2026-10-28)
                 KDYN_HERO=h['rig'], KDYN_ACTION='author', KDYN_COLLIDERS=with_colliders, KDYN_BEHAVIOURS=behaviours,
                 KDYN_AIR=air)

    roots = []
    if h['grip'] and not h['grip']['pose']:
        seqs = _find_grip_pose(ops, SKELETON)
        pose = pick_grip_pose([tuple(s) for s in seqs])
        if not pose:
            raise RuntimeError('KHAIDYN_ABP %s holds a prop but none of its %d clips is a non-additive %s pose: hand-set '
                               'a grip in HeroProfiles/dynamics/%s.json' % (h['rig'], len(seqs), GRIP_POSE_TOKENS,
                                                                           h['rig']))
        h = dict(h, grip=dict(h['grip'], pose=pose))
    grip_pose = (h['grip'] or {}).get('pose')
    if grip_pose:
        roots = grip_roots(_skeleton_bones(ops, h), tuple(h['grip']['hands']))
        info = _sequence_info(ops, grip_pose)
        if str(info.get('additive_type')).upper() not in NOT_ADDITIVE:
            raise RuntimeError('KHAIDYN_ABP grip pose %s is additive (%s): evaluated alone it is the additive identity '
                               '(zero translation and scale), which collapses the hands'
                               % (grip_pose, info.get('additive_type')))
        if (info.get('skeleton') or '').split('.')[0] != SKELETON:
            raise RuntimeError('KHAIDYN_ABP grip pose %s is on %s, not %s' % (grip_pose, info.get('skeleton'), SKELETON))

    # Hit reaction: when the hero has a reaction body (PA_MT_<S>, hero_onboard.py physics stage). The per-joint springs
    # name the asset's own controls, and the limbs and the leg IK follow its bodies: the onboarded spec must be the
    # asset that is built (a missing control name only logs a warning in the node, RigidBodyWithControl.cpp:859-863).
    reaction, legs, over = None, [], {}
    if _exists(ops, h['physics_asset']):
        import hero_onboard
        import hit_reaction_core as hrc
        reaction = hrc.onboarded_spec(h)
        stale = hero_onboard.physics_mismatch(reaction, hero_onboard.describe_physics(h['physics_asset']))
        if stale:
            raise RuntimeError('KHAIDYN_ABP the onboarded reaction spec is not the built asset: %s' % stale)
        legs = reaction.get('leg_ik') or []
        # KDYN_REACTION_JSON (environment, experiments only): {"strength": Hz, "damping": ratio, "gravity": multiplier,
        # "arm_multiplier": x, "arm_joints": {role class: x}, "arm_damping": {role class: x}, "arm_target_hz" /
        # "arm_target_zeta": {role class: Hz / ratio} (hit_reaction_core.ARM_TARGET_HZ), "pelvis_linear" /
        # "pelvis_angular": Hz, "pelvis_damping": ratio} for the recovery springs.
        over = json.loads(os.environ['KDYN_REACTION_JSON']) if os.environ.get('KDYN_REACTION_JSON') else {}
        if legs:
            _build_reach(ops, h, legs)

    spec = dynamics_spec(h, grip_pose, roots, reaction, legs, over)
    # One sequence for any generated asset (asset_pipeline.realize): refuse a tracked target, delete, build,
    # describe, compare, save only on a match; a failed or mismatched build is deleted before the error is raised.
    described = ap.realize(ops, ap.animgraph(FOLDED_OK), ABP_PATH, spec)['described']
    _save_checked(ops, ABP_PATH, 'dynamics_graph')
    print('KHAIDYN_GRIP %s' % json.dumps({'pose': grip_pose, 'roots': roots}))
    write_manifest(h, with_colliders, behaviours, air,
                   _reaction_record(h, reaction, legs, over) if reaction is not None else None)
    return report(h, described)


def report(h, described=None, a_spec=None):
    """The ABP as DescribeAnimGraph reads it, independently of what build() intended: `described` when the build
    already read it, else read now (with a_spec's property paths when given)."""
    if described is None:
        described = _ops().call('MotionTerminal.AnimGraphTools', 'DescribeAnimGraph', AnimBlueprintPath=h['abp_path'],
                                PathsJson=json.dumps(ag.paths(a_spec) if a_spec else {}))
    out = {'hero': h['rig'], 'path': h['abp_path'], 'graph': described}
    print('KHAIDYN_ABP ' + json.dumps(out)[:6000])
    return out


if __name__ == '__main__':
    rig, args = dyn_hero.hero_arg(sys.argv[1:])
    build(dyn_hero.resolve(rig), with_colliders='--no-colliders' not in args)
