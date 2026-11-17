"""Hit reaction, the decisions: pure Python (no editor), unit-tested in test_hit_reaction_core.py.

Spec: docs/specs/2026-10-29-hit-reaction-design.md. A hero's reaction
body comes from its own profile: bodies on body roles, shaped by the intake's body fit (the same capsules Control Rig
Dynamics collides with), parented by the real skeleton, joints from a generic table. No rig name appears here.

  physics_spec(profile, parents, ref_tms, idle_tms)
                                   {'bodies', 'constraints', 'collide', ...} for UHeroPhysicsTools::BuildPhysicsAsset,
                                   every shape explicit bone-local geometry
  body_com(body, tms)              a body's volume-weighted centre of mass, as Chaos computes it
  limb_setup(profile, spec)        the RBwC node's CharacterSetupData.LimbSetupData, as native text
  leg_ik / leg_ik_nodes            the legs planted by IK after the reaction, and the ABP's CopyBone / LegIK text
"""
import math

# SCOPE: every bone from the pelvis to the head (torso_bones) and from the spine to each hand (arm_bones). The torso
# came first and passed on two heroes; the arms joined for the volley. The pelvis
# reacts; the legs follow it, their feet planted by IK (leg_ik), or on a skeleton without IK foot bones the kinematic
# thighs below hold them on the animation (LEG_ANCHORS).
KINEMATIC = ()
ARM_ROLES = ('clavicle', 'upperarm', 'lowerarm', 'hand')
# The hips (a reaction reads more convincingly when the hips take some of the motion): the pelvis is simulated, held to
# the animation by a world-space control (its own linear and angular springs; it has no parent body), and the thighs
# are KINEMATIC bodies with no joint, so RBwC writes their animated component-space transform back (it reads back
# every body, AnimNode_RigidBodyWithControl.cpp:850-866) and the legs below stay the animation's while the pelvis turns
# above them. At a partial reaction alpha the node's local-space blend lets them drift:
# the "legs untouched" gate measures it, fade included.
LEG_ANCHORS = ('thigh_l', 'thigh_r')
# UHeroPhysicsTools' contract version (bump with any change to HeroPhysicsTools.cpp's output): recorded in the build
# manifest beside the physics asset's described contents.
BUILDER_VERSION = 'HeroPhysicsTools/5'
# Radii where the fit measures nothing, as a share of the hero's height (a 181 cm hero: spine 9.1, head 10.0,
# neck 5.1, pelvis 12.7 cm), near the mannequin physics asset's proportions.
DEFAULT_SHARE = {'spine': 0.05, 'head': 0.055, 'neck': 0.028, 'pelvis': 0.07,
                 'clavicle': 0.025, 'upperarm': 0.028, 'lowerarm': 0.022, 'hand': 0.028, 'thigh': 0.05}
# Joint limits (swing1, swing2, twist; degree half-angles) by the CHILD's role class. Applied in the child's bone frame
# (SnapTransformsToDefault); kept round (swing1 == swing2) so a skeleton's bone-axis convention cannot turn a limit
# into the wrong hinge (anatomical frames are out of the torso scope). Narrow: a flinch, not a collapse.
# The asset's joints are angularly FREE: their limit frames are the reference pose's and stay
# there while the animation moves, and they are soft, so on Aurora (r1-r5) 20-30 deg limits fought the animated pose
# and widening them removed the only support the bodies had. The parent-space springs alone restore the pose; the
# joints only hold the bodies together (linear locked, valid because every bone between two bodies has a body).
JOINT_ANGULAR = 'free'
FIT_SHAPE = {'pelvis': ('waist',), 'neck': ('neck',)}     # fitted body shapes by role (spine: torso_<bone>[_p|_n])
# Parent-space control strength, Hz (RigidBodyWithControl.cpp: omega = 2 pi strength), and damping ratio. NOMINAL: in
# this articulated chain the solver's joint drives act far softer (at "10 Hz / 1.0" a hit rang at a ~0.9 s period with
# no visible decay, r7). Sweep on Aurora at a 150 cm/s hit (peak cm / deg, recovery s, running lag cm / deg):
#   10 / 3: 21.0 / 52.5, 4.12, 6.1 / 27     20 / 3: 13.9 / 34.3, 2.60, 6.0 / 29
#   10 / 10: 23.8 / 53.0, 2.77, 5.5 / 24    30 / 5: 12.9 / 30.5, 2.45, 5.9 / 24   (chosen)
# Standing, every row tracks the animation within 0.3 cm; the running lag does not fall with stiffness, so the reaction
# is a transient layer (the drive's reaction_schedule) rather than always on.
# ...and the softness was largely the solver's: the asset's default 6 position iterations leave this chain
# unconverged. At 24 (Aurora volley, 6x arms) the unhit hand tracks the idle within 0.57 cm (2.1 cm at 6) and the
# volley's peak falls from 82 to 30 cm; heavier Khaimera's hand needed 32 (1.18 cm at 24, 0.92 at 32). The sweep
# above predates it.
POSITION_ITERATIONS = 32
CONTROL_STRENGTH_HZ = 30.0
CONTROL_DAMPING_RATIO = 5.0
# The transient: armed just before the impulse, held while the springs carry the flinch, faded back to the animation.
SCHEDULE = {'arm_s': 0.05, 'hold_s': 1.0, 'fade_s': 0.3}
LIMB = 'Spine'
ARM_LIMB = 'Arm'


def role_class(role):
    return role.split('_')[0]


def _shapes_for(profile, role, bone):
    """The profile's fitted body shapes (with local geometry) owned by this bone for this role."""
    # an arm role's fitted shape carries the role's own name (upperarm_l, lowerarm_r)
    names = FIT_SHAPE.get(role_class(role)) or ((role,) if role_class(role) in ARM_ROLES else None)
    out = []
    for s in (profile.get('colliders') or {}).get('shapes') or []:
        if s.get('role') != 'body' or s.get('owner') != bone or 'local' not in s:
            continue
        n = s['name']
        if (names and n in names) or (role_class(role) == 'spine' and n.startswith('torso_' + bone)) or (
                role_class(role) == 'head' and n == 'head'):
            out.append(s)
    return out


def capsule_rotation(axis):
    """A rotation taking local +Z onto axis with its local X LEVEL (X.z = 0): the roll that survives the builder's
    FRotator. The builder stores a capsule's rotation as FQuat(...).Rotator() (HeroPhysicsTools.cpp) and the describe
    reads it back with .Quaternion(); FQuat4d::Rotator (UnrealMath.cpp, SINGULARITY_THRESHOLD 0.4999995) snaps pitch to
    exactly +/-90 once the rotation's X lies within ~1.41e-3 rad of +/-Z, moving the axis that far. collider_autofit's
    q_z_to puts X on -Z for an axis along the bone's X (the usual child-bone direction): L = [30, 0, 0.04] came back
    1.33e-3 off, past shape_matches' AXIS_TOL, on an asset built exactly as asked. A
    level X keeps the pitch test at 0, so the round trip is exact to double precision. Any roll is the same capsule."""
    import collider_autofit as af
    z = af.unit(axis)
    x = af.cross([0.0, 0.0, 1.0], z)
    if af.norm(x) < 1e-6:
        x = af.cross([0.0, 1.0, 0.0], z)                # the axis along +/-Z: X is level to within z's own tilt
    x = af.unit(x)
    return af.q_from_axes(x, af.cross(z, x), z)


def _element(s):
    loc = s['local']
    if s.get('type') == 'capsule' and loc.get('length_cm', 0.0) > 0.0:
        import collider_autofit as af
        # the fit's rotation re-rolled level (capsule_rotation): the same capsule, a rotation the FRotator keeps
        return {'type': 'capsule', 'center': loc['translation'],
                'rotation_xyzw': capsule_rotation(af.qrot(loc['rotation_xyzw'], [0.0, 0.0, 1.0])),
                'radius_cm': loc['radius_cm'],
                # FKSphylElem::Length is the cylinder alone (SphylElem.h:26); the fit's length_cm is segment a->b,
                # i.e. the cylinder (the caps sit beyond a and b), so it carries over as is.
                'length_cm': loc['length_cm'], 'from': 'fit:' + s['name']}
    return {'type': 'sphere', 'center': loc['translation'], 'radius_cm': loc['radius_cm'], 'from': 'fit:' + s['name']}


