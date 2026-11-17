"""Acclaim ASF/AMC reader and the conversion to canonical tracks (schema 2) in Unreal's space.

    skel   = parse_asf(asf_text)                      # units, root, bones, hierarchy; refuses what it cannot take
    motion = parse_amc(amc_text, skel)                # frames, per-bone channels in that bone's dof order
    canon  = to_canonical(skel, motion, B, fps=120)   # cm, Z-up, left-handed; the stage-2 contract
    canon  = resample(canon, 30)                      # keeps a (fractional) source_frame for every sample
    world  = forward_kinematics(canon, i)             # {bone: (position_cm, rotation_matrix)} in UE component space

Design: docs/specs/2026-11-16-mocap-intake-design.md, sections 5 (data contracts) and 6 (convert and import).
Host Python, stdlib only. Every refusal raises AcclaimError, whose `state` is always 'unsupported' and whose `reason`
names what was refused; nothing is guessed, clamped or skipped.

Conventions, derived once here and pinned by Scripts/test_acclaim.py:

Vectors are COLUMN vectors: a rotation matrix R maps v to R v, and a chain composes parent-first,
world_child = world_parent . local_child. Column vectors were chosen because every other matrix in the contract (the
basis B, R_UE = B R B^-1, p_UE = s B p) is written that way in the spec, and a quaternion q made from R rotates
v as q v q* = R v (Hamilton product, [w, x, y, z]), so one convention covers the reader, the basis and the tracks.

Euler angles. An `axis a1 a2 a3 ORDER` line (and the root `orientation` with the root `axis` order) gives the x, y
and z angles as the three values, and ORDER the order they apply in, first letter first, about fixed axes:
'XYZ' means v' = Rz . Ry . Rx v. AMC channels apply in the order the bone's `dof` (the root's `order`) lists them:
for `dof d1 d2 d3`, M = R_d3 . R_d2 . R_d1 (compose_motion). Reading the values per letter instead of per axis is
indistinguishable on an 'XYZ' bone; the other reading is not supported by any opened format description (the spec,
section 6, notes the description has not been opened), so it is a pinned choice, not a verified fact.

The bone local transform. The format states it for ROW vectors as L = Cinv M C B (v' = v L; C the axis rotation,
M the motion, B the offset translation). With row matrices being the transposes of column ones, C_row = C^T and
Cinv_row = C, so v' = v C M_row C^T, and transposing, v'^T = C M C^-1 v^T. In this module's column convention:

    rotation of a bone in its parent's frame = C M C^-1            (compose_local)
    translation of a bone in its parent's frame = the parent's segment vector, direction * length

A segment's `direction` x `length` runs from its joint to its children's joint, so a bone's joint sits at its
parent's segment end; the root has no segment, so the root's children start at the root. At zero channels every
non-root bone's local rotation is the identity: ASF directions are given in the skeleton's frame at rest.

The root. Its static `position` is kept: p_root = position + T (T from the TX/TY/TZ channels) and R_root = M_root
(RX/RY/RZ in `order`). A nonzero static `orientation` is REFUSED (reason 'root_orientation'): whether it composes as
O M or as O M O^-1 is unverified, and CMU roots carry `orientation 0 0 0`. The code path keeps O (identity once
refused otherwise) so that lifting the refusal is one decision, made against the pinned viewer.

Canonical. p_UE = s B p and R_UE = B R B^-1, with s = CM_PER_UNIT applied exactly once, to segment offsets and to
the root translation (static plus animated), never to a rotation. B must be a signed permutation; det(B) must be -1
(a right-handed source into Unreal's left-handed frame) unless allow_any_handedness, which exists only so a test can
show what a mirrored basis does. `local_q` is a bone's full local rotation in its parent's frame (equal to
`rest_rotation` at zero channels), not a delta from rest; the root's translation track is `pelvis_t`.
Time: t_s = (source_frame - first) / fps; N samples last (N - 1) / fps.
"""
import hashlib
import json
import math
import os

