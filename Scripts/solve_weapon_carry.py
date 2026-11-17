# Derivation tool for the rifle take's carry transform.
#
# WHY THIS EXISTS: pie_rifle_take_drive.py carries four hard numbers per rig (a RelativeLocation and a
# RelativeRotation in hand_r bone space). Hard numbers with no derivation rot. This script IS the
# derivation — run it against the live PIE world and it prints exactly the constants that script pastes.
#
# THE PROBLEM IT SOLVES. The earlier cube stand-in weapon put IK_Hand_Gun 15 cm straight UP out of hand_r, which sat
# 54.35 cm from upperarm_l against a 54.92 cm two-bone arm — 99% of the chain's reach. The real rifle's
# grip->foregrip span is 40.87 cm. Dropping that span into the cube's orientation puts the foregrip well
# outside the arm, the TwoBoneIK stalls short, and the off-hand rifle reach ("hand plants on the weapon") fails. So
# the carry orientation is DERIVED from the live pose (POSE-DERIVED below) and the transform SOLVED per
# rig, because Khaimera's skeleton is not Manny's.
#
# METHOD. Build an orthonormal frame in world space whose Y' is the desired grip->foregrip direction and
# whose Z' is as close to world up as possible (sights up); build the matching frame in mesh space from
# the two socket positions; the rotation mapping one onto the other is the weapon's desired WORLD
# rotation. Convert into hand_r bone space, then set RelativeLocation = -(R_rel . grip_local) so the
# grip_r socket lands exactly on the hand_r bone.
#
# RUN IT (in PIE through `Scripts/editor_ops.py` (`ops.run_file`), against Lvl_MT_Test for Manny/Quinn and
# Lvl_MT_Test_Khaimera for Khaimera):
#     exec(open(r'.../Scripts/solve_weapon_carry.py').read(), {'unreal': unreal})
# then read the 'SOLVE ...' lines out of the log.
#
# GOTCHA-A': everything lives inside the function; no World/Actor/Component binds to a module-level name.
import math

import unreal

GRIP = (0.0, -13.5, -7.5)     # SM_SovietRifle mesh-local pistol grip  (socket grip_r)
FORE = (0.0, 26.0, 3.0)       # SM_SovietRifle mesh-local fore-grip    (socket IK_Hand_Gun)

# POSE-DERIVED (2026-08-14): the barrel points along the LIVE hand_r -> hand_l vector.
#
# WHY NOT THE CHOSEN DIAGONAL. The original version of this solver used a chosen port-arms diagonal (barrel
# 45 deg left of facing, 30 deg up), and that was a WORKAROUND for a pose that could not hold a rifle.
# This file's own header says so: the unarmed idle's hands are 15 cm up out of hand_r with the foregrip
# at 99% of the left arm's reach, so an honest grip was unreachable and the rifle was bent to the body
# instead. With the rifle-mode branch built (research/aim-offset-neutrality-defect.md, "as built"), the
# character is now IN MF_Rifle_Idle_ADS from spine_01 up — the hands are already placed to hold a
# weapon, roughly grip-span apart. So the direction stops being a judgement call and becomes a
# measurement: aim the barrel down the vector between the hands and the foregrip lands in the off hand
# by construction, with `foregrip <- hand_l` in the report as the check on that claim rather than a
# reach percentage standing in for it. The chosen-diagonal path was removed as unreachable; its
# parameters (45 deg left of facing, 30 deg up) are recorded above if the old constants ever need
# re-deriving.
#
# The roll about the barrel is "sights as close to world up as possible"; it is not taken from the
# hand's twist. Stated because it is the one part of the orientation this solver still chooses rather
# than derives.