def torso_bones(roles, parents):
    """Every bone from the pelvis up to the head, in order: a gap (neck_02 between neck_01 and head) would make a body
    hang from a non-adjacent one by a reference-pose anchor that the animated in-between bone moves away from.
    [(role, bone, aim)]: aim is the next bone up (a capsule runs to it), None at the head."""
    path, b = [], roles['head']
    while b and b != roles['pelvis']:
        path.append(b)
        b = parents.get(b)
    if b != roles['pelvis']:
        raise ValueError('head %s does not descend from pelvis %s on this skeleton' % (roles['head'], roles['pelvis']))
    path = [roles['pelvis']] + list(reversed(path))
    spine = roles.get('spine') or []
    top = path.index(spine[-1]) if spine and spine[-1] in path else 0

    def role(i, x):
        if i == 0:
            return 'pelvis'
        if x == roles['head']:
            return 'head'
        return 'spine' if i <= top else 'neck'      # up to the spine top it is spine (helpers included), then neck
    return [(role(i, x), x, path[i + 1] if i + 1 < len(path) else None) for i, x in enumerate(path)]


def arm_bones(roles, parents, torso):
    """Every bone from each hand up to the torso, outermost last per side: [(role, bone, aim)]. A bone between two arm
    roles takes the role below it (the next one toward the hand); aim is the next bone toward the hand, None at the
    hand. A side without a hand role, or whose hand does not reach the torso, is skipped."""
    out = []
    for side in ('l', 'r'):
        hand = roles.get('hand_' + side)
        if not hand:
            continue
        role_of = {roles[k + '_' + side]: k + '_' + side for k in ARM_ROLES if roles.get(k + '_' + side)}
        path, b = [], hand
        while b and b not in torso:
            path.append(b)
            b = parents.get(b)
        if b not in torso:
            continue
        path.reverse()                                  # torso-side first
        current, roled = None, []
        for x in reversed(path):                        # hand first: a helper takes the role below it
            current = role_of.get(x, current)
            roled.append(current)
        roled.reverse()
        out += [(roled[i], x, path[i + 1] if i + 1 < len(path) else None) for i, x in enumerate(path)]
    return out


def hand_aim(hand, lowerarm, parents, ref_tms, depth=2):
    """The bone a hand's capsule runs to: of the hand's descendants (up to depth levels: one rig puts metacarpals
    between the hand and the fingers, another not), the one that best continues the forearm, at least 4 cm out
    (reference component-space transforms, their positions). A hand body centred ON the wrist (a default sphere) gives
    the wrist no lever: Aurora's wrists turned 0.25 deg under the volley at any spring (sweep s1-s3)."""
    if hand not in ref_tms or lowerarm not in ref_tms:
        return None
    ref_cs = {b: tm[:3] for b, tm in ref_tms.items()}
    kids = {}
    for b, p in parents.items():
        kids.setdefault(p, []).append(b)
    level, cands = [hand], []
    for _ in range(depth):
        level = [k for b in level for k in kids.get(b, ())]
        cands += level
    h, f = ref_cs[hand], ref_cs[lowerarm]
    fw = [a - b for a, b in zip(h, f)]
    n = math.sqrt(sum(x * x for x in fw)) or 1.0
    best, aim = 0.0, None
    for c in cands:
        if c not in ref_cs:
            continue
        v = [a - b for a, b in zip(ref_cs[c], h)]
        d = math.sqrt(sum(x * x for x in v))
        score = sum(a * b for a, b in zip(v, fw)) / n / d if d >= 4.0 else -1.0
        if score > best:
            best, aim = score, c
    return aim


def _default(cls, aim, height):
    """A body the fit measures nothing for: a capsule from the bone to its aim bone, else a sphere on the bone."""
    s = DEFAULT_SHARE[cls]
    e = {'type': 'capsule' if aim else 'sphere', 'radius_cm': round(s * height, 2), 'from': 'default:%.3f x height' % s}
    return dict(e, aim=aim) if aim else dict(e, center=[0.0, 0.0, 0.0])


def aimed_length(e):
    """An aimed capsule's cylinder, its caps inside the span from the bone to the aim bone (HeroPhysicsTools.cpp: Length
    max(0, |L| - 2r)): the span is twice the centre's distance (the centre sits at L / 2)."""
    return max(0.0, 2.0 * math.sqrt(sum(x * x for x in e['center'])) - 2.0 * e['radius_cm'])


def resolve_aims(bodies, ref_tms):
    """Each aimed element (in place) as explicit bone-local geometry: the capsule the builder made from the aim bone
    (HeroPhysicsTools.cpp aim path: L = the aim's reference position in the owning bone's reference frame, centre L / 2,
    local Z along L, cylinder max(0, |L| - 2r)). It was rebuilt from the LIVE aim bone wherever the shape was posed
    (overlay, clearance, impact centre), which is not the asset's: the asset's shape is rigid in its bone's frame.
    Any rotation taking Z onto L is the same capsule (axisymmetric). The aim stays as
    'from_aim' (information only: the builder reads 'aim')."""
    import collider_autofit as af
    for body in bodies:
        for i, e in enumerate(body['elements']):
            if not e.get('aim'):
                continue
            bone, aim = body['bone'], e['aim']
            if bone not in ref_tms or aim not in ref_tms:
                raise ValueError('%s aims at %s: no reference transform for %s' % (
                    bone, aim, [b for b in (bone, aim) if b not in ref_tms]))
            o = ref_tms[bone]
            L = af.qrot(af.qconj(o[3:]), af.sub(ref_tms[aim][:3], o[:3]))
            if af.norm(L) < 1e-4:                           # UE_KINDA_SMALL_NUMBER, the builder's own refusal
                raise ValueError('%s and its aim %s coincide' % (bone, aim))
            r = {k: v for k, v in e.items() if k != 'aim'}
            if e.get('type') == 'tapered':
                # its end spheres ON the joints, the bone's and the aim's (Length is the centres' separation:
                # TaperedCapsuleElem.h:35), so neighbouring segments overlap at each joint: inset caps met at a point
                # at the knee, and on a span shorter than its radii (Aurora's waist, 7.35 cm against 9.9) put the
                # centre past the aim
                r.update(center=af.scale(L, 0.5), rotation_xyzw=capsule_rotation(L), length_cm=round(af.norm(L), 4),
                         from_aim=aim)
            else:
                r.update(center=af.scale(L, 0.5), rotation_xyzw=capsule_rotation(L), from_aim=aim)
                r['length_cm'] = aimed_length(r)
            body['elements'][i] = r


