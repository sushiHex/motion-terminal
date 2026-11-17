"""Cut a rendered take so it loops: the cut-out frame in the closing stand flows into the cut-in frame in the opening
stand as cleanly as two neighbouring frames do (the stands at
the head and tail match up cleanly).

    python Scripts/loop_cut.py <frames dir> <out.mp4> [--fps 60] [--head-skip 0.25] [--scale 1920:1080] [--overlay a.png]

Works on the rendered frames, not a simulation record, so it needs no mapping between MRQ output numbers and driver
ticks. Each frame is read as a small grayscale image (ffmpeg). Motion energy (the mean absolute difference to the
previous frame) finds the two standing windows: the opening one ends where the energy first rises above STAND_K x
its own baseline, and the closing one begins where it last falls back under it. Among cut-in frames i in the opening
window and cut-out frames j in the closing window, the pair with the smallest D(frame j, frame i-1) is chosen, so that
j -> i looks like the ordinary step (i-1) -> i. The report states the seam distance against the clip's median
neighbour step, so "clean" is a number.
"""
import json
import os
import subprocess
import sys

import numpy as np

W, H = 320, 180         # comparison resolution; enough for silhouettes and limbs, cheap for 450 frames
STAND_K = 3.0           # a standing window lasts while motion energy stays under this x its baseline


def load(frames_dir):
    names = sorted(f for f in os.listdir(frames_dir) if f.startswith('frame_') and f.endswith('.png'))
    raw = subprocess.run(['ffmpeg', '-loglevel', 'error', '-framerate', '60', '-start_number',
                          names[0][len('frame_'):-len('.png')], '-i', os.path.join(frames_dir, 'frame_%04d.png'),
                          '-vf', 'scale=%d:%d,format=gray' % (W, H), '-f', 'rawvideo', '-'],
                         capture_output=True, check=True).stdout
    return names, np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)


def dist(a, b):
    return float(np.abs(a - b).mean())


def standing_windows(frames, head_skip):
    """(head_lo, head_hi, tail_lo, tail_hi), inclusive frame indices of the opening and closing stands."""
    energy = np.array([0.0] + [dist(frames[k], frames[k - 1]) for k in range(1, len(frames))])
    base = float(np.median(energy[head_skip:head_skip + 30]))
    limit = STAND_K * max(base, 1e-3)
    head_hi = next(k for k in range(head_skip, len(frames)) if energy[k] > limit) - 1
    tail_lo = next(k for k in range(len(frames) - 1, head_hi, -1) if energy[k] > limit) + 1
    return head_skip, head_hi, tail_lo, len(frames) - 1, energy


def best_cut(frames, head, tail):
    """Cut-in i in head (lo, hi), cut-out j in tail (lo, hi): minimise D(frames[j], frames[i - 1])."""
    best = None
    for i in range(max(head[0], 1), head[1] + 1):
        for j in range(tail[0], tail[1] + 1):
            d = dist(frames[j], frames[i - 1])
            if best is None or d < best[0]:
                best = (d, i, j)
    return best


def window(frames_dir, fps=60.0, head_skip_s=0.25):
    """The loop of a rendered take: (names, i, j, report) with frames i..j (indices into names) the cut, or None when
    the take has no run to loop between. The overlay of a take draws over the same cut (hit_reaction_release)."""
    names, frames = load(frames_dir)
    try:
        h_lo, h_hi, t_lo, t_hi, energy = standing_windows(frames, int(round(fps * head_skip_s)))
    except StopIteration:
        return None
    seam, i, j = best_cut(frames, (h_lo, h_hi), (t_lo, t_hi))
    step = float(np.median(energy[1:]))
    return names, i, j, {'cut_in': names[i], 'cut_out': names[j], 'frames': j - i + 1,
                         'seconds': round((j - i + 1) / fps, 3), 'head_window': [names[h_lo], names[h_hi]],
                         'tail_window': [names[t_lo], names[t_hi]], 'seam_distance': round(seam, 3),
                         'median_neighbour_step': round(step, 3), 'seam_vs_step': round(seam / max(step, 1e-6), 2)}


def main(argv):
    frames_dir, out = argv[0], argv[1]
    fps = float(argv[argv.index('--fps') + 1]) if '--fps' in argv else 60.0
    cut = window(frames_dir, fps, float(argv[argv.index('--head-skip') + 1]) if '--head-skip' in argv else 0.25)
    if cut is None:
        print('LOOP_CUT no run found in %s: nothing to loop between' % frames_dir)
        return 1
    names, i, j, report = cut
    first = int(names[i][len('frame_'):-len('.png')])
    # --overlay: an image composited top-left (a legend) in the same, single encode
    scale = 'scale=%s:flags=lanczos' % argv[argv.index('--scale') + 1] if '--scale' in argv else 'null'
    overlay = argv[argv.index('--overlay') + 1] if '--overlay' in argv else None
    graph = (['-i', overlay, '-filter_complex', '[0:v]%s[v];[v][1:v]overlay=24:24' % scale] if overlay
             else ['-vf', scale])
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-framerate', str(fps), '-start_number', str(first),
                    '-i', os.path.join(frames_dir, 'frame_%04d.png')] + graph + ['-frames:v', str(j - i + 1),
                   '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '16', out], check=True)
    print('LOOP_CUT ' + json.dumps(dict(report, out=out)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
