"""acclaim: the ASF/AMC reader and the conversion to canonical tracks, pinned by hand-worked analytical fixtures.

Design: docs/specs/2026-11-16-mocap-intake-design.md, sections 5 and 6. Every expected number below is worked out by
hand in the comments; nothing is produced by running the code under test and pasting its output.

Notation used in the comments (column vectors, right-handed source space, angles in degrees):
    Rx(90) = [[1,0,0],[0,0,-1],[0,1,0]]    y -> z,  z -> -y
    Ry(90) = [[0,0,1],[0,1,0],[-1,0,0]]    z -> x,  x -> -z
    Rz(90) = [[0,-1,0],[1,0,0],[0,0,1]]    x -> y,  y -> -x
    s      = 2.54 / 0.45 = 5.6444... cm per ASF unit
    B      = [[0,0,1],[-1,0,0],[0,1,0]]    the candidate basis these fixtures declare: source +X left, +Y up,
                                           +Z forward  ->  UE +X forward, +Y right, +Z up (det -1)
    B p    = (p.z, -p.x, p.y)
"""
import math

import pytest

import acclaim as ac

S = 2.54 / 0.45
B = [[0, 0, 1], [-1, 0, 0], [0, 1, 0]]
MIRRORED_B = [[0, 0, 1], [1, 0, 0], [0, 1, 0]]          # B with UE Y flipped: a mirror, det +1
TOL = 1e-9


def _close(a, b, tol=TOL):
    if isinstance(a, (int, float)):
        return abs(a - b) <= tol
    return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))


def _q_close(q, expected, tol=TOL):
    """Same rotation: q equals expected up to the double cover."""
    return _close(q, expected, tol) or _close([-c for c in q], expected, tol)


UNITS = """:units
  mass 1.0
  length 0.45
  angle deg
"""


def _asf(root, bones, hierarchy, units=UNITS, header=':version 1.10\n:name fixture\n'):
    text = header + units + ':documentation\n  hand-written fixture, no captured motion\n'
    text += ':root\n' + ''.join('   %s\n' % line for line in root)
    text += ':bonedata\n'
    for bone in bones:
        text += '  begin\n' + ''.join('     %s\n' % line for line in bone) + '  end\n'
    if hierarchy is not None:
        text += ':hierarchy\n  begin\n' + ''.join('    %s\n' % line for line in hierarchy) + '  end\n'
    return text


ROOT_PLAIN = ['order TX TY TZ RX RY RZ', 'axis XYZ', 'position 0 0 0', 'orientation 0 0 0']


def _bone(i, name, direction, length, axis='0 0 0 XYZ', dof=None, limits=None):
    lines = ['id %d' % i, 'name %s' % name, 'direction %s' % direction, 'length %s' % length, 'axis %s' % axis]
    if dof:
        lines.append('dof %s' % dof)
    if limits:
        lines.append('limits %s' % limits[0])
        lines.extend('       %s' % lim for lim in limits[1:])
    return lines


def _amc(*frames, header=':FULLY-SPECIFIED\n:DEGREES\n'):
    text = '# hand-written fixture\n' + header
    for number, lines in frames:
        text += '%d\n' % number + ''.join('%s\n' % line for line in lines)
    return text


def _canon(asf_text, amc_text, basis=B, **kw):
    skel = ac.parse_asf(asf_text)
    motion = ac.parse_amc(amc_text, skel)
    kw.setdefault('fps', 120)
    return ac.to_canonical(skel, motion, basis, **kw)


def _source_world(asf_text, amc_text, frame=0):
    skel = ac.parse_asf(asf_text)
    motion = ac.parse_amc(amc_text, skel)
    return ac.source_world(skel, ac.source_locals(skel, motion)[frame])


def _refused(reason, fn, *args, **kw):
    with pytest.raises(ac.AcclaimError) as info:
        fn(*args, **kw)
    assert info.value.state == 'unsupported'
    assert info.value.reason == reason, (info.value.reason, str(info.value))
    return info.value


