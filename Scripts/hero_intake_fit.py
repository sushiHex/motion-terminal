# Collider autofit, the measurement: runs INSIDE the editor (unreal_mcp run_file). Measures the mesh around the
# bones collider_autofit.fit_specs names, so the dynamics colliders are DERIVED from the rig ("fitted
# capsules: weight cutoff, trimmed-percentile radius, accessory exclusion, margin"), never hand-set.
#
#     client.run_file('.../Scripts/hero_intake_fit.py', HIF_MESH='/Game/...', HIF_SPECS={...}, HIF_OUT='<abs path>.json')
#
# HIF_SPECS:
#   'segments': [{'name', 'bone', 'end', 'region': [bones], 'split_axis': [x, y, z] | None}]
#       a capsule along bone -> end over the vertices whose weight summed over `region` reaches DOMINANT; with a
#       split axis (the torso's left-right) it also fits the two halves separately, each about its own offset axis,
#       so a wide, flat cross-section is not forced into one fat cylinder
#   'blobs':    [{'name', 'bone', 'region'}]        a sphere around the region's vertex centroid (a head)
#   'chains':   [{'root', 'bones'}]                 the cloth's half-width around its root -> tip line, and its TAILS:
#       per leaf bone, how far the mesh it carries hangs past it along the last segment (the fringe and feather below
#       a strip's last bone), with the end point in the leaf's local space
#   'ref_bones': [bones]                            reference-pose component-space positions and transforms
# Read-only: copies the LOD0 mesh into a transient DynamicMesh; no asset is edited.
import importlib
import json
import math
import os
import sys

import unreal

# exec'd in the editor (no __file__): the pure helpers are found through the project's Scripts folder.
_SCRIPTS = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()), 'Scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
importlib.reload(importlib.import_module('dyn_behaviours'))
_af = importlib.reload(importlib.import_module('collider_autofit'))

DOMINANT = 0.5          # a vertex belongs to a region when the region's bones carry at least this much of its weight
RADIUS_PCT = 90         # radius = this percentile of radial distance (trims straps, pouches, outliers)
WIDTH_PCT = 80          # cloth half-width = this percentile of distance from the chain line
SPAN_TRIM = 0.1         # radial samples only from the middle of a segment (t in [trim, 1 - trim])
TAIL_PCT = 95           # tail length = this percentile of how far the leaf's vertices reach past the leaf bone
LEAF_SHARE = 0.25       # a vertex counts toward a leaf's tail when the leaf carries at least this much of its weight
# The tail length is the percentile of the vertices that DO reach past the leaf: a conditional percentile, so it needs a
# real distal population. Fewer than this share of the leaf's vertices past it (and at least TAIL_MIN_SAMPLES) is no
# tail: one stray vertex 100 cm out must not become a 100 cm tail (review 2026-10-27, reproduced).
TAIL_MIN_SHARE = 0.05
TAIL_MIN_SAMPLES = 5
# A flap's FREE geometry (collider_autofit.flap_vertex_sets): vertices only the flap and its own descendants move. A
# pendant welded to a cord that is painted into the chest (a necklace, 2026-10-28;
# research/ue58-rigging-tools-assessment.md, 'Behaviours and the necklace') dominates 872 vertices but only 652 are free: the rest are cord around the neck, and taking them
# in pulled the tip to 3.1 cm against a 10.5 cm hang. Measured over that rig's 37 flaps: every feather 100% free, the
# pendant 75%, the necklace's side pieces 0% (painted on).


def _descends(bone, ancestor, parent):
    """True when `ancestor` is above `bone` in the hierarchy."""
    b = parent.get(bone)
    while b is not None:
        if b == ancestor:
            return True
        b = parent.get(b)
    return False


def _pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))]


def _seg(p, a, b):
    """(t along a->b clamped to [0, 1], distance to the segment, the closest point)."""
    t, q = _af.seg_closest(p, a, b)
    return t, math.dist(p, q), q


def _r(v):
    return round(v, 2) if v is not None else None


