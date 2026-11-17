"""The engine names this project's graph specs depend on, as one checked list.

A spec names engine things by string: a node class path, a property path inside the node's struct, the members of
every struct value it writes. Unreal renames such things between versions, and a text import of a struct value
skips a member it no longer knows without an error (the 5.9 damping rename cost exactly that). So the names are
collected from the specs themselves into Plugins/MotionTerminal/Tests/engine_contract.json, and checked twice:

  host   (Scripts/test_engine_contract.py)  the committed file is exactly what today's specs produce
  editor (Verify.EngineContract)            every class loads and every path resolves in the running engine

A name a spec starts using reaches the engine check in the same change, and an engine upgrade that drops one fails
a headless test that names it, before any asset is built.

    {"anim_nodes": {"/Script/Module.AnimGraphNode_X": ["Member", "Member.Sub", "Array[].Member", ...]}}

A path is members joined by '.', with '[]' after a member that is an array (the path continues in its element).
"""
import json
import os

import animgraph_spec as ag

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(REPO, 'Plugins', 'MotionTerminal', 'Tests', 'engine_contract.json')


def member_paths(value, prefix):
    """Every member path a parsed export-text value names under `prefix`: the value's own path, and for a struct or
    an array of structs each member inside it."""
    out = {prefix}
    if isinstance(value, dict):
        for key, inner in value.items():
            out |= member_paths(inner, '%s.%s' % (prefix, key))
    elif isinstance(value, list):
        for inner in value:
            if isinstance(inner, (dict, list)):
                out |= member_paths(inner, prefix + '[]')
    return out


def _array_step(path):
    """A spec's path or pin spelling as a contract path: "Legs[2].Bone" -> "Legs[].Bone", "BlendPoses_0" ->
    "BlendPoses[]"."""
    steps = []
    for step in path.split('.'):
        if '[' in step:
            step = step[:step.index('[')] + '[]'
        steps.append(step)
    head = steps[0]
    name, _, index = head.rpartition('_')
    if name and index.isdigit():
        steps[0] = name + '[]'
    return '.'.join(steps)


def from_spec(a_spec):
    """{node class: set of paths} for one animgraph spec: every property path with the members its value names, and
    every linked input pin that is a member of the node's struct (a pin the spec exposes is the asset's own
    variable, not an engine name, and is left out)."""
    out = {}
    exposed = {}
    for node in a_spec['nodes']:
        paths = out.setdefault(node['class'], set())
        exposed[node['id']] = set(node['exposed'])
        for path, text in node['properties']:
            paths |= member_paths(ag.parse_export(text), _array_step(path))
    classes = {node['id']: node['class'] for node in a_spec['nodes']}
    for _src, _src_pin, to, pin in a_spec['links']:
        if to in classes and pin not in exposed[to]:
            out[classes[to]].add(_array_step(pin))
    return out


def merge(*parts):
    out = {}
    for part in parts:
        for cls, paths in part.items():
            out.setdefault(cls, set()).update(paths)
    return out


def document(nodes):
    """The file's content for {class: paths}: sorted, so the file only changes when a name does."""
    return {'anim_nodes': {cls: sorted(paths) for cls, paths in sorted(nodes.items())}}


def read(path=PATH):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def write(nodes, path=PATH):
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(document(nodes), fh, indent=1)
        fh.write('\n')