# ---------------------------------------------------------------------------------------------------------------
# Fixture A: a non-XYZ axis order with two nonzero, non-commuting angles; a reordered subset of DOFs; a fixed child.
#
#   b1: axis 90 90 0 YXZ   ->  values are the x, y, z angles; the letters are the order they apply in, first
#       letter first.  C = Rx(90) . Ry(90)   (Y applied first, then X; Z is 0)
#         Rx.Ry: row [1,0,0] -> [0,0,1]; row [0,0,-1] -> -[-1,0,0] = [1,0,0]; row [0,1,0] -> [0,1,0]
#         C = [[0,0,1],[1,0,0],[0,1,0]]      C^-1 = C^T = [[0,1,0],[0,0,1],[1,0,0]]
#       dof rz rx, AMC "b1 90 90"  ->  rz = 90 applied first, then rx = 90:  M = Rx(90) . Rz(90)
#         Rx.Rz: row [1,0,0] -> [0,-1,0]; row [0,0,-1] -> [0,0,-1]; row [0,1,0] -> [1,0,0]
#         M = [[0,-1,0],[0,0,-1],[1,0,0]]
#       L = C M C^-1:
#         M C^-1 = [[0,0,-1],[-1,0,0],[0,1,0]]
#         C (M C^-1) = [[0,1,0],[0,0,-1],[-1,0,0]]
#   b2 (fixed) joint = b1 joint + L (2,0,0) = 2 * (column 1 of L) = (0,0,-2)
#   UE: R_UE = B L B^-1.  L B^T = [[0,0,1],[-1,0,0],[0,1,0]];  B (L B^T) = [[0,1,0],[0,0,-1],[-1,0,0]]
#       quaternion (trace 0): w = 1/2, x = (R32-R23)/4w = (0+1)/2, y = (R13-R31)/4w = (0+1)/2,
#       z = (R21-R12)/4w = (0-1)/2   ->  (0.5, 0.5, 0.5, -0.5)
#       b2 position: s B (0,0,-2) = s (-2, 0, 0)
# ---------------------------------------------------------------------------------------------------------------
ASF_A = _asf(ROOT_PLAIN,
             [_bone(1, 'b1', '1 0 0', 2, axis='90 90 0 YXZ', dof='rz rx', limits=['(-180 180)', '(-90 90)']),
              _bone(2, 'b2', '0 1 0', 1)],
             ['root b1', 'b1 b2'])
AMC_A = _amc((1, ['root 0 0 0 0 0 0', 'b1 90 90']))
L_A = [[0, 1, 0], [0, 0, -1], [-1, 0, 0]]


def test_asf_parse_reads_units_root_bones_and_hierarchy():
    skel = ac.parse_asf(ASF_A)
    assert skel['units'] == {'mass': 1.0, 'length': 0.45, 'angle': 'deg'}
    assert skel['root'] == {'order': ['tx', 'ty', 'tz', 'rx', 'ry', 'rz'], 'axis': 'XYZ',
                            'position': [0.0, 0.0, 0.0], 'orientation': [0.0, 0.0, 0.0]}
    b1 = skel['bones']['b1']
    assert b1['id'] == 1 and b1['direction'] == [1.0, 0.0, 0.0] and b1['length'] == 2.0
    assert b1['axis'] == [90.0, 90.0, 0.0] and b1['axis_order'] == 'YXZ'
    assert b1['dof'] == ['rz', 'rx'] and b1['limits'] == [[-180.0, 180.0], [-90.0, 90.0]]
    assert skel['bones']['b2']['dof'] == [] and skel['bones']['b2']['limits'] == []
    assert skel['parent'] == {'b1': 'root', 'b2': 'b1'}
    assert skel['order'] == ['root', 'b1', 'b2']


def test_amc_parse_keeps_each_bones_channels_in_its_dof_order():
    skel = ac.parse_asf(ASF_A)
    motion = ac.parse_amc(AMC_A, skel)
    assert motion['directives'] == {'fully_specified': True, 'angle': 'deg'}
    assert motion['frames'] == [{'frame': 1, 'channels': {'root': {'tx': 0.0, 'ty': 0.0, 'tz': 0.0, 'rx': 0.0,
                                                                     'ry': 0.0, 'rz': 0.0},
                                                            'b1': {'rz': 90.0, 'rx': 90.0}}}]
    assert list(motion['frames'][0]['channels']['b1']) == ['rz', 'rx']


def test_fixture_a_local_is_c_m_c_inverse_with_yxz_axis_and_rz_then_rx():
    skel = ac.parse_asf(ASF_A)
    local = ac.source_locals(skel, ac.parse_amc(AMC_A, skel))[0]
    assert _close(local['b1'][0], L_A)
    world = ac.source_world(skel, local)
    assert _close(world['b2'][0], [0, 0, -2])


def test_fixture_a_canonical_rotation_and_position_in_ue():
    canon = _canon(ASF_A, AMC_A)
    assert _q_close(canon['frames']['local_q']['b1'][0], [0.5, 0.5, 0.5, -0.5])
    assert _close(ac.matrix_from_quat(canon['frames']['local_q']['b1'][0]), [[0, 1, 0], [0, 0, -1], [-1, 0, 0]])
    world = ac.forward_kinematics(canon, 0)
    assert _close(world['b2'][0], [-2 * S, 0, 0])


def test_fixture_a_xyz_reading_of_the_same_angles_differs():
    # Read as XYZ instead: C' = Ry(90) . Rx(90), not the YXZ matrix above, so the order letters matter here.
    xyz = ASF_A.replace('90 90 0 YXZ', '90 90 0 XYZ')
    skel = ac.parse_asf(xyz)
    local = ac.source_locals(skel, ac.parse_amc(AMC_A, skel))[0]
    assert not _close(local['b1'][0], L_A, 1e-6)