def solve_weapon_carry():
    def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
    def add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
    def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    def scal(a, s): return (a[0] * s, a[1] * s, a[2] * s)
    def cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    def norm(a):
        m = math.sqrt(dot(a, a))
        return (a[0] / m, a[1] / m, a[2] / m)
    def vec(t): return unreal.Vector(t[0], t[1], t[2])
    def tup(v): return (v.x, v.y, v.z)
    def dist(a, b): return math.sqrt(dot(sub(a, b), sub(a, b)))

    world = unreal.EditorLevelLibrary.get_game_world()
    pc = unreal.GameplayStatics.get_player_controller(world, 0)
    pawn = pc.get_controlled_pawn()
    mesh = pawn.get_component_by_class(unreal.SkeletalMeshComponent)
    bt = mesh.get_socket_transform('hand_r', unreal.RelativeTransformSpace.RTS_WORLD)
    hand_r = tup(bt.translation)
    hand_l = tup(mesh.get_socket_location('hand_l'))
    up_l = tup(mesh.get_socket_location('upperarm_l'))
    lo_l = tup(mesh.get_socket_location('lowerarm_l'))
    arm = dist(lo_l, up_l) + dist(hand_l, lo_l)      # the two-bone chain the TwoBoneIK actually has

    v = sub(FORE, GRIP)
    span = math.sqrt(dot(v, v))
    # The pose is the specification. hand_r holds the grip, so the barrel should run toward the hand
    # that holds the foregrip. Requires the caller to have driven the rig INTO rifle mode and let it
    # settle first — measuring this on an unarmed idle silently re-derives the old workaround from a
    # pose that cannot hold a rifle, which is the failure this solver exists to retire.
    hand_span = dist(hand_l, hand_r)
    d = norm(sub(hand_l, hand_r))

    e2 = d                                                                 # world frame
    e3 = norm(sub((0.0, 0.0, 1.0), scal(e2, dot((0.0, 0.0, 1.0), e2))))
    e1 = cross(e2, e3)
    f2 = norm(v)                                                           # matching mesh-local frame
    f3 = norm(sub((0.0, 0.0, 1.0), scal(f2, dot((0.0, 0.0, 1.0), f2))))
    f1 = cross(f2, f3)

    def to_world(p):
        return add(add(scal(e1, dot(p, f1)), scal(e2, dot(p, f2))), scal(e3, dot(p, f3)))

    bx = unreal.MathLibrary.inverse_transform_direction(bt, vec(to_world((1.0, 0.0, 0.0))))
    by = unreal.MathLibrary.inverse_transform_direction(bt, vec(to_world((0.0, 1.0, 0.0))))
    rel_rot = unreal.MathLibrary.make_rot_from_xy(bx, by)

    spin = unreal.Transform(unreal.Vector(0, 0, 0), rel_rot, unreal.Vector(1, 1, 1))
    rel_loc = scal(tup(unreal.MathLibrary.transform_location(spin, vec(GRIP))), -1.0)
    rel = unreal.Transform(vec(rel_loc), rel_rot, unreal.Vector(1, 1, 1))

    def predict(p):
        return tup(unreal.MathLibrary.transform_location(bt, unreal.MathLibrary.transform_location(rel, vec(p))))

    g_w, f_w = predict(GRIP), predict(FORE)

    unreal.log('SOLVE rig=%s mode=pose_derived' % pawn.get_class().get_name())
    # The residual IS the story: the barrel points hand-to-hand by construction, so the foregrip can
    # only miss hand_l by however much the hand separation differs from the grip span.
    unreal.log('SOLVE hand_r->hand_l span=%.2f cm vs grip->foregrip span=%.2f cm (residual %.2f cm — '
               'this is the whole error budget for "the off hand is on the foregrip")'
               % (hand_span, span, abs(hand_span - span)))
    unreal.log('SOLVE rel_loc = (%.4f, %.4f, %.4f)' % rel_loc)
    unreal.log('SOLVE rel_rot = (pitch=%.4f, yaw=%.4f, roll=%.4f)' % (rel_rot.pitch, rel_rot.yaw, rel_rot.roll))
    # PASTE-READY for pie_rifle_take_drive.CARRY. unreal.Rotator's constructor takes
    # (roll, pitch, yaw) — NOT the (pitch, yaw, roll) order the line above prints, which is the
    # display order. Emitting the constructor order explicitly removes the transposition footgun.
    unreal.log("SOLVE CARRY-entry: ((%.4f, %.4f, %.4f), (%.4f, %.4f, %.4f))  # (RelativeLocation), "
               "unreal.Rotator(roll, pitch, yaw)"
               % (rel_loc + (rel_rot.roll, rel_rot.pitch, rel_rot.yaw)))
    unreal.log('SOLVE grip_span=%.2f cm  left-arm two-bone length=%.2f cm' % (span, arm))
    unreal.log('SOLVE grip lands on hand_r to %.4f cm (0 == exact)' % dist(g_w, hand_r))
    unreal.log('SOLVE foregrip <- upperarm_l = %.2f cm (%.0f%% of arm)  <- hand_l at rest = %.2f cm'
               % (dist(f_w, up_l), 100.0 * dist(f_w, up_l) / arm, dist(f_w, hand_l)))


solve_weapon_carry()