def physics_spec(profile, parents, ref_tms=None, idle_tms=None):
    """Bodies and constraints for the hero's hit-reaction physics asset. parents: {bone: parent} of the skeleton;
    ref_tms: {bone: [x, y, z, qx, qy, qz, qw]} the reference pose in component space (the hands' capsules aim from it,
    every aimed shape is resolved in it to the builder's exact bone-local geometry, and a colliding pair must clear in
    it); idle_tms: [{bone: [x, y, z, qx, qy, qz, qw]}] component-space samples of the hero's idle (which arm-torso pairs
    collide). Without ref_tms the spec is a draft: aimed shapes keep their 'aim' (the builder resolves them, and the
    stale checks refuse the result) and no pair collides."""
    roles = profile['roles']
    height = profile['scale']['height_cm']
    expanded = torso_bones(roles, parents)
    expanded += arm_bones(roles, parents, {b for _r, b, _a in expanded})
    for side in ('l', 'r'):
        hand = roles.get('hand_' + side)
        aim = hand_aim(hand, roles.get('lowerarm_' + side), parents, ref_tms or {}) if hand else None
        if aim:
            expanded = [(r, b, aim if b == hand else a) for r, b, a in expanded]
    bodies = []
    for role, bone, aim in expanded:
        if not bone:
            continue
        cls = role_class(role)
        elems = [_element(s) for s in _shapes_for(profile, role, bone)] or [_default(cls, aim, height)]
        bodies.append({'bone': bone, 'role': role, 'elements': elems,
                       'physics_type': 'kinematic' if cls in KINEMATIC else 'simulated'})
    legs = leg_ik(roles, parents)
    anchors = []
    for role in ([] if legs else LEG_ANCHORS):
        bone = roles.get(role)
        if not bone:
            continue
        aim = roles.get(role.replace('thigh', 'calf'))
        elems = [_element(s) for s in (profile.get('colliders') or {}).get('shapes') or []
                 if s.get('role') == 'body' and s.get('owner') == bone and s.get('name') == role and 'local' in s]
        elems = elems or [_default('thigh', aim, height)]
        bodies.append({'bone': bone, 'role': role, 'elements': elems, 'physics_type': 'kinematic'})
        anchors.append(bone)
    have = {b['bone'] for b in bodies}
    if len(have) != len(bodies):
        raise ValueError('a bone takes two body roles: %s' % [b['bone'] for b in bodies])
    constraints = []
    for b in bodies:
        if b['bone'] in anchors:
            continue                               # no joint: a joint would pin the pelvis to the animated legs
        p = parents.get(b['bone'])
        while p and p not in have:
            p = parents.get(p)                     # the nearest BODY ancestor on the real skeleton
        if p is None:
            continue
        constraints.append({'child': b['bone'], 'parent': p, 'angular': JOINT_ANGULAR})
    roots = [b['bone'] for b in bodies if b['bone'] not in {c['child'] for c in constraints} and b['bone'] not in anchors]
    if roots != [roles['pelvis']]:
        raise ValueError('the reaction body must be one tree rooted at the pelvis, roots: %s' % roots)
    if ref_tms is not None:
        resolve_aims(bodies, ref_tms)
    shrunk = slack_to_clear(bodies, idle_tms)
    if ref_tms is not None:
        joint_inertias(bodies, constraints, ref_tms)        # after the shrink: the shapes as built
    collide, idle, ref = colliding(bodies, idle_tms, ref_tms)
    return {'bodies': bodies, 'constraints': constraints, 'shrunk_cm': shrunk,
            'simulated': [b['bone'] for b in bodies if b['physics_type'] == 'simulated'],
            'kinematic': [b['bone'] for b in bodies if b['physics_type'] == 'kinematic'],
            'collision': 'every pair disabled but the arms against the torso, where the idle and the reference pose '
                         'keep them apart',
            'collide': collide, 'idle_clearance_cm': idle, 'ref_clearance_cm': ref, 'leg_ik': legs,
            'fixed_step_s': 1.0 / 60.0, 'position_iterations': POSITION_ITERATIONS}


def leg_ik(roles, parents):
    """The legs planted by IK after the reaction, or [] (the thighs then anchor the legs, LEG_ANCHORS): per side
    {'thigh', 'calf', 'foot', 'target'}, when the foot hangs from the calf and the calf from the thigh (LegIK's
    NumBonesInLimb 2 walks the real parents: AnimNode_LegIK.cpp:843) and the skeleton has an ik_foot_<side> bone that no
    reaction body moves (outside the pelvis's descendants). The ABP copies the incoming foot into that bone before
    the reaction and LegIK puts the foot back on it after, so the thighs follow the pelvis and the feet stay where the
    animation had them (otherwise the leg IK does not follow the hip and the legs look disconnected: the anchors
    held the thighs on the animation while the pelvis moved 5 cm). The thigh must hang below the pelvis: the reach rig
    (leg_reach.py) moves the hips by moving the pelvis. Both legs or neither."""
    def under_pelvis(bone):
        while bone in parents:
            bone = parents[bone]
            if bone == roles['pelvis']:
                return True
        return False
    out = []
    for side in ('l', 'r'):
        thigh, calf, foot = (roles.get('%s_%s' % (k, side)) for k in ('thigh', 'calf', 'foot'))
        target = 'ik_foot_' + side
        if not (thigh and calf and foot and target in parents and not under_pelvis(target) and under_pelvis(thigh)
                and parents.get(foot) == calf and parents.get(calf) == thigh):
            return []
        out.append({'thigh': thigh, 'calf': calf, 'foot': foot, 'target': target})
    return out


def leg_ik_nodes(legs):
    """The ABP's LegIK definition (FAnimLegIKDefinition, AnimNode_LegIK.h:102) and one CopyBone per leg, as native
    text: (copies [(source, target)], legs definition). Knee twist correction off: it re-aims the knee from the foot's
    forward axis (default Y, not every rig's), and the knee plane is the incoming pose's."""
    copies = [(g['foot'], g['target']) for g in legs]
    definition = '(%s)' % ','.join(
        '(FKFootBone=(BoneName="%s"),IKFootBone=(BoneName="%s"),NumBonesInLimb=2,bEnableKneeTwistCorrection=False)'
        % (g['foot'], g['target']) for g in legs)
    return copies, definition


def slack_to_clear(bodies, idle_tms):
    """Shrink each arm body (in place) by the most its idle comes within IDLE_MIN_CLEARANCE_CM of a torso body, for
    overlaps no deeper than SLACK_MAX_CM (the fit's slack: a fitted capsule a little fatter than the arm it hugs); a
    deeper one is anatomy (the shoulder into the chest) and that pair stays apart. {bone: cm shrunk}."""
    out = {}
    for a, b in collide_pairs(bodies):
        gaps = [clearance(bodies, tms, a, b) for tms in idle_tms or ()]
        if gaps and -SLACK_MAX_CM <= min(gaps) < IDLE_MIN_CLEARANCE_CM:
            out[a] = round(max(out.get(a, 0.0), IDLE_MIN_CLEARANCE_CM - min(gaps)), 3)
    for body in bodies:
        if body['bone'] in out:
            for e in body['elements']:
                e['fit_radius_cm'] = e.get('fit_radius_cm', e['radius_cm'])
                e['radius_cm'] = round(max(e['fit_radius_cm'] * SHRINK_FLOOR, e['fit_radius_cm'] - out[body['bone']]),
                                       3)
                if e.get('from_aim'):
                    e['length_cm'] = aimed_length(e)     # the caps stay at the joints, as the builder sized them
    return out


def colliding(bodies, idle_tms, ref_tms=None):
    """([{a, b, idle_clearance_cm, ref_clearance_cm, min_clearance_cm}] for the builder, {"a|b": idle clearance},
    {"a|b": reference-pose clearance}): the arm-torso pairs the idle samples keep IDLE_MIN_CLEARANCE_CM apart AND the
    reference pose keeps COLLIDE_MIN_CLEARANCE_CM apart. The list is EXACT: the builder enables every pair on it (and
    fails on one that does not clear its own reference pose), so the asset's enabled set must equal it
    (hero_onboard.physics_mismatch). With no idle sample or no reference pose no pair
    collides."""
    idle, ref, ref_raw = {}, {}, {}
    for a, b in collide_pairs(bodies):
        gaps = [clearance(bodies, tms, a, b) for tms in idle_tms or ()]
        idle[(a, b)] = round(min(gaps), 3) if gaps else None
        ref_raw[(a, b)] = clearance(bodies, ref_tms, a, b) if ref_tms and a in ref_tms and b in ref_tms else None
        ref[(a, b)] = round(ref_raw[(a, b)], 3) if ref_raw[(a, b)] is not None else None
    # The idle bound is decided on the 3-decimal value, as slack_to_clear sizes its shrink (an unrounded 0.4999999
    # would undo it). The reference bound is decided UNROUNDED, as the builder decides it (Gap >= MinGap) and fails the
    # build on a listed pair under it: a true 1.9996 cm rounds to 2.0 and would have listed a pair the builder refuses
    # The recorded value is the rounded one, so a 2.0 there can be a pair left out.
    return ([{'a': a, 'b': b, 'idle_clearance_cm': g, 'ref_clearance_cm': ref[(a, b)],
              'min_clearance_cm': COLLIDE_MIN_CLEARANCE_CM}
             for (a, b), g in idle.items() if g is not None and g >= IDLE_MIN_CLEARANCE_CM
             and ref_raw[(a, b)] is not None and ref_raw[(a, b)] >= COLLIDE_MIN_CLEARANCE_CM],
            {'%s|%s' % k: g for k, g in idle.items()}, {'%s|%s' % k: g for k, g in ref.items()})


