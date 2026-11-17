"""CR Dynamics skin-weight spike: per chain, per LOD, how many vertices are skinned to each chain bone.

A chain bone the renderable mesh is not weighted to cannot show secondary motion, however well it
simulates, so this decides which chains and LODs the Dynamics rig may target.

Source: UE 5.8's own SkeletalMeshExporterUsd output (for Khaimera, the usdCharacter lab export of
2026-10-14, which carried the skin in full: SkelBindingAPI, elementSize 6, no truncation; see
research/usdcharacter-pipeline-fit.md). Reads the .usda as TEXT, so no pxr install is needed: the
Skeleton's `joints`, then each LOD variant mesh's jointIndices / jointWeights. CHAINS names Khaimera's
candidate chains; edit it for another rig.

Usage: python -I Scripts/usd_chain_weights.py <Mesh.usda> [min_weight=0.05]
Writes <Mesh>_s2_chain_weights.json next to the input (the input's extension, whatever its case, is replaced;
a run whose output would resolve to the input itself is refused before anything is written).
"""
import collections
import json
import os
import re
import sys

OUT_TAIL = "_s2_chain_weights.json"
CHAINS = {
    "loin_cloth_bk": r"^loin_cloth_bk_\d+$", "loin_cloth_fr": r"^loin_cloth_fr_\d+$",
    "torso_cloth_bk_l": r"^torso_cloth_bk_l_\d+$", "torso_cloth_bk_r": r"^torso_cloth_bk_r_\d+$",
    "torso_cloth_fr_l": r"^torso_cloth_fr_l_\d+$", "torso_cloth_fr_r": r"^torso_cloth_fr_r_\d+$",
    "feather_l": r"^feather_\d+_l$", "feather_r": r"^feather_\d+_r$",
    "weapon_tail_l": r"^weapon_tail_l_\d+$", "weapon_tail_r": r"^weapon_tail_r_\d+$",
    "necklace": r"^necklace", "back_moon": r"^back_moon",
}


def output_path(src, tail):
    """`src` with its extension (any case: Mesh.USDA, Mesh.usd) replaced by `tail`. Raises SystemExit when the result
    is the input itself, so opening it for writing can never truncate the source."""
    out = os.path.splitext(src)[0] + tail
    if os.path.exists(out) and os.path.exists(src) and os.path.samefile(out, src):
        raise SystemExit("REFUSING: the output %s would overwrite the input %s" % (out, src))
    return out


def array_after(text, key, start):
    i = text.index(key, start)
    j = text.index("= [", i) + 2   # not the "[" of the type, e.g. token[]
    k = text.index("]", j)
    return text[j + 1:k], k


def main(argv):
    path = argv[1]
    min_w = float(argv[2]) if len(argv) > 2 else 0.05
    out_path = output_path(path, OUT_TAIL)
    text = open(path, encoding="utf-8").read()
    joints_raw, _ = array_after(text, "uniform token[] joints", 0)
    joints = [j.strip().strip('"').split("/")[-1] for j in joints_raw.split(",")]
    result = {"source": path, "min_weight": min_w, "joints": len(joints), "lods": {}}
    for m in re.finditer(r'def Mesh "(LOD\d+)"', text):
        lod = m.group(1)
        idx_raw, end = array_after(text, "primvars:skel:jointIndices", m.end())
        w_raw, _ = array_after(text, "primvars:skel:jointWeights", end)
        idx = [int(x) for x in idx_raw.split(",")]
        wts = [float(x) for x in w_raw.split(",")]
        per_bone = collections.Counter()
        nverts = len(idx) // 6
        for v in range(nverts):
            for s in range(6):
                if wts[v * 6 + s] >= min_w:
                    per_bone[joints[idx[v * 6 + s]]] += 1
        lodres = {"vertices": nverts, "chains": {}}
        for chain, pat in CHAINS.items():
            bones = sorted(b for b in joints if re.match(pat, b))
            lodres["chains"][chain] = {"bones": len(bones), "weighted_vertices": sum(per_bone[b] for b in bones),
                                       "bones_with_weight": sum(1 for b in bones if per_bone[b] > 0),
                                       "per_bone": {b: per_bone[b] for b in bones}}
        result["lods"][lod] = lodres
    print(json.dumps({lod: {"verts": r["vertices"], **{c: "%d/%d bones, %d v" % (d["bones_with_weight"], d["bones"],
                      d["weighted_vertices"]) for c, d in r["chains"].items()}} for lod, r in result["lods"].items()},
                     indent=1))
    with open(out_path, "w") as f:
        json.dump(result, f, indent=1)


if __name__ == "__main__":
    main(sys.argv)
