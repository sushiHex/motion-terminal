"""Which bones drive each separate piece of one material section (e.g. every feather of the feather material).

Reads UE 5.8's SkeletalMeshExporterUsd .usda as TEXT (same source as usd_chain_weights.py): LOD0's points, faces,
the section's GeomSubset face indices and the skin (its width read from the joint arrays' elementSize: the exporter
writes the LOD's max influences, not a constant). Faces sharing a vertex form one island; per island it prints the
vertex count, the centroid in the USD STAGE's coordinates (not converted back to UE component space: the exporter
changes axes and units) and the bones carrying the most summed weight. Which of those bones are simulated is the
hero profile's business: this only reports, per top bone, how many pieces it drives.

Usage: python -I Scripts/usd_section_islands.py <Mesh.usda> <SectionN> [LODn=LOD0]
Writes <Mesh>_<SectionN>_islands.json next to the input (the input's extension, whatever its case, is replaced;
a run whose output would resolve to the input itself is refused before anything is written).
"""
import collections
import json
import os
import re
import sys


def output_path(src, tail):
    """`src` with its extension (any case: Mesh.USDA, Mesh.usd) replaced by `tail`. Raises SystemExit when the result
    is the input itself, so opening it for writing can never truncate the source. (Kept beside its twin in
    usd_chain_weights.py: both run under `python -I`, which does not put Scripts/ on the import path.)"""
    out = os.path.splitext(src)[0] + tail
    if os.path.exists(out) and os.path.exists(src) and os.path.samefile(out, src):
        raise SystemExit('REFUSING: the output %s would overwrite the input %s' % (out, src))
    return out


def array_after(text, key, start):
    i = text.index(key, start)
    j = text.index('= [', i) + 2
    k = text.index(']', j)
    return text[j + 1:k], k


def nums(raw, cast):
    return [cast(x) for x in raw.split(',') if x.strip()]


def main(argv):
    path, section = argv[1], argv[2]
    lod = argv[3] if len(argv) > 3 else 'LOD0'
    out_path = output_path(path, '_%s_islands.json' % section)
    text = open(path, encoding='utf-8').read()

    joints = [j.strip().strip('"').split('/')[-1]
              for j in array_after(text, 'uniform token[] joints', 0)[0].split(',')]
    m = re.search(r'def Mesh "%s"' % lod, text).end()
    end_mesh = text.find('def Mesh "', m)
    fv_counts = nums(array_after(text, 'int[] faceVertexCounts', m)[0], int)
    fv_idx = nums(array_after(text, 'int[] faceVertexIndices', m)[0], int)
    pts = [tuple(float(c) for c in p.split(','))
           for p in re.findall(r'\(([^()]*)\)', array_after(text, 'point3f[] points', m)[0])]
    jidx = nums(array_after(text, 'primvars:skel:jointIndices', m)[0], int)
    jw = nums(array_after(text, 'primvars:skel:jointWeights', m)[0], float)
    width = int(re.search(r'elementSize = (\d+)', text[text.index('primvars:skel:jointIndices', m):]).group(1))
    if len(jidx) != width * len(pts) or len(jw) != len(jidx):
        raise SystemExit('skin arrays (%d indices, %d weights) do not match %d points x %d influences'
                         % (len(jidx), len(jw), len(pts), width))
    sub = text.index('def GeomSubset "%s"' % section, m)
    if end_mesh != -1 and sub > end_mesh:
        raise SystemExit('%s not in %s' % (section, lod))
    faces = nums(array_after(text, 'int[] indices', sub)[0], int)

    starts, s = [], 0
    for c in fv_counts:
        starts.append(s)
        s += c
    parent = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for f in faces:
        vs = fv_idx[starts[f]:starts[f] + fv_counts[f]]
        for v in vs[1:]:
            parent[find(v)] = find(vs[0])
    islands = collections.defaultdict(set)
    for v in list(parent):
        islands[find(v)].add(v)

    out = []
    for vs in islands.values():
        w = collections.Counter()
        for v in vs:
            for k in range(width):
                if jw[v * width + k] > 0:
                    w[joints[jidx[v * width + k]]] += jw[v * width + k]
        tot = sum(w.values())
        c = [round(sum(pts[v][i] for v in vs) / len(vs), 1) for i in range(3)]
        out.append({'verts': len(vs), 'centroid_usd': c,
                    'bones': [[b, round(x / tot, 3)] for b, x in w.most_common(3)] if tot > 0
                    else [['(unweighted)', 0.0]]})
    out.sort(key=lambda r: (r['bones'][0][0], r['centroid_usd']))
    print('%s %s: %d faces, %d islands, %d influences per vertex' % (lod, section, len(faces), len(out), width))
    for b in sorted({r['bones'][0][0] for r in out}):
        rs = [r for r in out if r['bones'][0][0] == b]
        print('  top bone %-28s %3d islands %5d verts' % (b, len(rs), sum(r['verts'] for r in rs)))
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=1)


if __name__ == '__main__':
    main(sys.argv)