CM_PER_UNIT = 2.54 / 0.45          # (1 / 0.45) * 2.54 / 100 m per ASF unit at `length 0.45`, in cm
SUPPORTED_LENGTH = 0.45
SCHEMA = 2
SPACE = {'handedness': 'left', 'up': '+Z', 'forward': '+X', 'right': '+Y'}
ROTATION_CHANNELS = ('rx', 'ry', 'rz')
TRANSLATION_CHANNELS = ('tx', 'ty', 'tz')
IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_UNIT_DIRECTION_TOL = 1e-3


class AcclaimError(ValueError):
    """A refused input. `state` is the decision state it maps to; `reason` names the refusal."""
    state = 'unsupported'

    def __init__(self, reason, message):
        super().__init__('%s: %s' % (reason, message))
        self.reason = reason


# ------------------------------------------------------------------------------------------------ small linear algebra
def mat_mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def mat_vec(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def transpose(m):
    return tuple(tuple(m[j][i] for j in range(3)) for i in range(3))


def _det(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _scale(v, k):
    return tuple(x * k for x in v)


def axis_rotation(axis, degrees):
    """The column-vector rotation by `degrees` about source axis 'x', 'y' or 'z' (right-handed)."""
    r = math.radians(degrees)
    c, s = math.cos(r), math.sin(r)
    if axis == 'x':
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if axis == 'y':
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def euler(angles_xyz, order):
    """A static Euler rotation: x, y, z angles, applied in `order`, first letter first (see the header)."""
    m = IDENTITY
    for letter in order.lower():
        m = mat_mul(axis_rotation(letter, angles_xyz['xyz'.index(letter)]), m)
    return m


def compose_motion(rotations):
    """AMC channel rotations, listed in the order they apply (the dof order), into one matrix: R_n ... R_2 R_1."""
    m = IDENTITY
    for r in rotations:
        m = mat_mul(r, m)
    return m


def compose_local(c, m):
    """A bone's rotation in its parent's frame: C M C^-1, the column form of the row-vector Cinv M C (header)."""
    return mat_mul(mat_mul(c, m), transpose(c))


# ------------------------------------------------------------------------------------------------------- quaternions
def quat_from_matrix(m):
    """[w, x, y, z], unit length, such that quat_rotate(q, v) == m v."""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        k = 2.0 * math.sqrt(1.0 + t)
        q = (0.25 * k, (m[2][1] - m[1][2]) / k, (m[0][2] - m[2][0]) / k, (m[1][0] - m[0][1]) / k)
    elif m[0][0] >= m[1][1] and m[0][0] >= m[2][2]:
        k = 2.0 * math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2])
        q = ((m[2][1] - m[1][2]) / k, 0.25 * k, (m[0][1] + m[1][0]) / k, (m[0][2] + m[2][0]) / k)
    elif m[1][1] >= m[2][2]:
        k = 2.0 * math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2])
        q = ((m[0][2] - m[2][0]) / k, (m[0][1] + m[1][0]) / k, 0.25 * k, (m[1][2] + m[2][1]) / k)
    else:
        k = 2.0 * math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1])
        q = ((m[1][0] - m[0][1]) / k, (m[0][2] + m[2][0]) / k, (m[1][2] + m[2][1]) / k, 0.25 * k)
    return _normalise(q)


def _normalise(q):
    n = math.sqrt(sum(c * c for c in q))
    if not math.isfinite(n) or n == 0:
        raise AcclaimError('non_finite', 'a quaternion of length %r' % n)
    return [c / n for c in q]


def matrix_from_quat(q):
    w, x, y, z = _normalise(q)
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
            (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
            (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)))


def quat_rotate(q, v):
    return mat_vec(matrix_from_quat(q), v)


def _sign_continuous(track):
    """Each quaternion takes the sign nearer its predecessor; the first takes w >= 0 (first nonzero part positive)."""
    out = []
    for q in track:
        q = list(q)
        if out:
            if sum(a * b for a, b in zip(out[-1], q)) < 0:
                q = [-c for c in q]
        else:
            lead = next((c for c in q if abs(c) > 1e-12), 1.0)
            if lead < 0:
                q = [-c for c in q]
        out.append(q)
    return out