def fit(mesh_path, specs, out_path):
    mesh = unreal.load_asset(mesh_path)
    dm = unreal.DynamicMesh()
    unreal.GeometryScript_AssetUtils.copy_mesh_from_skeletal_mesh(
        mesh, dm, unreal.GeometryScriptCopyMeshFromAssetOptions(), unreal.GeometryScriptMeshReadLOD())
    # Geometry Script UFUNCTIONs return (target mesh, out params...) to Python.
    bones = unreal.GeometryScript_BoneWeights.get_all_bones_info(dm)[1]
    names = {b.index: str(b.name) for b in bones}
    parent = {str(b.name): names.get(b.parent_index) for b in bones}
    pos_ref, tm_ref, xf_ref = {}, {}, {}
    for b in bones:
        xf_ref[str(b.name)] = b.world_transform
        # The MESH's reference pose in component space (WorldTransform accumulates parents,
        # MeshBoneWeightFunctions.cpp:1342).
        t, q = b.world_transform.translation, b.world_transform.rotation
        pos_ref[str(b.name)] = [t.x, t.y, t.z]
        tm_ref[str(b.name)] = {'translation': [round(t.x, 4), round(t.y, 4), round(t.z, 4)],
                               'rotation_xyzw': [round(q.x, 6), round(q.y, 6), round(q.z, 6), round(q.w, 6)]}
    vlist = unreal.GeometryScript_MeshQueries.get_all_vertex_positions(dm, False)[1]
    positions = unreal.GeometryScript_List.convert_vector_list_to_array(vlist)   # the list's payload is not exposed
    vweights = []          # per vertex {bone: weight}; bSkipGaps False keeps list index == vertex ID
    for vid in range(len(positions)):
        weights, valid = unreal.GeometryScript_BoneWeights.get_vertex_bone_weights(dm, vid)[1:3]
        vweights.append({names.get(w.bone_index): w.weight for w in weights} if valid else {})
    pts = [[p.x, p.y, p.z] for p in positions]

    def members(region):
        """Vertices whose weight summed over `region` reaches DOMINANT: a vertex split 0.4/0.35 between a bone and
        its own twist helper belongs to it (review 2026-10-26; a single-bone argmax dropped it)."""
        return [p for p, w in zip(pts, vweights) if sum(w.get(b, 0.0) for b in region) >= DOMINANT]

    def skin_points(region):
        """The body's own surface the region carries (no cloth section), reference pose, component space."""
        got = json.loads(unreal.HeroDynamicsTools.skin_points(mesh, [unreal.Name(b) for b in region], DOMINANT))
        if not got.get('ok'):
            raise RuntimeError('HIF skin points: %s' % got.get('error'))
        xyz = got['xyz']
        return [xyz[i:i + 3] for i in range(0, len(xyz), 3)]

    def garment_points():
        """The cloth's movable particles at rest, each with its asset's collision thickness ([(xyz, cm)]; empty without
        cloth): what the skin capsules must leave clear (collider_autofit.garment_clear)."""
        got = json.loads(unreal.HeroDynamicsTools.garment_points(mesh))
        if not got.get('ok'):
            raise RuntimeError('HIF garment points: %s' % got.get('error'))
        # each with its asset's thickness: the most a particle may sit inside a collider (garment_clear's error budget)
        return [(a['xyz'][3 * i:3 * i + 3], a['thickness_cm']) for a in got['assets']
                for i, m in enumerate(a['max_distance']) if m > 0.0]

    garment = garment_points()
    out = {'mesh': mesh_path, 'vertices': len(pts), 'segments': {}, 'blobs': {}, 'chains': {},
           'ref_cs': {b: [round(v, 3) for v in pos_ref[b]] for b in specs.get('ref_bones', []) if b in pos_ref},
           'ref_tm': {b: tm_ref[b] for b in specs.get('ref_bones', []) if b in tm_ref}}
    for spec in specs.get('segments', []):
        a, b = pos_ref[spec['bone']], pos_ref[spec['end']]
        mid = [(x + y) / 2.0 for x, y in zip(a, b)]
        radial, halves = [], {-1: [], 1: []}
        axis = spec.get('split_axis')
        mine = members(set(spec['region']))
        for p in mine:
            t, r, q = _seg(p, a, b)
            if SPAN_TRIM <= t <= 1.0 - SPAN_TRIM:
                radial.append(r)
                if axis:
                    halves[1 if _af.dot(_af.sub(p, q), axis) >= 0.0 else -1].append(p)
        entry = {'bone': spec['bone'], 'end': spec['end'], 'samples': len(radial),
                 'length_cm': round(math.dist(a, b), 2), 'radius_cm': _r(_pct(radial, RADIUS_PCT)),
                 'radius_p50_cm': _r(_pct(radial, 50)),
                 # the capsule covering the body's own surface there, for what a garment rests on
                 # (collider_autofit.skin_capsule): never the cloth's vertices (UHeroDynamicsTools.SkinPoints)
                 'skin': _af.skin_capsule(skin_points(spec['region']), a, b, garment=garment)}
        if axis:
            entry['halves'] = {}
            for sign, hp in halves.items():
                if not hp:
                    continue
                # The half's own axis: the segment shifted along the split axis to the half's mean lateral offset.
                off = sum(_af.dot(_af.sub(p, _seg(p, a, b)[2]), axis) for p in hp) / len(hp)
                sa = [x + off * k for x, k in zip(a, axis)]
                sb = [x + off * k for x, k in zip(b, axis)]
                rr = [_seg(p, sa, sb)[1] for p in hp]
                entry['halves']['%+d' % sign] = {'offset_cm': round(off, 2), 'radius_cm': _r(_pct(rr, RADIUS_PCT)),
                                                 'samples': len(hp)}
        out['segments'][spec['name']] = entry
    for spec in specs.get('blobs', []):
        mp = members(set(spec['region']))
        if not mp:
            continue
        c = [sum(p[k] for p in mp) / len(mp) for k in range(3)]
        out['blobs'][spec['name']] = {'bone': spec['bone'], 'centre_cs': [round(v, 3) for v in c],
                                      'radius_cm': _r(_pct([math.dist(p, c) for p in mp], RADIUS_PCT)),
                                      'samples': len(mp)}
    for spec in specs.get('chains', []):
        a, b = pos_ref[spec['root']], pos_ref[spec['bones'][-1]]
        dists = [_seg(p, a, b)[1] for p in members(set(spec['bones']))]
        entry = {'samples': len(dists), 'half_width_cm': _r(_pct(dists, WIDTH_PCT)), 'tails': []}
        chain = set(spec['bones'])
        single = len(spec['bones']) == 1
        for leaf in spec['bones'] if single else spec['bones'][1:]:
            if any(parent.get(o) == leaf for o in chain) or parent.get(leaf) not in pos_ref:
                continue                                   # not a leaf of this chain
            lp, pp = pos_ref[leaf], pos_ref[parent[leaf]]
            use_free, free_ids = False, set()
            if single:
                # A flap (a feather on one bone) has no chain direction: its mesh points the way it hangs, so the tip
                # runs from the bone towards the centroid of its FREE geometry when that is most of it (a pendant welded
                # to a painted cord), else of every vertex it dominates (collider_autofit.flap_vertex_sets).
                mine = {leaf} | {b for b in parent if _descends(b, leaf, parent)}
                dom, free, use_free = _af.flap_vertex_sets(vweights, leaf, mine, DOMINANT, TAIL_MIN_SAMPLES)
                entry['skin'] = {'dominant': len(dom), 'free': len(free)}
                sel = free if use_free else dom
                if len(dom) < TAIL_MIN_SAMPLES:
                    # A BLENDED flap dominates nothing: every vertex is shared with its host (soft tissue: a breast
                    # bone carries under half of each vertex it moves, measured 0 dominated of 556 weighted, 2026-10-29).
                    # Its geometry is the vertices it carries a real share of; the tip and reach are measured over them.
                    sel = [i for i, w in enumerate(vweights) if sum(w.get(b, 0.0) for b in mine) >= LEAF_SHARE]
                    entry['skin']['blended'] = len(sel)
                    use_free, free = True, sel
                own = [pts[i] for i in sel]
                free_ids = set(free)
                if len(own) < TAIL_MIN_SAMPLES:
                    continue
                pp = _af.sub(lp, _af.sub([sum(p[k] for p in own) / len(own) for k in range(3)], lp))
            d = _af.sub(lp, pp)
            n = math.sqrt(_af.dot(d, d))
            if n < 1e-6:
                continue
            d = [x / n for x in d]
            # The chain's vertices the leaf carries a real share of: a strip's end is often split 50/50 with the bone
            # above, so "dominated by the leaf" finds nothing (measured: 0 of 551, the mesh 21.6 cm past the bone).
            # A flap whose tip follows its free geometry measures its reach over that same set.
            past = [_af.dot(_af.sub(p, lp), d) for i, (p, w) in enumerate(zip(pts, vweights))
                    if (i in free_ids if use_free else
                        w.get(leaf, 0.0) >= LEAF_SHARE and sum(w.get(b, 0.0) for b in chain) >= DOMINANT)]
            beyond = [v for v in past if v > 0.0]
            if len(beyond) < max(TAIL_MIN_SAMPLES, TAIL_MIN_SHARE * len(past)):
                continue
            length = _pct(beyond, TAIL_PCT)
            end = [x + length * k for x, k in zip(lp, d)]
            loc = xf_ref[leaf].inverse_transform_location(unreal.Vector(*end))
            entry['tails'].append({'bone': leaf, 'length_cm': _r(length), 'end_cs': [round(v, 3) for v in end],
                                   'local': [round(loc.x, 3), round(loc.y, 3), round(loc.z, 3)]})
        if not single:
            # Face normals for a twist modifier (collider_autofit.face_normal): each bone the solver rotates, i.e. with
            # exactly one particle child (the next chain bone, or a leaf's tail end). A branch keeps its animated
            # rotation in the solver (RigDynamicsSolver.cpp:832-845) and gets none. Vertices: the bone carries a real
            # share (LEAF_SHARE) and the chain dominates, weighted by the bone's influence.
            host = pos_ref.get(parent.get(spec['root']))
            tail_end = {t['bone']: t['end_cs'] for t in entry['tails']}
            faces = {}
            for b in spec['bones']:
                kids = [o for o in spec['bones'] if parent.get(o) == b]
                nxt = pos_ref[kids[0]] if len(kids) == 1 else (tail_end.get(b) if not kids else None)
                if nxt is None or host is None:
                    faces[b] = {'reason': 'branch' if kids else 'no next particle'}
                    continue
                sel = [(p, w.get(b, 0.0)) for p, w in zip(pts, vweights)
                       if w.get(b, 0.0) >= LEAF_SHARE and sum(w.get(o, 0.0) for o in chain) >= DOMINANT]
                n, why = _af.face_normal([p for p, _ in sel], [w for _, w in sel], pos_ref[b], nxt, host)
                if n is None:
                    faces[b] = {'reason': why}
                    continue
                loc = xf_ref[b].rotation.unrotate_vector(unreal.Vector(*n))
                faces[b] = {'normal_local': [round(loc.x, 4), round(loc.y, 4), round(loc.z, 4)], 'flat': why,
                            'samples': len(sel), 'next': kids[0] if kids else 'tail'}
            entry['faces'] = faces
        out['chains'][spec['root']] = entry
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=1, sort_keys=True)
    unreal.log('HERO_INTAKE_FIT ' + json.dumps({'mesh': mesh_path, 'out': out_path, 'vertices': len(pts)}))


_mesh = globals().pop('HIF_MESH', None)
_specs = globals().pop('HIF_SPECS', None)
_out = globals().pop('HIF_OUT', None)
if _mesh and _specs is not None and _out:
    fit(_mesh, _specs, _out)