# Self-collision: an arm body below the clavicle against a torso body, so the volley cannot drive a forearm through
# the hips (Aurora v4: 7.8 cm into the pelvis, hit, against 0 unhit). A pair collides only where the
# hero's own idle keeps it apart (IDLE_MIN_CLEARANCE_CM over the sampled idle) and the reference pose leaves room
# (COLLIDE_MIN_CLEARANCE_CM, checked here and again by the builder). A pair touching in the idle dragged the arm off
# the animation: with Aurora's upper arm 0.46 cm into the hip and forearm 0.43 cm into the chest colliding, the UNHIT
# hand lagged 1.31 cm (uniform 6x springs; 0.45 cm before collision) and did not respond to the arm springs; without those pairs, 0.79 cm,
# but then the volley drove the forearms 7.2 cm into the pelvis. So an arm body gives up the fit's slack first
# (slack_to_clear) and every pair it then clears collides. The shoulder's fitted capsules overlap the chest by design
# (11-22 cm in the idle) and never collide.
ARM_COLLIDE = ('upperarm', 'lowerarm', 'hand')
TORSO_COLLIDE = ('pelvis', 'spine', 'neck', 'head')
COLLIDE_MIN_CLEARANCE_CM = 2.0
IDLE_MIN_CLEARANCE_CM = 0.5
SLACK_MAX_CM = 1.5
SHRINK_FLOOR = 0.6          # of the fitted radius


def collide_pairs(bodies):
    """(arm bone, torso bone) for every arm body below the clavicle against every torso body."""
    return [(a['bone'], t['bone']) for a in bodies if role_class(a.get('role') or '') in ARM_COLLIDE
            for t in bodies if role_class(t.get('role') or '') in TORSO_COLLIDE]


def body_capsules(body, tms):
    """A reaction body's shapes as component-space capsules [(a, b, radius)] (a sphere has a == b), posed by the
    recorded bone transforms tms: an explicit element (every element of a current spec: resolve_aims) from its
    bone-local centre and rotation (axis local Z, the cylinder length_cm). An older record's aimed element is
    APPROXIMATED from the live aim bone (centred halfway to it, the caps inside the span), which is the asset's shape
    only at the reference pose."""
    import collider_autofit as af
    tm = tms.get(body['bone'])
    out = []
    for e in body['elements'] if tm else ():
        if 'center' in e:
            c = af.add(tm[:3], af.qrot(tm[3:], e['center']))
            ax = af.qrot(af.qmul(tm[3:], e.get('rotation_xyzw') or [0.0, 0.0, 0.0, 1.0]), [0.0, 0.0, 1.0])
            h = e.get('length_cm', 0.0) / 2 if e.get('type') == 'capsule' else 0.0
        elif e.get('aim') and e['aim'] in tms:
            p, q = tm[:3], tms[e['aim']][:3]
            c, n = af.scale(af.add(p, q), 0.5), math.dist(p, q)
            h = max(0.0, n / 2 - e['radius_cm'])
            ax = af.unit(af.sub(q, p)) if n > 1e-6 else [0.0, 0.0, 1.0]
        else:
            c, ax, h = tm[:3], [0.0, 0.0, 1.0], 0.0
        out.append((af.sub(c, af.scale(ax, h)), af.add(c, af.scale(ax, h)), e['radius_cm']))
    return out


def body_tapered(body, tms):
    """A body's shapes as component-space (near end, far end, near radius, far radius), the cloth source's tapered
    capsules with their two radii (body_capsules gives every shape one radius)."""
    import collider_autofit as af
    tm = tms.get(body['bone'])
    out = []
    for e in body['elements'] if tm else ():
        if e.get('type') != 'tapered' or 'center' not in e:
            out += [(a, b, r, r) for a, b, r in body_capsules(dict(body, elements=[e]), tms)]
            continue
        c = af.add(tm[:3], af.qrot(tm[3:], e['center']))
        ax = af.qrot(af.qmul(tm[3:], e['rotation_xyzw']), [0.0, 0.0, 1.0])
        h = e['length_cm'] / 2.0
        out.append((af.sub(c, af.scale(ax, h)), af.add(c, af.scale(ax, h)), e['radius_cm'], e['radius_end_cm']))
    return out


def body_com_local(body):
    """The body's centre of mass in its bone's frame, as Chaos computes it: each shape's centre weighted by its mass,
    uniform density, so by its volume (MassProperties.cpp CalculateMassPropertiesFromShapeCollection; a capsule
    pi r^2 L + 4/3 pi r^3, L the cylinder, a sphere 4/3 pi r^3; overlapping shapes count twice there too). The plain
    mean of the centres put a focused impact off the body's real centre. Every element
    must be explicit (an unresolved aim has no bone-local centre)."""
    tot, acc = 0.0, [0.0, 0.0, 0.0]
    for e in body['elements']:
        s = element_shape(e)
        if s is None:
            raise ValueError('%s: an aimed element is unresolved (a spec from before explicit geometry): '
                             're-onboard the physics stage' % body['bone'])
        r = s['radius_cm']
        v = math.pi * r * r * s['length_cm'] + 4.0 / 3.0 * math.pi * r ** 3
        tot += v
        acc = [a + v * c for a, c in zip(acc, s['center'])]
    if tot <= 0.0:
        raise ValueError('%s has no volume' % body['bone'])
    return [a / tot for a in acc]


def element_shape(e):
    """A spec element as the shape the builder makes of it ({'type', 'center', 'rotation_xyzw', 'radius_cm',
    'length_cm'}, a sphere's length 0), branch for branch with HeroPhysicsTools.cpp: a capsule with a centre and a
    rotation is built as given; a capsule with an aim and no centre is the builder's to resolve (None here); anything
    else is a sphere at its centre (the bone origin without one)."""
    cap = e.get('type') in ('capsule', 'tapered')
    if cap and 'center' in e and 'rotation_xyzw' in e:
        out = {'type': e['type'], 'center': list(e['center']), 'rotation_xyzw': list(e['rotation_xyzw']),
               'radius_cm': e['radius_cm'], 'length_cm': e['length_cm']}
        if e['type'] == 'tapered':
            out['radius_end_cm'] = e['radius_end_cm']
        return out
    if cap and e.get('aim'):
        return None
    return {'type': 'sphere', 'center': list(e.get('center') or [0.0, 0.0, 0.0]),
            'rotation_xyzw': [0.0, 0.0, 0.0, 1.0], 'radius_cm': e['radius_cm'], 'length_cm': 0.0}


SHAPE_TOL_CM = 0.01
AXIS_TOL = 1e-3


