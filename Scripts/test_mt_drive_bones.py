"""mt_drive.bone_transform reads the BONE, never a socket of the same name (review: the Paragon meshes'
Foot_R socket sits 13.3 cm down the foot, and every 'foot_r' read took it)."""
import importlib
import sys
import types

import pytest


class _T:
    """A translation-only stand-in for unreal.Transform: a * b is a then b, as FTransform composes."""

    def __init__(self, location=(0.0, 0.0, 0.0), rotation=None, scale=None):
        self.t = tuple(location)

    def inverse(self):
        return _T(tuple(-x for x in self.t))

    def __mul__(self, other):
        return _T(tuple(a + b for a, b in zip(self.t, other.t)))


def _module(monkeypatch):
    stub = types.ModuleType('unreal')
    stub.Name = str
    stub.Transform = _T
    stub.RelativeTransformSpace = types.SimpleNamespace(RTS_WORLD='world', RTS_COMPONENT='component')
    stub.log = lambda *a, **k: None
    stub.Paths = types.SimpleNamespace(project_dir=lambda: '/x', convert_relative_path_to_full=lambda p: p)
    monkeypatch.setitem(sys.modules, 'unreal', stub)
    monkeypatch.delitem(sys.modules, 'mt_drive', raising=False)
    return importlib.import_module('mt_drive')


class _Mesh:
    def __init__(self, sockets, path):
        self.sockets, self.path = sockets, path

    def get_skeletal_mesh_asset(self):
        sockets, path = self.sockets, self.path
        return types.SimpleNamespace(get_path_name=lambda: path,
                                     find_socket=lambda n: sockets.get(str(n).lower()))

    def get_socket_transform(self, name, space):
        sock = self.sockets.get(str(name).lower())
        bone = (10.0, 0.0, 50.0)                                  # the bone's own transform
        return _T(sock.relative_location) * _T(bone) if sock else _T(bone)


def _socket(name, bone, loc):
    return types.SimpleNamespace(socket_name=name, bone_name=bone, relative_location=loc, relative_rotation=None,
                                 relative_scale=None)


def test_a_same_named_socket_on_the_bone_is_undone(monkeypatch):
    mt = _module(monkeypatch)
    mesh = _Mesh({'foot_r': _socket('Foot_R', 'Foot_R', (13.28, 0.0, 0.0))}, '/Game/A')
    assert mt.bone_transform(mesh, 'foot_r', 'component').t == pytest.approx((10.0, 0.0, 50.0))
    assert mt.bone_transform(_Mesh({}, '/Game/B'), 'calf_r').t == (10.0, 0.0, 50.0)     # no socket: as read


def test_a_same_named_socket_on_another_bone_is_refused(monkeypatch):
    mt = _module(monkeypatch)
    mesh = _Mesh({'foot_r': _socket('Foot_R', 'ball_r', (5.0, 0.0, 0.0))}, '/Game/C')
    with pytest.raises(RuntimeError, match='sits on ball_r'):
        mt.bone_transform(mesh, 'foot_r')