def _slerp(a, b, u):
    d = sum(x * y for x, y in zip(a, b))
    if d < 0:
        b, d = [-c for c in b], -d
    if d > 1 - 1e-12:
        return _normalise([x + (y - x) * u for x, y in zip(a, b)])
    theta = math.acos(min(1.0, d))
    sa, sb = math.sin((1 - u) * theta) / math.sin(theta), math.sin(u * theta) / math.sin(theta)
    return _normalise([sa * x + sb * y for x, y in zip(a, b)])


# ----------------------------------------------------------------------------------------------------------- parsing
def _lines(text):
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if line:
            yield number, line


def _num(token, where):
    try:
        value = float(token)
    except ValueError:
        raise AcclaimError('syntax', '%s: %r is not a number' % (where, token)) from None
    if not math.isfinite(value):
        raise AcclaimError('non_finite', '%s: %r' % (where, token))
    return value


def _nums(tokens, count, where):
    if len(tokens) != count:
        raise AcclaimError('syntax', '%s: expected %d numbers, got %d' % (where, count, len(tokens)))
    return [_num(t, where) for t in tokens]


def _order(token, where):
    order = token.upper()
    if sorted(order) != ['X', 'Y', 'Z']:
        raise AcclaimError('syntax', '%s: %r is not an Euler order of X, Y, Z' % (where, token))
    return order


def _channels(tokens, where, root):
    seen = []
    for token in tokens:
        ch = token.lower()
        if ch == 'l':
            raise AcclaimError('channel_l', '%s: length channels are not supported' % where)
        if ch in TRANSLATION_CHANNELS and not root:
            raise AcclaimError('channel_translation', '%s: translation channels on a bone are not supported' % where)
        if ch not in ROTATION_CHANNELS + TRANSLATION_CHANNELS:
            raise AcclaimError('syntax', '%s: unknown channel %r' % (where, token))
        if ch in seen:
            raise AcclaimError('duplicate', '%s: channel %r listed twice' % (where, token))
        seen.append(ch)
    return seen


def _limits(tokens, where):
    flat = ' '.join(tokens).replace('(', ' ').replace(')', ' ').split()
    if len(flat) % 2:
        raise AcclaimError('syntax', '%s: limits are not pairs' % where)
    values = [_num(t, where) for t in flat]
    return [values[i:i + 2] for i in range(0, len(values), 2)]