def shape_matches(want, got):
    """Whether a built shape (DescribePhysicsAsset's per-body "shapes") is the wanted one: positions and sizes within
    SHAPE_TOL_CM, a capsule's axis (its rotated +Z, either sense: a capsule is symmetric end to end, and the rotation
    went through an FRotator) within AXIS_TOL per component. The FRotator round trip is exact only for a rotation clear
    of its pitch singularity, which every spec capsule is (capsule_rotation); an older spec's q_z_to rotation can read
    back up to 1.41e-3 off and is refused here, which re-onboarding fixes."""
    import collider_autofit as af
    if want['type'] != got.get('type') or len(got.get('center') or ()) != 3:
        return False
    if any(abs(a - b) > SHAPE_TOL_CM for a, b in zip(want['center'], got['center'])):
        return False
    if abs(want['radius_cm'] - got.get('radius_cm', -1.0)) > SHAPE_TOL_CM:
        return False
    if want['type'] == 'sphere':
        return True
    if abs(want['length_cm'] - got.get('length_cm', -1.0)) > SHAPE_TOL_CM or len(got.get('rotation_xyzw') or ()) != 4:
        return False
    tapered = want['type'] == 'tapered'
    if tapered and abs(want['radius_end_cm'] - got.get('radius_end_cm', -1.0)) > SHAPE_TOL_CM:
        return False
    wa = af.qrot(want['rotation_xyzw'], [0.0, 0.0, 1.0])
    ga = af.qrot(got['rotation_xyzw'], [0.0, 0.0, 1.0])
    # a tapered capsule has a sense (its far end is the other radius); a capsule reads the same either way
    return any(all(abs(a - s * b) <= AXIS_TOL for a, b in zip(wa, ga)) for s in ((1.0,) if tapered else (1.0, -1.0)))


def shapes_mismatch(bone, elements, got):
    """[difference] between a spec body's elements and a built body's shapes (None: an old describe without them).
    Paired greedily, each wanted shape to the first unpaired built one that matches: a sorted pairing could cross two
    mirrored shapes (torso_<bone>_p / _n) whose centres differ by float noise in the sort key."""
    if got is None:
        return ['physics body %s: the read-back has no shapes (a builder from before the shape describe)' % bone]
    want = [element_shape(e) for e in elements]
    if any(w is None for w in want):
        return ['physics body %s: the spec has an unresolved aimed element (no reference pose): regenerate it' % bone]
    left = list(got)
    for w in want:
        hit = next((g for g in left if shape_matches(w, g)), None)
        if hit is None:
            return ['physics body %s: no built shape matches %s (built %s)' % (bone, w, got)]
        left.remove(hit)
    return ['physics body %s: extra built shapes %s' % (bone, left)] if left else []


def body_mass(body):
    """(mass kg, inertia about its centre of mass kg cm^2, transverse to its shapes' axes) as the engine makes them:
    uniform density (the default physical material, DENSITY_KG_CM3), each capsule's tensor as Chaos (Capsule.h:283,
    the cylinder length; a sphere its length 0) moved to the body's centre (parallel axis), then mass raised to
    MASS_POWER with the inertia scaled by the same ratio (BodyUtils.cpp ApplyMassPropertiesModifiers). The scalar is a
    model of the solver's inertia, which inertia conditioning may still change."""
    import collider_autofit as af
    com = body_com_local(body)
    mu_tot, inertia = 0.0, 0.0
    for e in body['elements']:
        s = element_shape(e)
        r, h = s['radius_cm'], s['length_cm']
        mu = DENSITY_KG_CM3 * math.pi * r * r * (h + 4.0 / 3.0 * r)
        mu_tot += mu
        inertia += mu * (5 * h ** 3 + 20 * h * h * r + 45 * h * r * r + 32 * r ** 3) / (60 * h + 80 * r) + \
            mu * af.dot(af.sub(s['center'], com), af.sub(s['center'], com))
    m = mu_tot ** MASS_POWER
    return m, inertia * m / mu_tot


def joint_inertias(bodies, constraints, ref_tms):
    """Each joint's (in place) 'pair_inertia', the solver's 1 / (1/I_parent + 1/I_child) that its acceleration-mode
    drive scales stiffness and damping by (PBDJointCachedSolverGaussSeidel.cpp), and 'carried_inertia', the bodies
    it holds (the child and every body below it) about the joint at the reference pose, I + m d^2 (kg cm^2). Their
    ratio is what a joint's frequency and damping must be scaled by for the chain below it to answer alike on every
    hero (initial_updates)."""
    import collider_autofit as af
    by = {b['bone']: b for b in bodies}
    mass = {b['bone']: body_mass(b) for b in bodies}
    kids = {}
    for c in constraints:
        kids.setdefault(c['parent'], []).append(c['child'])

    def below(bone):
        return [bone] + [x for k in kids.get(bone, []) for x in below(k)]
    for c in constraints:
        ip, ic = mass[c['parent']][1], mass[c['child']][1]
        joint = ref_tms[c['child']][:3]
        carried = 0.0
        for b in below(c['child']):
            d = af.sub(body_com(by[b], ref_tms), joint)
            carried += mass[b][1] + mass[b][0] * af.dot(d, d)
        c['pair_inertia'] = round(1.0 / (1.0 / ip + 1.0 / ic), 4)
        c['carried_inertia'] = round(carried, 4)


def body_com(body, tms):
    """body_com_local posed by the bone transforms tms ({bone: [x, y, z, qx, qy, qz, qw]}): component space."""
    import collider_autofit as af
    tm = tms[body['bone']]
    return af.add(tm[:3], af.qrot(tm[3:], body_com_local(body)))


def seg_distance(p1, q1, p2, q2):
    """The shortest distance between segments p1q1 and p2q2 (Ericson, Real-Time Collision Detection 5.1.9)."""
    d1 = [b - a for a, b in zip(p1, q1)]
    d2 = [b - a for a, b in zip(p2, q2)]
    r = [a - b for a, b in zip(p1, p2)]
    dot = lambda u, v: sum(x * y for x, y in zip(u, v))   # noqa: E731
    a, e, f = dot(d1, d1), dot(d2, d2), dot(d2, r)
    clamp = lambda x: min(1.0, max(0.0, x))   # noqa: E731
    if a <= 1e-9 and e <= 1e-9:
        s = t = 0.0
    elif a <= 1e-9:
        s, t = 0.0, clamp(f / e)
    else:
        c = dot(d1, r)
        if e <= 1e-9:
            s, t = clamp(-c / a), 0.0
        else:
            b = dot(d1, d2)
            den = a * e - b * b
            s = clamp((b * f - c * e) / den) if den > 1e-9 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                s, t = clamp(-c / a), 0.0
            elif t > 1.0:
                s, t = clamp((b - c) / a), 1.0
    return math.dist([x + y * s for x, y in zip(p1, d1)], [x + y * t for x, y in zip(p2, d2)])


def clearance(bodies, tms, a, b):
    """Surface gap between two bodies (negative: they overlap by that much), cm."""
    ca = {x['bone']: x for x in bodies}
    return min(seg_distance(p, q, u, v) - r - s for p, q, r in body_capsules(ca[a], tms)
               for u, v, s in body_capsules(ca[b], tms))


# The bodies a mesh's own cloth (Chaos Cloth clothing assets) collides with, added to the pawn as a cloth collision
# source (USkeletalMeshComponent::AddClothCollisionSource): the hips and legs a draped garment falls over. A stock
# physics asset can lack them (Aurora's: pelvis, spine_03, hands, weapon, head, feet; her right leg cut through her
# dress). (role, aim role, fit segment): each a tapered capsule from the bone toward its aim.
CLOTH_ROLES = (('pelvis', 'spine', 'waist'), ('thigh_l', 'calf_l', 'thigh'), ('thigh_r', 'calf_r', 'thigh'),
               ('calf_l', 'foot_l', 'calf'), ('calf_r', 'foot_r', 'calf'))
CLOTH_SHARE = {'waist': 0.07, 'thigh': 0.05, 'calf': 0.035}     # of height, for a segment the fit did not measure