def test_axis_values_are_x_y_z_angles_not_per_letter():
    # axis 0 90 0 ZXY: read as x, y, z angles only y = 90, so C = Ry(90); dof rx 90 then gives
    #   L = Ry(90) Rx(90) Ry(90)^-1 = a 90 rotation about Ry(90) x = (0,0,-1) = Rz(-90) = [[0,1,0],[-1,0,0],[0,0,1]]
    # Read per letter (Z = 0, X = 90, Y = 0) it would be C = Rx(90) and L = Rx(90): this fixture tells them apart.
    asf = _asf(ROOT_PLAIN, [_bone(1, 'arm', '1 0 0', 1, axis='0 90 0 ZXY', dof='rx')], ['root arm'])
    skel = ac.parse_asf(asf)
    local = ac.source_locals(skel, ac.parse_amc(_amc((1, ['root 0 0 0 0 0 0', 'arm 90'])), skel))[0]
    assert _close(local['arm'][0], [[0, 1, 0], [-1, 0, 0], [0, 0, 1]])


def test_fixture_a_swapped_dof_order_differs():
    # M' = Rz(90) . Rx(90) (rx first) would be a different rotation; the reader composes in the listed dof order.
    swapped = ASF_A.replace('dof rz rx', 'dof rx rz')
    skel = ac.parse_asf(swapped)
    local = ac.source_locals(skel, ac.parse_amc(AMC_A, skel))[0]
    assert not _close(local['b1'][0], L_A, 1e-6)


# ---------------------------------------------------------------------------------------------------------------
# Fixture B: a nonzero static root position, an animated root on a reordered subset of channels, an asymmetric
# hierarchy (a two-bone left chain, a one-bone fixed right chain) and a fixed bone. The root orientation is 0 0 0:
# a nonzero one is refused (its composition is unverified; see test_nonzero_root_orientation_is_refused).
#
#   root: order RY TZ TX TY, axis XYZ, position (1,0,0), orientation (0,0,0)  ->  O = I
#         AMC "root 90 3 0 2"  ->  ry = 90, T = (tx,ty,tz) = (0,2,3);  M_root = Ry(90)
#           R_root = Ry(90) = [[0,0,1],[0,1,0],[-1,0,0]]
#           p_root = position + T = (1,0,0) + (0,2,3) = (1,2,3)
#   lleg: direction (0,-1,0) length 4, axis 0 90 0 XYZ -> C = Ry(90); dof rx; AMC 90
#         L = Ry(90) Rx(90) Ry(90)^-1 = a 90 rotation about Ry(90) x = (0,0,-1)  ->  Rz(-90) = [[0,1,0],[-1,0,0],[0,0,1]]
#         R_lleg = R_root . Rz(-90): row [0,0,1] -> [0,0,1]; row [0,1,0] -> [-1,0,0];
#                                    row [-1,0,0] -> -[0,1,0] = [0,-1,0]   ->  [[0,0,1],[-1,0,0],[0,-1,0]]
#   lfoot joint = p_root + R_lleg (0,-4,0) = (1,2,3) + (-4)(column 2 = (0,0,-1)) = (1,2,7)
#   lfoot: direction (0,0,1) length 1, dof rz, AMC 90:  R_lfoot = R_lleg . Rz(90)
#         row [0,0,1] -> [0,0,1]; row [-1,0,0] -> -[0,-1,0] = [0,1,0]; row [0,-1,0] -> -[1,0,0]
#         ->  [[0,0,1],[0,1,0],[-1,0,0]]
#   ltoe joint = lfoot + R_lfoot (0,0,1) = (1,2,7) + (column 3 = (1,0,0)) = (2,2,7)
#   rleg (fixed): direction (0,-1,0) length 3; rfoot joint = p_root + R_root (0,-3,0) = (1,2,3) + (-3)(0,1,0)
#         = (1,-1,3)
#   UE (B p = (p.z, -p.x, p.y), times s):
#       pelvis_t = s (3, -1, 2)   lfoot = s (7, -1, 2)   ltoe = s (7, -2, 2)   rfoot = s (3, -1, -1)
#       rest_offset root = s B (1,0,0) = s (0,-1,0);  rest_rotation root = B I B^-1 = identity
# ---------------------------------------------------------------------------------------------------------------
ASF_B = _asf(['order RY TZ TX TY', 'axis XYZ', 'position 1 0 0', 'orientation 0 0 0'],
             [_bone(1, 'lleg', '0 -1 0', 4, axis='0 90 0 XYZ', dof='rx', limits=['(-160 20)']),
              _bone(2, 'lfoot', '0 0 1', 1, dof='rz', limits=['(-45 45)']),
              _bone(3, 'ltoe', '0 0 1', 0.5),
              _bone(4, 'rleg', '0 -1 0', 3),
              _bone(5, 'rfoot', '0 0 1', 1)],
             ['root lleg rleg', 'lleg lfoot', 'lfoot ltoe', 'rleg rfoot'])
AMC_B = _amc((1, ['root 90 3 0 2', 'lleg 90', 'lfoot 90']))