def parse_asf(text):
    """The skeleton: {'units', 'root', 'bones': {name: bone}, 'parent': {bone: parent}, 'children', 'order'}."""
    units, root, bones, ids, hierarchy = {}, {}, {}, set(), []
    section, bone, last_key, seen = None, None, None, set()
    header = {}
    for n, line in _lines(text):
        where = 'ASF line %d' % n
        tokens = line.split()
        if line.startswith(':'):
            keyword = tokens[0].lower()
            if keyword in seen:
                raise AcclaimError('syntax', '%s: %s appears twice' % (where, keyword))
            seen.add(keyword)
            if bone is not None:
                raise AcclaimError('syntax', '%s: a bone block is not closed by end' % where)
            if keyword in (':version', ':name'):
                header[keyword[1:]] = ' '.join(tokens[1:])
                section = None
            elif keyword in (':units', ':documentation', ':root', ':bonedata', ':hierarchy'):
                section = keyword[1:]
            else:
                raise AcclaimError('syntax', '%s: unknown keyword %s' % (where, keyword))
            continue
        key = tokens[0].lower()
        if section == 'documentation':
            continue
        if section in ('units', 'root') and key in (units if section == 'units' else root):
            raise AcclaimError('duplicate', '%s: %s %s given twice' % (where, section, key))
        if section == 'units':
            if len(tokens) != 2:
                raise AcclaimError('syntax', '%s: units line %r' % (where, line))
            if key in ('mass', 'length'):
                units[key] = _num(tokens[1], where)
            elif key == 'angle':
                units[key] = tokens[1].lower()
            else:
                raise AcclaimError('syntax', '%s: unknown unit %r' % (where, key))
        elif section == 'root':
            if key == 'order':
                root['order'] = _channels(tokens[1:], where + ' root order', root=True)
            elif key == 'axis':
                if len(tokens) != 2:
                    raise AcclaimError('syntax', '%s: root axis takes one Euler order' % where)
                root['axis'] = _order(tokens[1], where)
            elif key in ('position', 'orientation'):
                root[key] = _nums(tokens[1:], 3, where + ' root ' + key)
            else:
                raise AcclaimError('syntax', '%s: unknown root key %r' % (where, key))
        elif section == 'bonedata':
            if key == 'begin':
                if bone is not None:
                    raise AcclaimError('syntax', '%s: begin inside a bone block' % where)
                bone, last_key = {}, None
            elif key == 'end':
                if bone is None:
                    raise AcclaimError('syntax', '%s: end without begin' % where)
                _finish_bone(bone, bones, ids, where)
                bone = None
            elif bone is None:
                raise AcclaimError('syntax', '%s: bone data outside begin/end' % where)
            elif line.startswith('(') and last_key == 'limits':
                bone['limits'].extend(_limits(tokens, where))
            else:
                _bone_line(bone, key, tokens, where)
                last_key = key
        elif section == 'hierarchy':
            if key in ('begin', 'end'):
                continue
            hierarchy.append((n, tokens))
        else:
            raise AcclaimError('syntax', '%s: data outside a section' % where)
    if bone is not None:
        raise AcclaimError('syntax', 'ASF: the last bone block is not closed by end')
    _check_units(units)
    for key in ('order', 'axis', 'position', 'orientation'):
        if key not in root:
            raise AcclaimError('syntax', 'ASF :root has no %s' % key)
    _check_root_orientation(root)
    if ':hierarchy' not in seen:
        raise AcclaimError('hierarchy', 'ASF has no :hierarchy')
    parent, children, order = _hierarchy(hierarchy, bones)
    return {'version': header.get('version'), 'name': header.get('name'), 'units': units, 'root': root,
            'bones': bones, 'parent': parent, 'children': children, 'order': order}


def _bone_line(bone, key, tokens, where):
    if key in bone and key != 'limits':
        raise AcclaimError('syntax', '%s: %s given twice in one bone' % (where, key))
    if key == 'id':
        if len(tokens) != 2:
            raise AcclaimError('syntax', '%s: id takes one value' % where)
        value = _num(tokens[1], where)
        if not value.is_integer():
            raise AcclaimError('syntax', '%s: id %r is not an integer' % (where, tokens[1]))
        bone['id'] = int(value)
    elif key == 'name':
        if len(tokens) != 2:
            raise AcclaimError('syntax', '%s: name takes one value' % where)
        bone['name'] = tokens[1]
    elif key == 'direction':
        bone['direction'] = _nums(tokens[1:], 3, where + ' direction')
    elif key == 'length':
        bone['length'] = _nums(tokens[1:], 1, where + ' length')[0]
    elif key == 'axis':
        if len(tokens) != 5:
            raise AcclaimError('syntax', '%s: axis takes three angles and an Euler order' % where)
        bone['axis'] = _nums(tokens[1:4], 3, where + ' axis')
        bone['axis_order'] = _order(tokens[4], where)
    elif key == 'dof':
        bone['dof'] = _channels(tokens[1:], where + ' dof', root=False)
    elif key == 'limits':
        bone.setdefault('limits', []).extend(_limits(tokens[1:], where))
    elif key in ('bodymass', 'cofmass'):
        _nums(tokens[1:], 1, where + ' ' + key)
    else:
        raise AcclaimError('syntax', '%s: unknown bone key %r' % (where, key))