def cloth_collision_spec(profile, ref_tms=None):
    """Bodies for the cloth collision source: per CLOTH_ROLES the tapered capsule covering that limb's own skin (the
    fit's 'skin': collider_autofit.skin_capsule over the body's non-cloth surface, centred on the limb, not the bone),
    each side its own, no margin (Chaos keeps its own cloth thickness). Not the dynamics colliders: those are the 90th
    percentile of everything the limb carries plus a margin, and stood Aurora's dress 2-3 cm off her legs. The smaller-side rule this replaced shrank her right thigh to the left's and the dress sank 6.3 cm into
    it (dyn_contact, 2026-10-31). Unmeasured: a capsule from the bone to its aim at a height share. Kinematic, no
    joints: collision only. ref_tms (physics_spec's): the geometry made explicit in the bone's reference frame."""
    roles = profile['roles']
    fitted = (profile.get('colliders') or {}).get('fitted') or {}
    height = profile['scale']['height_cm']
    bodies = []
    for role, aim_role, seg in CLOTH_ROLES:
        bone = roles.get(role)
        aim = roles.get(aim_role)
        aim = aim[0] if isinstance(aim, list) and aim else aim          # the spine role is a chain: its first bone
        if not (bone and aim):
            continue
        name = seg + '_' + role.split('_')[1] if '_' in role else seg
        if 'skin_cm' in (fitted.get(name) or {}) and 'skin' not in fitted[name]:
            # an older intake's fit (bone-centred radii over the cloth too): never quietly a height share instead
            raise ValueError('%s: segment %s was fitted by an older intake (skin_cm, no skin capsule): re-run '
                             'hero_intake.py for this hero' % (profile.get('rig'), name))
        skin = (fitted.get(name) or {}).get('skin') if ref_tms is not None else None
        if skin:
            elem = dict(skin_element(skin, ref_tms[bone]), **{'from': 'skin:%s' % name})
        else:
            share = round(CLOTH_SHARE[seg] * height, 2)
            elem = {'type': 'tapered', 'aim': aim, 'radius_cm': share, 'radius_end_cm': share, 'from': 'share:%s' % seg}
        bodies.append({'bone': bone, 'role': role, 'physics_type': 'kinematic', 'elements': [elem]})
    if ref_tms is not None:
        resolve_aims(bodies, ref_tms)
    return {'bodies': bodies, 'constraints': [], 'simulated': [], 'kinematic': [b['bone'] for b in bodies],
            'fixed_step_s': 1.0 / 60.0}


def skin_element(skin, bone_tm):
    """A fitted skin capsule ({'near', 'far' component space, 'radius_cm': [near, far]}) as an explicit tapered element
    in its bone's reference frame (bone_tm: [x, y, z, qx, qy, qz, qw]): local +Z from the near end to the far
    (capsule_rotation), the near radius at -Z (HeroPhysicsTools: Radius1) and the far at +Z."""
    import collider_autofit as af
    near, far = (af.qrot(af.qconj(bone_tm[3:]), af.sub(p, bone_tm[:3])) for p in (skin['near'], skin['far']))
    axis = af.sub(far, near)
    return {'type': 'tapered', 'center': [round(v, 4) for v in af.scale(af.add(near, far), 0.5)],
            'rotation_xyzw': capsule_rotation(axis), 'length_cm': round(af.norm(axis), 4),
            'radius_cm': skin['radius_cm'][0], 'radius_end_cm': skin['radius_cm'][1]}


def limbs(profile, spec=None):
    """[(limb name, start bone)]: each arm from its root-most arm role, then the spine from the first spine bone (the
    pelvis is not in it: a kinematic anchor that the spine's parent-space controls pull against). Order matters: a limb
    takes every body below its start bone not already in an earlier limb (PhysicsControlOperatorNameGeneration.cpp:
    288-323), so the arms come first or the spine would take them."""
    roles = profile['roles']
    out = []
    for side in ('l', 'r'):
        start = next((roles['%s_%s' % (k, side)] for k in ARM_ROLES if roles.get('%s_%s' % (k, side))), None)
        if start:
            out.append(('%s_%s' % (ARM_LIMB, side), start))
    out.append((LIMB, roles['spine'][0]))
    # the leg anchors next, when the spec has them (spec: physics_spec's; legs planted by IK have none), claimed by
    # limbs that create nothing (GetLimbBones claims whatever the flags say), so the pelvis limb below the rest cannot
    # give them its simulated body modifier; the pelvis last, alone
    bodies = None if spec is None else {b['bone'] for b in spec.get('bodies') or []}
    out += [(LEG_LIMB + '_' + r.split('_')[1], roles[r]) for r in LEG_ANCHORS
            if roles.get(r) and (bodies is None or roles[r] in bodies)]
    return out + [(PELVIS_LIMB, roles['pelvis'])]


LEG_LIMB = 'Leg'
PELVIS_LIMB = 'Pelvis'


def limb_setup(profile, spec=None):
    """RBwC CharacterSetupData.LimbSetupData as UE native text: parent-space controls on the arms and the spine, a
    world-space control on the pelvis (the root has no parent body to be held against), nothing on the leg anchors."""
    def flags(n):
        if n == PELVIS_LIMB:
            return True, False, True
        if n.startswith(LEG_LIMB + '_'):
            return False, False, False
        return False, True, True
    return '(%s)' % ','.join(
        '(LimbName="%s",StartBone="%s",bIncludeParentBone=False,bCreateWorldSpaceControls=%s,'
        'bCreateParentSpaceControls=%s,bCreateBodyModifiers=%s)' % ((n, b) + tuple(map(str, flags(n))))
        for n, b in limbs(profile, spec))


# The pelvis's world-space control, Hz / damping ratio: linear and angular both (a control's strengths default to zero,
# and nothing else restores the body's absolute position); angular softer, so the hips turn
# with the hits. Linear was held at 30 Hz so the hips would not stretch from the planted thighs, but there a focused hip
# impact moved the pelvis 1.0 cm (both heroes) and Aurora's lower back 0.9: neither landed its 2 cm. Sweep 2026-10-29
# (hip / lower-back impact cm, Aurora): 30 Hz 1.0 / 0.9, 15 Hz -, 10 Hz 3.3 / 1.6, 6 Hz 5.5 / 2.5; the legs held (0.0
# cm) and the arms stayed out of the body at every setting. The damping ratio was 3 (heavily overdamped: the hips
# resisted the hit's drop); 1 lets the hips give with a downward hit for a squat, with the same
# springs, so the unhit pose is unchanged (Aurora 0.77 -> 0.78 cm). At 4 Hz linear the knees passed 60 deg on both.
PELVIS_LINEAR_HZ = 6.0
PELVIS_ANGULAR_HZ = 12.0
PELVIS_DAMPING_RATIO = 1.0


def world_space_control(linear=None, angular=None, damping=None):
    """CharacterSetupData.DefaultWorldSpaceControlData as native text (only the pelvis limb makes world controls)."""
    d = PELVIS_DAMPING_RATIO if damping is None else damping
    return ('(bEnabled=True,LinearStrength=%f,LinearDampingRatio=%f,AngularStrength=%f,AngularDampingRatio=%f,'
            'bDisableCollision=True)' % (PELVIS_LINEAR_HZ if linear is None else linear, d,
                                         PELVIS_ANGULAR_HZ if angular is None else angular, d))