def test_fixture_b_static_root_plus_animated_root_in_source_space():
    world = _source_world(ASF_B, AMC_B)
    assert _close(world['root'][0], [1, 2, 3])
    assert _close(world['root'][1], [[0, 0, 1], [0, 1, 0], [-1, 0, 0]])
    assert _close(world['lleg'][1], [[0, 0, 1], [-1, 0, 0], [0, -1, 0]])
    assert _close(world['lfoot'][0], [1, 2, 7])
    assert _close(world['lfoot'][1], [[0, 0, 1], [0, 1, 0], [-1, 0, 0]])
    assert _close(world['ltoe'][0], [2, 2, 7])
    assert _close(world['rfoot'][0], [1, -1, 3])


def test_fixture_b_canonical_positions_and_rest_in_ue():
    canon = _canon(ASF_B, AMC_B)
    assert _close(canon['frames']['pelvis_t'][0], [3 * S, -S, 2 * S])
    world = ac.forward_kinematics(canon, 0)
    assert _close(world['lfoot'][0], [7 * S, -S, 2 * S])
    assert _close(world['ltoe'][0], [7 * S, -2 * S, 2 * S])
    assert _close(world['rfoot'][0], [3 * S, -S, -S])
    bones = {b['name']: b for b in canon['bones']}
    assert bones['root']['parent'] is None and _close(bones['root']['rest_offset'], [0, -S, 0])
    assert _q_close(bones['root']['rest_rotation'], [1, 0, 0, 0])
    # a child's rest offset is its parent's segment vector: lfoot sits at lleg's 4 units of (0,-1,0) -> s B = s (0,0,-4)
    assert bones['lfoot']['parent'] == 'lleg' and _close(bones['lfoot']['rest_offset'], [0, 0, -4 * S])
    assert _close(bones['rleg']['rest_offset'], [0, 0, 0]) and _close(bones['rfoot']['rest_offset'], [0, 0, -3 * S])
    # the fixed bones still have a rotation track, at rest (identity) on every frame
    assert _q_close(canon['frames']['local_q']['rleg'][0], [1, 0, 0, 0])


def test_nonzero_root_orientation_is_refused():
    # O M versus O M O^-1 is unverified, so a nonzero static root orientation is refused rather than guessed
    error = _refused('root_orientation', ac.parse_asf, ASF_B.replace('orientation 0 0 0', 'orientation 0 0 90'))
    assert 'unverified' in str(error)


def test_fixture_b_fixed_bone_in_amc_is_refused():
    skel = ac.parse_asf(ASF_B)
    _refused('channel_count', ac.parse_amc, _amc((1, ['root 90 3 0 2', 'lleg 90', 'lfoot 90', 'rleg 0'])), skel)


# ---------------------------------------------------------------------------------------------------------------
# Fixture C (handedness): the source declares +X left, +Y up, +Z forward. Only the left arm is raised.
#   lclav fixed, direction (1,0,0) length 1  -> larm joint at (1,0,0)
#   larm direction (1,0,0) length 2, axis 0 90 0 XYZ -> C = Ry(90), dof rx, AMC -90:
#        L = Ry(90) Rx(-90) Ry(90)^-1 = a -90 rotation about (0,0,-1) = Rz(+90): the arm swings from +X to +Y (up)
#   lhand = (1,0,0) + Rz(90)(2,0,0) = (1,2,0)     -> UE s B (1,2,0) = s (0, -1, 2): left (UE -Y) and up
#   rhand = (-1,0,0) + (-2,0,0) = (-3,0,0)          -> UE s (0, 3, 0): right, not raised
#   fwdtip = (0,0,1)                               -> UE s (1, 0, 0): forward
# Frame 2 turns the root +90 about source up (RY 90): Ry(90)(0,0,1) = (1,0,0), toward the subject's left;
#   UE: s B (1,0,0) = s (0,-1,0). R_UE root = B Ry(90) B^-1 = [[0,1,0],[-1,0,0],[0,0,1]] = Rz_UE(-90):
#   Ry.B^T = [[1,0,0],[0,0,1],[0,1,0]], B.(Ry.B^T) = [[0,1,0],[-1,0,0],[0,0,1]]; q = (c45, 0, 0, -s45).
#   With det(B) = -1 a positive source turn reads as a negative angle about UE +Z, which is still a turn to the left.
# The mirrored basis (UE Y flipped) puts the raised hand at +Y and turns the subject to +Y: it fails both.
# ---------------------------------------------------------------------------------------------------------------
ASF_C = _asf(ROOT_PLAIN,
             [_bone(1, 'lclav', '1 0 0', 1),
              _bone(2, 'larm', '1 0 0', 2, axis='0 90 0 XYZ', dof='rx', limits=['(-180 180)']),
              _bone(3, 'lhand', '1 0 0', 0.5),
              _bone(4, 'rclav', '-1 0 0', 1),
              _bone(5, 'rarm', '-1 0 0', 2),
              _bone(6, 'rhand', '-1 0 0', 0.5),
              _bone(7, 'fwd', '0 0 1', 1),
              _bone(8, 'fwdtip', '0 0 1', 0.5)],
             ['root lclav rclav fwd', 'lclav larm', 'larm lhand', 'rclav rarm', 'rarm rhand', 'fwd fwdtip'])
AMC_C = _amc((1, ['root 0 0 0 0 0 0', 'larm -90']), (2, ['root 0 0 0 0 90 0', 'larm -90']))