def _finish_bone(bone, bones, ids, where):
    for key in ('id', 'name', 'direction', 'length', 'axis'):
        if key not in bone:
            raise AcclaimError('syntax', '%s: bone %s has no %s' % (where, bone.get('name', '?'), key))
    name = bone['name']
    if name == 'root' or name in bones:
        raise AcclaimError('duplicate', '%s: bone name %r is already taken' % (where, name))
    if bone['id'] in ids:
        raise AcclaimError('duplicate', '%s: bone id %d is already taken' % (where, bone['id']))
    bone.setdefault('dof', [])
    bone.setdefault('limits', [])
    if bone['limits'] and len(bone['limits']) != len(bone['dof']):
        raise AcclaimError('syntax', '%s: bone %s has %d limits for %d dofs'
                           % (where, name, len(bone['limits']), len(bone['dof'])))
    if bone['length'] < 0:
        raise AcclaimError('syntax', '%s: bone %s has a negative length' % (where, name))
    norm = math.sqrt(sum(c * c for c in bone['direction']))
    if abs(norm - 1.0) > _UNIT_DIRECTION_TOL:
        raise AcclaimError('syntax', '%s: bone %s direction is not a unit vector (|d| = %g)' % (where, name, norm))
    bone['direction'] = [c / norm for c in bone['direction']]
    ids.add(bone['id'])
    bones[name] = {k: bone[k] for k in ('id', 'name', 'direction', 'length', 'axis', 'axis_order', 'dof', 'limits')}


def _check_units(units):
    length, angle = units.get('length'), units.get('angle')
    if length is None or abs(length - SUPPORTED_LENGTH) > 1e-12:
        raise AcclaimError('units', 'only `length %s` is supported, got %r' % (SUPPORTED_LENGTH, length))
    if angle != 'deg':
        raise AcclaimError('units', 'only `angle deg` is supported, got %r' % angle)


def _hierarchy(lines, bones):
    parent, children = {}, {'root': []}
    for name in bones:
        children[name] = []
    for n, tokens in lines:
        where = 'ASF hierarchy line %d' % n
        if len(tokens) < 2:
            raise AcclaimError('hierarchy', '%s: a parent with no children' % where)
        head = tokens[0]
        if head not in children:
            raise AcclaimError('hierarchy', '%s: unknown parent %r' % (where, head))
        for child in tokens[1:]:
            if child == 'root':
                raise AcclaimError('hierarchy', '%s: root cannot be a child' % where)
            if child not in bones:
                raise AcclaimError('hierarchy', '%s: unknown bone %r' % (where, child))
            if child in parent:
                raise AcclaimError('hierarchy', '%s: %r already has parent %r' % (where, child, parent[child]))
            parent[child] = head
            children[head].append(child)
    order, stack = [], ['root']
    while stack:
        name = stack.pop()
        order.append(name)
        stack.extend(reversed(children[name]))
    missing = [b for b in bones if b not in order]
    if missing:
        raise AcclaimError('hierarchy', 'bones not reachable from root: %s' % ', '.join(missing))
    return parent, children, order


def _channel_names(skel, name):
    return skel['root']['order'] if name == 'root' else skel['bones'][name]['dof']