# The arms' springs, as a multiplier on the default strength (RigidBodyWithControl.cpp:587 multiplies the strength,
# in Hz, before forming the spring, so stiffness goes with its square). At 1x (Aurora v1) the free elbows and wrists
# lagged the idle by 4.4 cm at the hand, unhit, and the volley swung a hand 110 cm. Aurora, hand lag unhit / volley
# peak: at 6 solver iterations 1x 4.4 / 111 cm, 3x 2.7 / 98, 6x 2.1 / 82; at 24 iterations 1x 1.8 / 57, 6x 0.6 / 30.
ARM_STRENGTH_MULTIPLIER = 6.0
# ...and per joint, by the CHILD body's role class (the control drives the child against its parent). A uniform 6x
# put the volley into the clavicles (Aurora v4: clavicle 33 deg, shoulder 8, elbow 0.6, wrist 0.25,
# seen on the render): each drive is an acceleration sized to its own pair of bodies, while the clavicle carries the whole arm.
# The clavicle is held stiff (a real one barely moves); the shoulder, elbow and wrist are left to lag the hit.
# Aurora sweep (clavicle / shoulder / elbow / wrist peak deg, unhit hand lag), multipliers clav/upper/lower/hand:
#   0.5/6/6/6: 98 / 16 / 2.8 / 0.3, 2.25 cm     40/2/1/1: 26 / 10 / 1.6 / 0.25, 1.12 cm
#   12/1/0.3/0.3: 16 / 26 / 5.5 / 0.6, 1.02 cm  (the wrist was dead at any spring: its body sat on the joint)
ARM_JOINT_MULTIPLIER = {'clavicle': 12.0, 'upperarm': 1.0, 'lowerarm': 0.5, 'hand': 0.5}
ARM_JOINT_DAMPING = {}          # per role class damping-ratio multipliers; none: every joint at the base ratio
# The arm springs as hero-independent targets (2026-10-30). One set of multipliers is not one behaviour: an
# acceleration drive scales its stiffness and damping by the joint's PAIR inertia (PBDJointCachedSolverGaussSeidel.cpp),
# but a joint holds the whole chain below it. Khaimera's arm is ten times Aurora's while his clavicle is barely
# heavier, so at Aurora's passing multipliers his elbow held its chain 1.6x stiffer and his shoulder 20% softer: elbow
# 1.3 deg, the unhit hand 1.7 cm. Each joint now gets frequency target x sqrt(R) and damping
# ratio target x sqrt(R), R = carried / pair inertia (joint_inertias), so k / I_carried and c / I_carried - the chain's
# natural frequency and damping in a one-coordinate model (a heuristic: the real chain's modes are
# coupled) - are the targets' on every hero. Calibrated to reproduce Aurora's passing p6d (clavicle / shoulder /
# elbow / wrist 360 / 30 / 7.5 / 7.5 Hz, every ratio 5).
# Aurora's R (carried / pair inertia, left / right): clavicle 1703 / 1554, shoulder 1335 / 1221, elbow 12.1 / 13.0,
# wrist 3.7 / 3.7; each target is p6d's value over the mean sqrt(R) of the two sides. Khaimera then gets clavicle
# ~470 Hz, shoulder ~35, elbow ~4.6, wrist 7.7, damping ratios 6.5 / 5.8 / 3.1 / 5.1.
ARM_TARGET_HZ = {'clavicle': 8.9235, 'upperarm': 0.83949, 'lowerarm': 2.11589, 'hand': 3.89045}
ARM_TARGET_ZETA = {'clavicle': 0.12394, 'upperarm': 0.13991, 'lowerarm': 1.4106, 'hand': 2.59363}
DENSITY_KG_CM3 = 0.001          # the default physical material, 1 g/cm^3 (PhysicalMaterial.cpp:48, BodyUtils.cpp:37)
MASS_POWER = 0.75               # RaiseMassToPower's default (BodyUtils.cpp ApplyMassPropertiesModifiers)


def spec_mismatch(want, got):
    """[difference] between two reaction specs (the generator's current output against a recorded one): bodies, their
    types and shapes, joints, the colliding pairs and the solver iterations. A record from an older generator passes
    physics_mismatch against the asset it built, both stale together."""
    out = []
    wb, gb = {b['bone']: b for b in want['bodies']}, {b['bone']: b for b in got['bodies']}
    if [b['bone'] for b in want['bodies']] != [b['bone'] for b in got['bodies']]:
        out.append('bodies %s, now %s' % (list(gb), list(wb)))
    for bone in sorted(set(wb) & set(gb)):
        if wb[bone]['physics_type'] != gb[bone]['physics_type']:
            out.append('body %s is %s, now %s' % (bone, gb[bone]['physics_type'], wb[bone]['physics_type']))
        shapes = [element_shape(e) for e in gb[bone]['elements']]
        out += shapes_mismatch(bone, wb[bone]['elements'], None if None in shapes else shapes)
    edges = lambda s: sorted((c['child'], c['parent'], c.get('angular')) for c in s['constraints'])   # noqa: E731
    if edges(want) != edges(got):
        out.append('joints %s, now %s' % (edges(got), edges(want)))
    # the joints' inertias set the arm springs (initial_updates): a record without them, or with others, is stale
    held = {(c['child'], c['parent']): c for c in got['constraints']}
    for c in want['constraints']:
        g = held.get((c['child'], c['parent'])) or {}
        for k in ('pair_inertia', 'carried_inertia'):
            if (c.get(k) is None) != (g.get(k) is None) or (c.get(k) is not None and
                                                            abs(c[k] - g[k]) > 1e-3 * max(abs(c[k]), 1e-9)):
                out.append('joint %s->%s %s %s, now %s' % (c['parent'], c['child'], k, g.get(k), c.get(k)))
    pairs = lambda s: sorted(sorted((c['a'], c['b'])) for c in s.get('collide') or [])   # noqa: E731
    if pairs(want) != pairs(got):
        out.append('colliding pairs %s, now %s' % (pairs(got), pairs(want)))
    if (want.get('leg_ik') or []) != (got.get('leg_ik') or []):      # the ABP builds its leg IK from it
        out.append('leg IK %s, now %s' % (got.get('leg_ik'), want.get('leg_ik')))
    if want.get('position_iterations') != got.get('position_iterations'):
        out.append('position iterations %s, now %s' % (got.get('position_iterations'), want.get('position_iterations')))
    return out


def onboard_physics(rec):
    """(reaction body {'spec', 'built'}, cloth collision {'spec', 'built'}) from a hero's onboard record file, each {}
    when absent. The file is stage_runner's ({stage: {'status', 'key', 'record', ...}}): the physics stage's record
    holds both, and only a FINISHED stage counts. A file from before the stages kept them flat ('physics' and
    'cloth_collision' at the top), which is still read."""
    entry = rec.get('physics') if isinstance(rec.get('physics'), dict) else {}
    if 'status' in entry and 'key' in entry:                  # a stage entry
        stage = (entry.get('record') or {}) if entry.get('status') == 'done' else {}
        return ({k: stage[k] for k in ('spec', 'built') if k in stage}, stage.get('cloth_collision') or {})
    return entry, rec.get('cloth_collision') or {}


def _onboard_record(h):
    import json
    with open(h['onboard_record']) as f:
        return json.load(f)


def onboarded_spec(h, current=None):
    """The reaction body the hero was onboarded with (hero_onboard's physics stage record), refused when stale: older
    than explicit geometry, or (current: the spec the generator makes now, hero_onboard.reaction_spec) different from
    what the generator would build today."""
    spec = onboard_physics(_onboard_record(h))[0].get('spec')
    if not spec:
        raise RuntimeError('%s has no physics stage in %s: run hero_onboard --stage physics' % (h['rig'],
                                                                                           h['onboard_record']))
    # A record from an older build (torso only, default iterations) would silently drop the arms from every measure:
    # it must cover every body role and carry the current solver iterations.
    roles = h['profile']['roles']
    need = {roles[k] for k in ('pelvis', 'neck', 'head') if roles.get(k)} | set(roles.get('spine') or []) | {
        roles['%s_%s' % (k, s)] for k in ARM_ROLES for s in ('l', 'r') if roles.get('%s_%s' % (k, s))}
    missing = sorted(need - {b['bone'] for b in spec['bodies']})
    if missing or spec.get('position_iterations') != POSITION_ITERATIONS:
        raise RuntimeError('%s: the onboarded reaction body is stale (missing %s, iterations %s, want %s): run '
                           'hero_onboard --stage physics' % (h['rig'], missing, spec.get('position_iterations'),
                                                             POSITION_ITERATIONS))
    # ...and its shapes explicit (an aimed one was posed from the live aim bone, not as built),
    # its colliding pairs checked at the reference pose too
    loose = sorted(b['bone'] for b in spec['bodies'] if any(element_shape(e) is None for e in b['elements']))
    unref = [c for c in spec.get('collide') or [] if c.get('ref_clearance_cm') is None]
    gaps = (['unresolved aimed shapes on %s' % loose] if loose else []) + (
        ['colliding pairs without a reference clearance'] if unref else []) + (
        spec_mismatch(current, spec) if current is not None else [])
    if gaps:
        raise RuntimeError('%s: the onboarded reaction body is stale (%s): run hero_onboard --stage physics' % (
            h['rig'], '; '.join(gaps)))
    return spec