def _handedness(canon):
    """(left hand raised on the UE left, positive source turn faces UE left) for a canonical take of fixture C."""
    first, second = ac.forward_kinematics(canon, 0), ac.forward_kinematics(canon, 1)
    lhand, rhand = first['lhand'][0], first['rhand'][0]
    raised_left = lhand[2] > rhand[2] and lhand[1] < 0 < rhand[1]          # UE +Y is right, so left is -Y
    facing = [b - a for a, b in zip(second['root'][0], second['fwdtip'][0])]
    return raised_left, _close(facing, [0, -S, 0], 1e-9)


def test_fixture_c_left_arm_and_positive_turn_land_where_the_basis_calls_left():
    canon = _canon(ASF_C, AMC_C)
    world = ac.forward_kinematics(canon, 0)
    assert _close(world['lhand'][0], [0, -S, 2 * S])
    assert _close(world['rhand'][0], [0, 3 * S, 0])
    assert _close(world['fwdtip'][0], [S, 0, 0])
    c45 = math.sqrt(0.5)
    assert _q_close(canon['frames']['local_q']['root'][1], [c45, 0, 0, -c45])
    assert _handedness(canon) == (True, True)


def test_fixture_c_mirrored_basis_fails_both_and_is_refused():
    skel = ac.parse_asf(ASF_C)
    motion = ac.parse_amc(AMC_C, skel)
    mirrored = ac.to_canonical(skel, motion, MIRRORED_B, fps=120, allow_any_handedness=True)
    assert _handedness(mirrored) == (False, False)
    _refused('basis', ac.to_canonical, skel, motion, MIRRORED_B, fps=120)


def test_fixture_c_non_permutation_basis_is_refused():
    skel = ac.parse_asf(ASF_C)
    motion = ac.parse_amc(AMC_C, skel)
    _refused('basis', ac.to_canonical, skel, motion, [[0, 0, 2], [-1, 0, 0], [0, 1, 0]], fps=120)
    _refused('basis', ac.to_canonical, skel, motion, [[0, 0, 1], [-1, 0, 0], [0, 0, 1]], fps=120)


# ---------------------------------------------------------------------------------------------------------------
# The composition-order mutation: C^-1 M C (the row form transliterated into column vectors) must change the result on
# every analytical fixture, or that fixture would not discriminate the composition.
#   A: b1 under the swap: C^-1 M C differs from C M C^-1 = L_A (checked numerically below).
#   B: lleg under the swap is Ry^-1 Rx(90) Ry = a 90 rotation about Ry(-90) x = (0,0,1): Rz(+90), not Rz(-90).
#   C: larm under the swap is a -90 rotation about (0,0,+1): the arm swings down, so the hand is not raised.
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('asf_text, amc_text, bone', [(ASF_A, AMC_A, 'b2'), (ASF_B, AMC_B, 'lfoot'),
                                                       (ASF_C, AMC_C, 'lhand')])
def test_swapped_composition_changes_every_fixture(monkeypatch, asf_text, amc_text, bone):
    right = _source_world(asf_text, amc_text)[bone]
    monkeypatch.setattr(ac, 'compose_local', lambda c, m: ac.mat_mul(ac.mat_mul(ac.transpose(c), m), c))
    swapped = _source_world(asf_text, amc_text)[bone]
    assert not (_close(right[0], swapped[0], 1e-6) and _close(right[1], swapped[1], 1e-6))


def test_swapped_dof_composition_changes_fixture_a(monkeypatch):
    right = _source_world(ASF_A, AMC_A)['b1'][1]
    real = ac.compose_motion
    monkeypatch.setattr(ac, 'compose_motion', lambda rotations: real(list(reversed(rotations))))
    assert not _close(_source_world(ASF_A, AMC_A)['b1'][1], right, 1e-6)


# ---------------------------------------------------------------------------------------------------------------
# Units: s is applied exactly once, to segment offsets and root translations, and never to rotations.
#   lclav length 1 -> larm rest offset s B (1,0,0) = (0, -s, 0) = (0, -5.6444.., 0) cm, not s^2 and not 1.
#   Fixture B pelvis_t s B (1,2,3) = (3s, -s, 2s): the static position and the AMC translation are both scaled once.
# ---------------------------------------------------------------------------------------------------------------
def test_unit_scale_applied_exactly_once():
    assert ac.CM_PER_UNIT == pytest.approx((1 / 0.45) * 2.54 / 100 * 100, abs=1e-12)
    canon = _canon(ASF_C, AMC_C)
    bones = {b['name']: b for b in canon['bones']}
    assert _close(bones['larm']['rest_offset'], [0, -5.644444444444445, 0], 1e-12)
    assert canon['units'] == 'cm' and canon['basis']['scale_cm_per_unit'] == pytest.approx(S, abs=1e-12)
    assert canon['basis']['det'] == -1 and canon['basis']['B'] == B
    assert canon['provenance']['asf_units'] == {'length': 0.45, 'angle': 'deg'}
    assert _close(_canon(ASF_B, AMC_B)['frames']['pelvis_t'][0], [3 * S, -S, 2 * S])