def parse_amc(text, skel):
    """The motion: {'directives': {'fully_specified', 'angle'}, 'frames': [{'frame': n, 'channels': {bone: {ch: v}}}]}.
    Channels are keyed in the bone's dof order (the root's `order`)."""
    fully, angle, frames, current = False, skel['units']['angle'], [], None
    for n, line in _lines(text):
        where = 'AMC line %d' % n
        tokens = line.split()
        if line.startswith(':'):
            if frames:
                raise AcclaimError('syntax', '%s: directive %s after the first frame' % (where, tokens[0]))
            directive = tokens[0].upper()
            if directive == ':FULLY-SPECIFIED':
                fully = True
            elif directive == ':DEGREES':
                angle = 'deg'
            elif directive == ':RADIANS':
                raise AcclaimError('units', '%s: only degrees are supported (the ASF says `angle deg`)' % where)
            else:
                raise AcclaimError('directive', '%s: unknown directive %s' % (where, tokens[0]))
            continue
        if len(tokens) == 1:
            if not tokens[0].isdigit():
                raise AcclaimError('syntax', '%s: %r is not a frame number' % (where, tokens[0]))
            current = {'frame': int(tokens[0]), 'channels': {}}
            frames.append(current)
            continue
        if current is None:
            raise AcclaimError('syntax', '%s: channel data before the first frame number' % where)
        name, values = tokens[0], tokens[1:]
        if name != 'root' and name not in skel['bones']:
            raise AcclaimError('unknown_bone', '%s: %r is not in the ASF' % (where, name))
        if name in current['channels']:
            raise AcclaimError('duplicate', '%s: %r given twice in frame %d' % (where, name, current['frame']))
        names = _channel_names(skel, name)
        if len(values) != len(names):
            raise AcclaimError('channel_count', '%s: %r has %d values for %d channels %s'
                               % (where, name, len(values), len(names), names))
        current['channels'][name] = {ch: _num(v, '%s %s.%s' % (where, name, ch)) for ch, v in zip(names, values)}
    if not fully:
        raise AcclaimError('directive', 'AMC is not :FULLY-SPECIFIED; partial specification is not supported')
    if angle != 'deg':
        raise AcclaimError('units', 'only degrees are supported')
    if not frames:
        raise AcclaimError('frames', 'AMC has no frames')
    for a, b in zip(frames, frames[1:]):
        if b['frame'] != a['frame'] + 1:
            raise AcclaimError('frames', 'frame %d follows frame %d; frames must be consecutive'
                               % (b['frame'], a['frame']))
    required = [name for name in skel['order'] if _channel_names(skel, name)]
    for frame in frames:
        missing = [name for name in required if name not in frame['channels']]
        if missing:
            raise AcclaimError('missing_bone', 'frame %d has no channels for %s' % (frame['frame'], ', '.join(missing)))
    return {'directives': {'fully_specified': fully, 'angle': angle}, 'frames': frames}


# ----------------------------------------------------------------------------------------------- source-space posing
def _motion_rotation(channels):
    return compose_motion([axis_rotation(ch[1], v) for ch, v in channels.items() if ch in ROTATION_CHANNELS])


def _segment(skel, name):
    if name == 'root':
        return (0.0, 0.0, 0.0)
    bone = skel['bones'][name]
    return tuple(c * bone['length'] for c in bone['direction'])


def _check_root_orientation(root):
    if any(abs(c) > 0 for c in root['orientation']):
        raise AcclaimError('root_orientation', 'a nonzero root orientation %r is not supported: whether it composes as '
                           'O M or O M O^-1 is unverified (no format description or viewer has pinned it)'
                           % (root['orientation'],))


def _root_static(skel):
    root = skel['root']
    return tuple(root['position']), euler(root['orientation'], root['axis'])


def source_locals(skel, motion):
    """Per AMC frame, {bone: (rotation, translation)} in the parent's frame, source axes and ASF units."""
    position, o = _root_static(skel)
    statics = {name: euler(bone['axis'], bone['axis_order']) for name, bone in skel['bones'].items()}
    out = []
    for frame in motion['frames']:
        local = {}
        ch = frame['channels'].get('root', {})
        t = tuple(ch.get(c, 0.0) for c in TRANSLATION_CHANNELS)
        local['root'] = (mat_mul(o, _motion_rotation(ch)), _add(position, mat_vec(o, t)))
        for name in skel['order'][1:]:
            m = _motion_rotation(frame['channels'].get(name, {}))
            local[name] = (compose_local(statics[name], m), _segment(skel, skel['parent'][name]))
        out.append(local)
    return out