def onboarded_cloth(h, current):
    """Refuse the hero's onboarded cloth collision bodies when they are not the ones the generator makes today
    (current: cloth_collision_spec at the reference pose): a cloth-only change - the skin-hugging tapered colliders,
    2026-10-30 - left the reaction body as it was, so the reaction check alone kept the old asset."""
    got = (onboard_physics(_onboard_record(h))[1].get('spec') or {}).get('bodies') or []
    held = {b['bone']: b for b in got}
    gaps = [] if [b['bone'] for b in got] == [b['bone'] for b in current['bodies']] else \
        ['cloth bodies %s, now %s' % (list(held), [b['bone'] for b in current['bodies']])]
    for b in current['bodies']:
        if b['bone'] in held:
            shapes = [element_shape(e) for e in held[b['bone']]['elements']]
            gaps += shapes_mismatch(b['bone'], b['elements'], None if None in shapes else shapes)
    if gaps:
        raise RuntimeError('%s: the onboarded cloth collision bodies are stale (%s): run hero_onboard --stage physics'
                           % (h['rig'], '; '.join(gaps[:3])))
    return got


def onboarded_collide(h):
    """[[arm, torso]] pairs the onboarded asset lets collide (the builder's 'collide' report)."""
    built = onboard_physics(_onboard_record(h))[0].get('built') or {}
    return [[c['a'], c['b']] for c in built.get('collide') or [] if c.get('enabled')]


def control_name(parent, child):
    """A parent-space control's name: "<parent body bone>_<child bone>" (PhysicsControlOperatorNameGeneration.h:181-207
    with the bones themselves, PhysicsControlOperatorNameGeneration.cpp:126-127). Unique per pair only as a pair: the
    concatenation is not (initial_updates checks)."""
    return '%s_%s' % (parent, child)


def check_control_names(spec, world=()):
    """Raise when two controls would get the same name. 'a' -> 'b_c' and 'a_b' -> 'c' both make "a_b_c"; UE then
    suffixes the later one "_2" (GetUniqueName, PhysicsControlOperatorNameGeneration.h:106-128, 206), and an update by
    name would land on the wrong joint or none. FName compares case-insensitively, so the
    check does too. world: the world-space controls' names (the child bone alone: no parent)."""
    seen = {}
    for name, edge in [(control_name(c['parent'], c['child']), '%s -> %s' % (c['parent'], c['child']))
                       for c in (spec or {}).get('constraints') or []] + [(w, 'world-space %s' % w) for w in world]:
        if name.lower() in seen:
            raise ValueError('control name %s is made by both %s and %s: UE would rename one, so its update would '
                             'miss' % (name, seen[name.lower()], edge))
        seen[name.lower()] = edge


def arm_joint_tuning(c, cls, hz=None, zeta=None):
    """(strength multiplier, damping-ratio multiplier) of one arm joint from the targets and its inertias
    (ARM_TARGET_HZ / ARM_TARGET_ZETA, joint_inertias), or None when either is missing."""
    hz, zeta = hz or ARM_TARGET_HZ, zeta or ARM_TARGET_ZETA
    if not (hz and zeta and cls in hz and cls in zeta and c.get('pair_inertia') and c.get('carried_inertia')):
        return None
    root = math.sqrt(c['carried_inertia'] / c['pair_inertia'])
    return hz[cls] * root / CONTROL_STRENGTH_HZ, zeta[cls] * root / CONTROL_DAMPING_RATIO


def initial_updates(profile, spec=None, arm_multiplier=None, joints=None, damping=None, target_hz=None,
                    target_zeta=None):
    """RBwC InitialControlAndBodyModifierUpdates as native text: each arm limb's parent-space controls (the set
    "ParentSpace_<limb>", PhysicsControlOperatorNameGeneration.cpp:139-142) get the arm strength multiplier, then each
    arm joint its own by its child body's role class (ARM_JOINT_MULTIPLIER; spec: physics_spec's bodies and
    constraints, helpers carrying the role below them). Applied once at setup, in order, each entry overwriting the
    control's multiplier (RigidBodyWithControl.cpp:849-865), so the per-joint entries win and persist."""
    m = ARM_STRENGTH_MULTIPLIER if arm_multiplier is None else arm_multiplier
    per = ARM_JOINT_MULTIPLIER if joints is None else joints
    damp = ARM_JOINT_DAMPING if damping is None else damping     # per joint damping-ratio multipliers, role class
    # every joint gets a parent-space control (the arm and spine limbs), the pelvis limb a world-space one: all in the
    # node's one name set ("All", PhysicsControlOperatorNameGeneration.cpp:126-127)
    check_control_names(spec, [profile['roles']['pelvis']] if spec else ())
    arms = [n for n, _b in limbs(profile) if n.startswith(ARM_LIMB + '_')]
    entries = ['(Name="ParentSpace_%s",Data=(AngularStrengthMultiplier=%f))' % (n, m) for n in arms]
    role = {b['bone']: b['role'] for b in (spec or {}).get('bodies') or []}
    for c in (spec or {}).get('constraints') or []:
        cls = role_class(role.get(c['child'], ''))
        tuned = None if joints is not None else arm_joint_tuning(c, cls, target_hz, target_zeta)
        if tuned:           # the targets, scaled by this joint's inertias (ARM_TARGET_HZ); an explicit damping wins
            d = damping[cls] if damping and cls in damping else tuned[1]
            entries.append('(Name="%s",Data=(AngularStrengthMultiplier=%f,AngularDampingRatioMultiplier=%f))' % (
                control_name(c['parent'], c['child']), tuned[0], d))
        elif cls in per:    # fixed multipliers: an explicit sweep (joints), or a spec without inertias
            extra = ',AngularDampingRatioMultiplier=%f' % damp[cls] if cls in damp else ''
            entries.append('(Name="%s",Data=(AngularStrengthMultiplier=%f%s))' % (
                control_name(c['parent'], c['child']), per[cls], extra))
    return '(ControlMultiplierParameters=(%s))' % ','.join(entries)


# Gravity on the reacting bodies (a body-modifier multiplier). Under the solver's joint drives the parent-space springs
# did not hold a torso against gravity at a nominal 3 Hz (the head sagged 53 cm, unhit; 25 cm at 20 Hz: r1, r2), so the
# springs track the animation and the hit alone moves the body; the animation already carries the weight.
GRAVITY = 0.0


def body_modifier(gravity=None):
    """CharacterSetupData.DefaultBodyModifierData as native text."""
    return '(MovementType=Simulated,GravityMultiplier=%f)' % (GRAVITY if gravity is None else gravity)


def parent_space_control(strength=None, damping=None):
    """CharacterSetupData.DefaultParentSpaceControlData as native text: the recovery spring. strength / damping
    override the defaults for a sweep (KDYN_REACTION_JSON in dynamics_graph)."""
    # bDisableCollision: a control joint ENABLES collision between its pair unless told otherwise
    # (RigidBodyWithControl.cpp ApplyControl: SetCollisionEnabled(!bDisableCollision)), overriding the asset's table;
    # neighbouring fitted capsules overlap (a torso capsule and the neck), and depenetration spun the torso bodies up to
    # 180 deg on Aurora's first reaction runs (r1-r3).
    return ('(bEnabled=True,LinearStrength=0.000000,AngularStrength=%f,AngularDampingRatio=%f,bDisableCollision=True)'
            % (CONTROL_STRENGTH_HZ if strength is None else strength,
               CONTROL_DAMPING_RATIO if damping is None else damping))
