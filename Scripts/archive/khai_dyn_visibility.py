"""Is the ON-vs-OFF cloth difference visible at the planned shot framing? Pure Python, no editor.

    python Scripts/archive/khai_dyn_visibility.py Saved/MotionTerminalTests/khai_dyn_<tag>.json

The pass rule was registered in research/ue58-rigging-tools-assessment.md ('Spike steps 3–7', step 7) BEFORE this was
run: at 1920x1080, horizontal FOV 50, a camera at (pawn X + 900, -700, 150) looking at (pawn X, 0, 100), the ON
pawn's chain tip against the OFF pawn's tip carried into the ON pawn's frame must peak >= 12 px over the stop
window on at least 3 of the 6 chains.

Overlay: the OFF tip's component-space position is placed with the ON mesh's world transform, so both points
are where they would be drawn if the two pawns stood in the same place. The ON tip is its recorded world position.
Pawn 0 is ON, pawn 1 is OFF (the drive's alphas [1, 0]).
"""
import json
import math
import sys

W, H, HFOV = 1920, 1080, 50.0
CAM_OFFSET = (900.0, -700.0, 150.0)     # added to (pawn X, 0, 0)
LOOK_AT = (0.0, 0.0, 100.0)             # added to (pawn X, 0, 0)
THRESHOLD_PX, MIN_CHAINS = 12.0, 3
STOP_W = 1.0


def _sub(a, b):
    return [x - y for x, y in zip(a, b)]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return [x / n for x in a]


def _yaw_rotate(v, yaw_deg):
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    return [c * v[0] - s * v[1], s * v[0] + c * v[1], v[2]]


def project(point, cam, target):
    """Pinhole projection to pixel coordinates (UE axes: X forward, Y right, Z up)."""
    fwd = _norm(_sub(target, cam))
    right = _norm(_cross([0.0, 0.0, 1.0], fwd))
    up = _cross(fwd, right)
    rel = _sub(point, cam)
    depth = _dot(rel, fwd)
    focal = (W / 2.0) / math.tan(math.radians(HFOV / 2.0))
    return [W / 2.0 + focal * _dot(rel, right) / depth, H / 2.0 - focal * _dot(rel, up) / depth], depth


def main(path):
    rec = json.load(open(path))
    frames, ev = rec['frames'], rec['events']
    t_rel = ev['t_release']
    stop = next(f['t'] for f in frames if f['t'] >= t_rel and f['pawns'][0]['speed'] <= 1.0)
    peaks = {}
    for f in frames:
        if not (t_rel <= f['t'] <= stop + STOP_W):
            continue
        on, off = f['pawns'][0], f['pawns'][1]
        mx, my, mz, yaw, pitch, roll = on['mesh_w']
        if abs(pitch) > 0.01 or abs(roll) > 0.01:
            raise SystemExit('mesh pitch/roll %.3f/%.3f: the yaw-only overlay does not hold' % (pitch, roll))
        cam = [on['loc'][0] + CAM_OFFSET[0], CAM_OFFSET[1], CAM_OFFSET[2]]
        target = [on['loc'][0] + LOOK_AT[0], LOOK_AT[1], LOOK_AT[2]]
        for chain, tip_w in on['tips_w'].items():
            off_w = [a + b for a, b in zip(_yaw_rotate(off['tips'][chain], yaw), (mx, my, mz))]
            (pa, depth), (pb, _) = project(tip_w, cam, target), project(off_w, cam, target)
            px = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
            if px > peaks.get(chain, (0,))[0]:
                peaks[chain] = (round(px, 2), f['t'], round(depth, 1))
    passing = [c for c, (px, _t, _d) in peaks.items() if px >= THRESHOLD_PX]
    out = {'path': path, 'rule': '>= %g px on >= %d of 6 chains' % (THRESHOLD_PX, MIN_CHAINS),
           'peak_px_t_depth': peaks, 'chains_passing': passing, 'pass': len(passing) >= MIN_CHAINS}
    print(json.dumps(out, indent=1))
    return out


if __name__ == '__main__':
    main(sys.argv[1])