def source_world(skel, local):
    """{bone: (joint position, world rotation)} for one frame of source_locals, source axes and ASF units."""
    world = {'root': (local['root'][1], local['root'][0])}
    for name in skel['order'][1:]:
        ppos, prot = world[skel['parent'][name]]
        rot, offset = local[name]
        world[name] = (_add(ppos, mat_vec(prot, offset)), mat_mul(prot, rot))
    return world


# ------------------------------------------------------------------------------------------------------- the basis
def check_basis(b, allow_any_handedness=False):
    """B as a tuple matrix with its determinant; refuses anything but a signed permutation (det -1 unless allowed)."""
    try:
        m = tuple(tuple(float(c) for c in row) for row in b)
    except (TypeError, ValueError):
        raise AcclaimError('basis', 'B is not a 3x3 matrix of numbers') from None
    if len(m) != 3 or any(len(row) != 3 for row in m) or any(c not in (-1.0, 0.0, 1.0) for row in m for c in row):
        raise AcclaimError('basis', 'B must be a 3x3 signed permutation, got %r' % (b,))
    if any(sum(abs(c) for c in row) != 1 for row in m) or any(sum(abs(row[j]) for row in m) != 1 for j in range(3)):
        raise AcclaimError('basis', 'B must be a 3x3 signed permutation, got %r' % (b,))
    det = int(round(_det(m)))
    if det != -1 and not allow_any_handedness:
        raise AcclaimError('basis', 'det(B) is %d; a right-handed source into a left-handed frame needs -1' % det)
    return m, det


def _check_fps(fps):
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise AcclaimError('time', 'fps must be a positive finite number, got %r' % (fps,))