def test_canonical_header_states_its_space():
    canon = _canon(ASF_A, AMC_A, fps_provenance='unverified', source_sha256={'asf': 'a' * 64})
    assert canon['schema'] == 2
    assert canon['space'] == {'handedness': 'left', 'up': '+Z', 'forward': '+X', 'right': '+Y'}
    assert canon['fps'] == 120 and canon['fps_provenance'] == 'unverified'
    assert canon['provenance']['source_sha256'] == {'asf': 'a' * 64}
    assert len(canon['provenance']['converter']) == 64
    assert ac.dumps(canon) == ac.dumps(ac.loads(ac.dumps(canon)))


# ---------------------------------------------------------------------------------------------------------------
# Time: source frames 5..8 at 120 fps.  t_s = (source_frame - 5)/120 -> 0, 1/120, 2/120, 3/120; N = 4 samples
# last (4-1)/120 = 0.025 s.  Root TX = 0,1,2,3 and RY = 0,30,60,90.
# Trim to [6, 8]: source_frame 6,7,8, t_s 0, 1/120, 2/120.
# Resample to 80 fps: step 120/80 = 1.5 source frames: source_frame 5, 6.5, 8; t_s 0, 1/80, 2/80 = 0.025 s.
#   at 6.5: TX lerps to 1.5 -> pelvis_t s B (1.5,0,0) = s (0,-1.5,0); RY slerps to 45 -> UE about +Z by -45.
# ---------------------------------------------------------------------------------------------------------------
ASF_T = _asf(ROOT_PLAIN, [_bone(1, 'spine', '0 1 0', 1)], ['root spine'])
AMC_T = _amc(*[(5 + i, ['root %d 0 0 0 %d 0' % (i, 30 * i)]) for i in range(4)])


def test_time_follows_source_frames_and_n_minus_one_over_fps():
    canon = _canon(ASF_T, AMC_T)
    assert canon['frames']['source_frame'] == [5, 6, 7, 8]
    assert _close(canon['frames']['t_s'], [0, 1 / 120, 2 / 120, 3 / 120])
    assert ac.duration_s(canon) == pytest.approx(3 / 120, abs=1e-15)


def test_trim_keeps_source_frames():
    canon = _canon(ASF_T, AMC_T, source_frames=[6, 8])
    assert canon['frames']['source_frame'] == [6, 7, 8]
    assert _close(canon['frames']['t_s'], [0, 1 / 120, 2 / 120])
    assert _close(canon['frames']['pelvis_t'][0], [0, -S, 0])
    _refused('time', _canon, ASF_T, AMC_T, source_frames=[4, 8])
    _refused('time', _canon, ASF_T, AMC_T, source_frames=[7, 6])


def test_resample_keeps_fractional_source_frames():
    out = ac.resample(_canon(ASF_T, AMC_T), 80)
    assert out['fps'] == 80
    assert _close(out['frames']['source_frame'], [5, 6.5, 8])
    assert _close(out['frames']['t_s'], [0, 1 / 80, 2 / 80])
    assert ac.duration_s(out) == pytest.approx(0.025, abs=1e-12)
    assert _close(out['frames']['pelvis_t'][1], [0, -1.5 * S, 0])
    c, s = math.cos(math.radians(22.5)), math.sin(math.radians(22.5))
    assert _q_close(out['frames']['local_q']['root'][1], [c, 0, 0, -s], 1e-12)
    assert out['frames']['end_sample'] == [False, False, False]       # 0.025 s is a multiple of 1/80: no extra sample


# Resample tail: the last source sample is kept. 0.025 s is not a multiple of the output step, so the grid samples are
# followed by one more at the source end time, flagged `end_sample` (it is off the k / fps_out grid).
#   60 fps: grid t = 0, 1/60 (source frame 5 + 120/60 = 7); 2/60 > 0.025, so the end sample t = 3/120, frame 8.
#   30 fps: grid t = 0 only (1/30 > 0.025); end sample t = 3/120, frame 8, TX = 3 -> pelvis_t s B (3,0,0) = s (0,-3,0).
def test_resample_keeps_the_last_source_sample():
    at60 = ac.resample(_canon(ASF_T, AMC_T), 60)['frames']
    assert _close(at60['source_frame'], [5, 7, 8]) and _close(at60['t_s'], [0, 1 / 60, 3 / 120])
    assert at60['end_sample'] == [False, False, True]
    out = ac.resample(_canon(ASF_T, AMC_T), 30)
    frames = out['frames']
    assert _close(frames['source_frame'], [5, 8]) and _close(frames['t_s'], [0, 3 / 120])
    assert frames['end_sample'] == [False, True]
    assert _close(frames['pelvis_t'][1], [0, -3 * S, 0])
    assert ac.duration_s(out) == pytest.approx(3 / 120, abs=1e-15)     # the take's duration survives the resample


