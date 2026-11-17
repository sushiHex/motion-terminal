"""dyn_analyze.root_lift: a chain's first simulated bone, measured in its parent's frame, ON against OFF."""
import math

import dyn_analyze as an

ID = [0.0, 0.0, 0.0, 1.0]


def _frame(lift, head_yaw_deg=0.0):
    h = math.radians(head_yaw_deg) / 2
    q = [0.0, 0.0, math.sin(h), math.cos(h)]
    # both pawns' heads turn together; pawn 0's clump bone is lifted `lift` cm off the head along the head's own +x
    c, s = math.cos(2 * h), math.sin(2 * h)
    on = [10.0 * c - 0.0 * s + lift * c, 10.0 * s + lift * s, 170.0] + ID
    off = [10.0 * c, 10.0 * s, 170.0] + ID
    head = [0.0, 0.0, 170.0] + q
    return {'pawns': [{'tm': {'head': head, 'hair_02': on}}, {'tm': {'head': head, 'hair_02': off}}]}


def test_lift_is_measured_in_the_parents_frame():
    parts = [{'id': 'hair_01', 'bones': ['hair_01', 'hair_02'], 'parent': 'head'}]
    frames = [_frame(0.0), _frame(3.0, 45.0), _frame(5.0, 90.0), _frame(1.0)]
    assert an.root_lift(frames, 1, parts) == {'hair_01': 5.0}           # head turning does not count, the lift does
    assert an.root_lift([{'pawns': [{'tm': {}}, {'tm': {}}]}], 1, parts) == {'hair_01': None}