def _converter_digest():
    with open(os.path.abspath(__file__), 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def to_canonical(skel, motion, basis, *, fps, fps_provenance='unverified', source_frames=None, source_sha256=None,
                 allow_any_handedness=False):
    """Canonical tracks (schema 2, docs/specs/2026-11-16-mocap-intake-design.md section 5) for the AMC frames, or the
    inclusive `source_frames` [a, b] trim of them."""
    b, det = check_basis(basis, allow_any_handedness)
    _check_fps(fps)
    frames = motion['frames']
    if source_frames is not None:
        first, last = frames[0]['frame'], frames[-1]['frame']
        lo, hi = source_frames
        if not (isinstance(lo, int) and isinstance(hi, int) and first <= lo <= hi <= last):
            raise AcclaimError('time', 'source_frames %r outside the take [%d, %d]' % (source_frames, first, last))
        frames = [f for f in frames if lo <= f['frame'] <= hi]
    s = CM_PER_UNIT
    bt = transpose(b)

    def pos(p):
        return [s * c for c in mat_vec(b, p)]

    def rot(r):
        return quat_from_matrix(mat_mul(mat_mul(b, r), bt))

    position, o = _root_static(skel)
    bones = [{'name': 'root', 'parent': None, 'rest_offset': pos(position), 'rest_rotation': rot(o)}]
    for name in skel['order'][1:]:
        parent = skel['parent'][name]
        bones.append({'name': name, 'parent': parent, 'rest_offset': pos(_segment(skel, parent)),
                      'rest_rotation': rot(IDENTITY)})
    locals_ = source_locals(skel, {'frames': frames})
    numbers = [f['frame'] for f in frames]
    canon = {
        'schema': SCHEMA, 'units': 'cm', 'space': dict(SPACE),
        'basis': {'B': [[int(c) for c in row] for row in b], 'det': det, 'scale_cm_per_unit': s},
        'fps': fps, 'fps_provenance': fps_provenance,
        'bones': bones,
        'frames': {'source_frame': numbers,
                   't_s': [(n - numbers[0]) / fps for n in numbers],
                   'pelvis_t': [pos(local['root'][1]) for local in locals_],
                   'local_q': {name: _sign_continuous([rot(local[name][0]) for local in locals_])
                               for name in skel['order']}},
        'provenance': {'source_sha256': dict(source_sha256 or {}), 'converter': _converter_digest(),
                       'asf_units': {'length': skel['units']['length'], 'angle': skel['units']['angle']}},
    }
    _check_finite(canon, 'canonical')      # finite inputs can still overflow once scaled
    return canon


# ------------------------------------------------------------------------------------------------- canonical helpers
def duration_s(canon):
    """From the first sample to the last: (N - 1) / fps on a uniform take; a resampled take whose duration is not a
    multiple of its step ends on a flagged end sample, so the time of the last sample is used, not the count."""
    t = canon['frames']['t_s']
    return t[-1] - t[0]


def forward_kinematics(canon, index):
    """{bone: (joint position cm, world rotation matrix)} in UE component space for sample `index`."""
    frames, world = canon['frames'], {}
    for bone in canon['bones']:
        q = frames['local_q'][bone['name']][index]
        if bone['parent'] is None:
            world[bone['name']] = (tuple(frames['pelvis_t'][index]), matrix_from_quat(q))
        else:
            ppos, prot = world[bone['parent']]
            world[bone['name']] = (_add(ppos, mat_vec(prot, bone['rest_offset'])), mat_mul(prot, matrix_from_quat(q)))
    return world


def resample(canon, fps_out):
    """The take at `fps_out` over the same duration: translations lerped, rotations slerped, and every output sample
    keeps its (possibly fractional) source_frame. Output samples sit on t = k / fps_out; when the duration is not a
    multiple of 1 / fps_out, one more sample at the source end time keeps the last source sample, and
    frames['end_sample'] flags it (True only there), since it is off the grid. Nothing past the end is invented."""
    _check_fps(fps_out)
    frames = canon['frames']
    t_in = frames['t_s']
    end = duration_s(canon)
    n_grid = int(math.floor(end * fps_out + 1e-9)) + 1
    times = [k / fps_out for k in range(n_grid)]
    flags = [False] * n_grid
    if end - times[-1] > 1e-9:
        times.append(t_in[-1] - t_in[0])
        flags.append(True)
    source, pelvis, local_q = [], [], {name: [] for name in frames['local_q']}
    for t in times:
        j = 0
        while j + 2 < len(t_in) and t_in[j + 1] - t_in[0] <= t + 1e-12:
            j += 1
        nxt = min(j + 1, len(t_in) - 1)
        span = t_in[nxt] - t_in[j]
        u = min(max((t - (t_in[j] - t_in[0])) / span, 0.0), 1.0) if span > 0 else 0.0
        source.append(frames['source_frame'][j] + (frames['source_frame'][nxt] - frames['source_frame'][j]) * u)
        pelvis.append([a + (b - a) * u for a, b in zip(frames['pelvis_t'][j], frames['pelvis_t'][nxt])])
        for name, track in frames['local_q'].items():
            local_q[name].append(_slerp(track[j], track[nxt], u))
    out = json.loads(json.dumps(canon))
    fps_in = canon['fps']
    out['fps'] = fps_out
    out['frames'] = {'source_frame': source, 't_s': times, 'end_sample': flags, 'pelvis_t': pelvis,
                     'local_q': {name: _sign_continuous(track) for name, track in local_q.items()}}
    out['provenance']['resampled_from_fps'] = fps_in
    _check_finite(out, 'resampled')
    return out


def dumps(canon):
    """The contract's serialisation: JSON, sorted keys; refuses any non-finite number."""
    return json.dumps(canon, sort_keys=True, allow_nan=False)


def _refuse_constant(token):
    raise AcclaimError('non_finite', 'canonical JSON holds %s' % token)


def loads(text):
    """The inverse of dumps; refuses NaN and Infinity as dumps does."""
    return json.loads(text, parse_constant=_refuse_constant)


def _check_finite(value, where):
    if isinstance(value, dict):
        for key, item in value.items():
            _check_finite(item, '%s.%s' % (where, key))
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check_finite(item, '%s[%d]' % (where, i))
    elif isinstance(value, float) and not math.isfinite(value):
        raise AcclaimError('non_finite', '%s is %r after conversion' % (where, value))