# ---------------------------------------------------------------------------------------------------------------
# Quaternions: unit length and sign-continuous.  RY = 0, 120, 240, 360 about source up -> about UE +Z by
# 0, -120, -240, -360. Half angles 0, -60, -120, -180: (1,0,0,0), (0.5,0,0,-0.866), (-0.5,0,0,-0.866), (-1,0,0,0).
# A matrix-to-quaternion conversion alone would return w >= 0 on frames 3 and 4; continuity flips them.
# ---------------------------------------------------------------------------------------------------------------
def test_quaternions_are_normalised_and_sign_continuous():
    amc = _amc(*[(1 + i, ['root 0 0 0 0 %d 0' % (120 * i)]) for i in range(4)])
    track = _canon(ASF_T, amc)['frames']['local_q']['root']
    h = math.sqrt(3) / 2
    for q, expected in zip(track, [[1, 0, 0, 0], [0.5, 0, 0, -h], [-0.5, 0, 0, -h], [-1, 0, 0, 0]]):
        assert _close(q, expected, 1e-12)
        assert sum(c * c for c in q) == pytest.approx(1.0, abs=1e-15)
    assert all(sum(a * b for a, b in zip(p, q)) >= 0 for p, q in zip(track, track[1:]))


def test_quaternion_matrix_round_trip_on_a_known_rotation():
    # Rz(90) in a column convention: q = (cos 45, 0, 0, sin 45), and rotate((1,0,0)) = (0,1,0)
    c45 = math.sqrt(0.5)
    q = ac.quat_from_matrix([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    assert _q_close(q, [c45, 0, 0, c45])
    assert _close(ac.quat_rotate(q, [1, 0, 0]), [0, 1, 0])


# ---------------------------------------------------------------------------------------------------------------
# Refusals: every one is `unsupported` with a reason, never a guess.
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('units, reason', [
    (UNITS.replace('length 0.45', 'length 1.0'), 'units'),
    (UNITS.replace('angle deg', 'angle rad'), 'units'),
    (':units\n  mass 1.0\n  angle deg\n', 'units'),
    ('', 'units'),
])
def test_unsupported_units_are_refused(units, reason):
    _refused(reason, ac.parse_asf, _asf(ROOT_PLAIN, [_bone(1, 'spine', '0 1 0', 1)], ['root spine'], units=units))


def _one_bone(**kw):
    return _asf(ROOT_PLAIN, [_bone(1, 'spine', '0 1 0', 1, **kw)], ['root spine'])


@pytest.mark.parametrize('text, reason', [
    (_one_bone(dof='rx l'), 'channel_l'),
    (_one_bone(dof='rx tx'), 'channel_translation'),
    (_one_bone(dof='rx rq'), 'syntax'),
    (_one_bone(dof='rx rx'), 'duplicate'),
    (_one_bone(axis='0 0 0 XXY'), 'syntax'),
    (_one_bone(axis='0 0 0'), 'syntax'),
    (_one_bone(dof='rx ry', limits=['(-10 10)']), 'syntax'),
    (_asf(ROOT_PLAIN, [_bone(1, 'spine', 'nan 1 0', 1)], ['root spine']), 'non_finite'),
    (_asf(ROOT_PLAIN, [_bone(1, 'spine', '0 1 0', 'inf')], ['root spine']), 'non_finite'),
    (_one_bone(dof='rx', limits=['(-inf inf)']), 'non_finite'),
    (_asf(ROOT_PLAIN, [_bone(1, 'spine', '0 2 0', 1)], ['root spine']), 'syntax'),
    (_asf(ROOT_PLAIN, [_bone(1, 'spine', '0 1 0', -1)], ['root spine']), 'syntax'),
    (_asf(['order TX TY TZ RX RY', 'axis XYZ', 'position 0 0 nan', 'orientation 0 0 0'],
          [_bone(1, 'spine', '0 1 0', 1)], ['root spine']), 'non_finite'),
    (_asf(['order TX TY TZ RX RX', 'axis XYZ', 'position 0 0 0', 'orientation 0 0 0'],
          [_bone(1, 'spine', '0 1 0', 1)], ['root spine']), 'duplicate'),
    (_asf(['order TX TY TZ RX RY L', 'axis XYZ', 'position 0 0 0', 'orientation 0 0 0'],
          [_bone(1, 'spine', '0 1 0', 1)], ['root spine']), 'channel_l'),
    (_asf(['order TX TY TZ RX RY RZ', 'axis XYZ', 'position 0 0 0'],
          [_bone(1, 'spine', '0 1 0', 1)], ['root spine']), 'syntax'),
    (_asf(['order TX TY TZ RX RY RZ', 'axis XYZ', 'position 0 0 0', 'position 1 0 0', 'orientation 0 0 0'],
          [_bone(1, 'spine', '0 1 0', 1)], ['root spine']), 'duplicate'),
    (_asf(ROOT_PLAIN, [_bone(1, 'spine', '0 1 0', 1)], ['root spine'],
          units=':units\n  length 1.0\n  length 0.45\n  angle deg\n'), 'duplicate'),
])
def test_malformed_or_unsupported_asf_is_refused(text, reason):
    _refused(reason, ac.parse_asf, text)


@pytest.mark.parametrize('bones, hierarchy', [
    ([_bone(1, 'spine', '0 1 0', 1)], None),                                            # no :hierarchy
    ([_bone(1, 'spine', '0 1 0', 1), _bone(2, 'neck', '0 1 0', 1)], ['root spine']),    # neck unreachable
    ([_bone(1, 'spine', '0 1 0', 1)], ['root spine', 'spine ghost']),                   # unknown child
    ([_bone(1, 'spine', '0 1 0', 1)], ['ghost spine']),                                 # unknown parent
    ([_bone(1, 'spine', '0 1 0', 1), _bone(2, 'neck', '0 1 0', 1)],
     ['root spine neck', 'spine neck']),                                                # two parents
    ([_bone(1, 'spine', '0 1 0', 1), _bone(2, 'neck', '0 1 0', 1)],
     ['root spine', 'spine neck', 'neck spine']),                                       # cycle
    ([_bone(1, 'spine', '0 1 0', 1)], ['root spine', 'spine root']),                    # root as a child
])
def test_broken_hierarchy_is_refused(bones, hierarchy):
    _refused('hierarchy', ac.parse_asf, _asf(ROOT_PLAIN, bones, hierarchy))


@pytest.mark.parametrize('bones', [
    [_bone(1, 'spine', '0 1 0', 1), _bone(2, 'spine', '0 1 0', 1)],                      # duplicate name
    [_bone(1, 'spine', '0 1 0', 1), _bone(1, 'neck', '0 1 0', 1)],                       # duplicate id
    [_bone(1, 'root', '0 1 0', 1)],                                                      # a bone named root
])
def test_duplicate_bones_are_refused(bones):
    _refused('duplicate', ac.parse_asf, _asf(ROOT_PLAIN, bones, ['root ' + ' '.join(b[1][5:] for b in bones)]))


SKEL_A = ac.parse_asf(ASF_A)


@pytest.mark.parametrize('amc_text, reason', [
    (_amc((1, ['root 0 0 0 0 0 0', 'b1 90 90', 'ghost 1']),), 'unknown_bone'),
    (_amc((1, ['root 0 0 0 0 0 0']),), 'missing_bone'),
    (_amc((1, ['b1 90 90']),), 'missing_bone'),
    (_amc((1, ['root 0 0 0 0 0 0', 'b1 90']),), 'channel_count'),
    (_amc((1, ['root 0 0 0 0 0 0', 'b1 90 90 0']),), 'channel_count'),
    (_amc((1, ['root 0 0 0 0 0', 'b1 90 90']),), 'channel_count'),
    (_amc((1, ['root 0 0 0 0 0 0', 'b1 90 nan']),), 'non_finite'),
    (_amc((1, ['root 0 0 inf 0 0 0', 'b1 90 90']),), 'non_finite'),
    (_amc((1, ['root 0 0 0 0 0 0', 'b1 90 x']),), 'syntax'),
    (_amc((1, ['root 0 0 0 0 0 0', 'b1 90 90', 'b1 90 90']),), 'duplicate'),
    (_amc((1, ['root 0 0 0 0 0 0', 'b1 90 90']), (3, ['root 0 0 0 0 0 0', 'b1 90 90'])), 'frames'),
    (_amc((2, ['root 0 0 0 0 0 0', 'b1 90 90']), (1, ['root 0 0 0 0 0 0', 'b1 90 90'])), 'frames'),
    (_amc(), 'frames'),
    (_amc((1, ['root 0 0 0 0 0 0', 'b1 90 90']), header=':FULLY-SPECIFIED\n:RADIANS\n'), 'units'),
    (_amc((1, ['root 0 0 0 0 0 0', 'b1 90 90']), header=':DEGREES\n'), 'directive'),
    (_amc((1, ['root 0 0 0 0 0 0', 'b1 90 90']), header=':FULLY-SPECIFIED\n:DEGREES\n:SOMETHING\n'), 'directive'),
    (':FULLY-SPECIFIED\n:DEGREES\nroot 0 0 0 0 0 0\n1\n', 'syntax'),
])
def test_malformed_or_unsupported_amc_is_refused(amc_text, reason):
    _refused(reason, ac.parse_amc, amc_text, SKEL_A)


def test_finite_input_that_overflows_once_scaled_is_refused():
    # 1e308 units is finite, but 1e308 * 5.64 cm is not: refused as non_finite, never written out as Infinity
    asf = _asf(['order TX TY TZ RX RY RZ', 'axis XYZ', 'position 1e308 0 0', 'orientation 0 0 0'],
               [_bone(1, 'spine', '0 1 0', 1)], ['root spine'])
    _refused('non_finite', _canon, asf, _amc((1, ['root 0 0 0 0 0 0'])))
    _refused('non_finite', ac.loads, '{"fps": NaN}')


def test_bad_fps_is_refused():
    _refused('time', _canon, ASF_A, AMC_A, fps=0)
    _refused('time', _canon, ASF_A, AMC_A, fps=float('nan'))
    _refused('time', ac.resample, _canon(ASF_T, AMC_T), -1)
