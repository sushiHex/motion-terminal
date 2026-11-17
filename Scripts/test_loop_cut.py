"""loop_cut's choices on synthetic frames: an idle that cycles, a run in the middle, the same idle after it."""
import numpy as np

import loop_cut


SWAY = [0, 1, 2, 3, 4, 3, 2, 1]        # a block swaying back and forth, one even step per frame, like an idle


def _pos(phase):
    return 40 + SWAY[phase % len(SWAY)] * 4


def _idle(phase):
    img = np.zeros((loop_cut.H, loop_cut.W), np.float32)
    img[60:120, _pos(phase):_pos(phase) + 20] = 200.0
    return img


def _clip(head=40, run=30, tail=37, tail_phase=3):
    frames = [_idle(k) for k in range(head)]
    frames += [np.random.default_rng(k).uniform(0, 255, (loop_cut.H, loop_cut.W)).astype(np.float32)
               for k in range(run)]                                       # the run: large frame-to-frame change
    frames += [_idle(tail_phase + k) for k in range(tail)]
    return np.stack(frames)


def test_standing_windows_bracket_the_run():
    frames = _clip()
    h_lo, h_hi, t_lo, t_hi, _ = loop_cut.standing_windows(frames, 2)
    assert h_lo == 2 and h_hi == 39 and t_lo == 71 and t_hi == len(frames) - 1


def test_best_cut_lands_the_seam_on_matching_phase():
    frames = _clip()
    seam, i, j = loop_cut.best_cut(frames, (2, 39), (71, len(frames) - 1))
    assert seam == 0.0
    assert _pos(3 + (j - 70)) == _pos(i - 1)      # frame j shows the pose that precedes the cut-in frame
