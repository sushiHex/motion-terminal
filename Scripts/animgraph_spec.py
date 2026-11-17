"""An AnimGraph as a declarative spec, and the comparison that accepts a build.

    spec (here, pure)  ->  AnimGraphBuildTools.BuildAnimBlueprint  ->  AnimGraphTools.DescribeAnimGraph  ->  mismatch
                                                                    ->  AnimGraphBuildTools.SaveAnimBlueprint

Design: docs/specs/2026-11-12-animgraph-build-describe-design.md. A build is accepted, and only then saved, when
mismatch() returns nothing; the build tool's own "ok" is never the evidence, and the describe tool shares no code
with it.

The spec's property values are engine export text (what hit_reaction_core produces, imported over the live value).
The description's are typed JSON with every struct member present, because the engine's text export drops members
that equal their defaults and a reader of it cannot tell "default" from "missing".

Pure: no editor is needed to import or test this file. The sequence above is run by asset_pipeline.realize with
the AnimGraph kind (asset_pipeline.animgraph), the one place that order is written.
"""
import json

OUTPUT = '<output>'       # the root node's id in a description
OUTPUT_PIN = 'Result'
VARIABLE_TYPES = {'float': 'real', 'bool': 'bool', 'int': 'int'}    # spec type -> the pin category Describe reports
VARIABLE_GET = '/Script/BlueprintGraph.K2Node_VariableGet'
DEFAULT_PARENT = '/Script/Engine.AnimInstance'
COMPILED = ('clean', 'warnings')        # compile statuses with a class worth reading; warnings come back from Build
FOLDED = 'folded constant'              # how Describe's reason for an unreadable folded compiled value starts
TOLERANCE = 1e-4


def node(node_id, cls, properties=(), exposed=(), poses=None):
    """One anim graph node. properties: [(path, export text)] applied in order (structure first: a Control Rig's
    asset reference before anything that depends on its variables)."""
    out = {'id': node_id, 'class': cls, 'properties': [[p, str(v)] for p, v in properties], 'exposed': list(exposed)}
    if poses is not None:
        out['poses'] = int(poses)
    return out


def spec(skeleton, nodes, links, output, variables=(), variable_gets=(), parent_class=''):
    """links: [(from id, output pin, to id, input pin)]; output: (id, pose output pin); variables: [(name, type,
    default)]; variable_gets: [(id, variable)]. Every variable is instance-editable: a graph's variables are what the
    host drives per instance (an alpha a drive sets on each pawn's anim instance), and the engine refuses to set one
    that is not."""
    return {'skeleton': skeleton, 'parent_class': parent_class,
            'variables': [{'name': n, 'type': t, 'default': str(d), 'instance_editable': True}
                          for n, t, d in variables],
            'nodes': list(nodes),
            'variable_gets': [{'id': i, 'variable': v} for i, v in variable_gets],
            'links': [list(link) for link in links], 'output': list(output)}


def paths(a_spec):
    """{node id: [property path]}: what to ask DescribeAnimGraph to read back, editor and compiled."""
    return {n['id']: [p for p, _v in n['properties']] for n in a_spec['nodes'] if n['properties']}


# --- engine export text ------------------------------------------------------------------------------------------

def parse_export(text):
    """Engine export text as data: "(A=1,B=(X=2))" -> {'A': '1', 'B': {'X': '2'}}, "((A=1),(A=2))" -> a list,
    a quoted string -> its content, anything else -> the bare token. Atoms stay text: what they mean (a number, a
    name, a path) is decided by the typed value they are compared with. Raises ValueError on text it cannot read
    to the end (a value compared half-read would pass on its first half)."""
    value, at = _value(text, 0)
    if text[at:].strip():
        raise ValueError('export text not read to its end: %r' % text[at:at + 60])
    return value


def _value(s, i):
    while i < len(s) and s[i].isspace():
        i += 1
    if i < len(s) and s[i] == '(':
        return _group(s, i + 1)
    if i < len(s) and s[i] == '"':
        return _quoted(s, i + 1)
    j = i
    depth = 0
    while j < len(s) and (depth or s[j] not in ',)'):
        if s[j] == '"':                       # Class'"/Game/A.A"' and Name="x" inside a bare token
            _text, j = _quoted(s, j + 1)
            continue
        depth += s[j] == '('
        depth -= s[j] == ')'
        j += 1
    return s[i:j].strip(), j


def _quoted(s, i):
    out = []
    while i < len(s) and s[i] != '"':
        if s[i] == '\\' and i + 1 < len(s):
            i += 1
        out.append(s[i])
        i += 1
    if i >= len(s):
        raise ValueError('unterminated string in export text')
    return ''.join(out), i + 1


def _group(s, i):
    items, keys = [], []
    while True:
        while i < len(s) and s[i].isspace():
            i += 1
        if i >= len(s):
            raise ValueError('unterminated group in export text')
        if s[i] == ')':
            i += 1
            break
        key = None
        j = i
        while j < len(s) and (s[j].isalnum() or s[j] in '_[] '):
            j += 1
        if j < len(s) and s[j] == '=' and j > i:
            key, i = s[i:j].strip(), j + 1
        value, i = _value(s, i)
        items.append(value)
        keys.append(key)
        while i < len(s) and s[i].isspace():
            i += 1
        if i < len(s) and s[i] == ',':
            i += 1
    if items and all(k is not None for k in keys):
        return dict(zip(keys, items)), i
    if any(k is not None for k in keys):
        raise ValueError('export text group mixes named and unnamed members')
    return items, i


def _reference(text):
    """A name, enum value or object path as comparable text: no class prefix (Class'/Game/A.A' -> /Game/A.A), no
    enum scope (EBoneControlSpace::BCS_X -> BCS_X), nothing for None. The object's own name stays: /Game/A.A and
    /Game/A.A_C are an asset and its class, two different objects."""
    t = text.strip()
    if "'" in t and t.endswith("'"):
        t = t[t.index("'") + 1:-1].strip('"')
    if '::' in t and not t.startswith('/'):
        t = t.rsplit('::', 1)[1]
    return '' if t == 'None' else t


def _same_reference(want, have):
    w, h = _reference(want), _reference(have)
    if w == h:
        return True
    # a spec may name an asset by its package alone; the read-back always carries the object name
    return w.startswith('/') and '.' not in w.rsplit('/', 1)[-1] and h == '%s.%s' % (w, w.rsplit('/', 1)[-1])


def _same_atom(want, have):
    """A spec atom (text) against a read-back value, compared as what the READ-BACK says it is: its type is the
    engine's, so "01" against the name "1" is two names, and against the number 1 is one number."""
    if isinstance(have, bool):
        return want.strip().lower() in ('true', 'false') and (want.strip().lower() == 'true') == have
    if isinstance(have, (int, float)):
        try:
            return abs(float(want) - have) <= TOLERANCE
        except ValueError:
            return False
    if have is None:
        return _reference(want) == ''
    return isinstance(have, str) and _same_reference(want, have)


def differences(want, have, where=''):
    """[text] where the read-back `have` (typed JSON) does not hold the spec value `want` (parsed export text). A
    struct is compared over the members `want` names (an import leaves the others as they were); a list by length
    and position, because order is behaviour; an atom by the read-back's type."""
    if isinstance(want, dict):
        if not isinstance(have, dict):
            return ['%s: a struct was set, %r read back' % (where, have)]
        out = []
        for key, value in want.items():
            if key not in have:
                out.append('%s.%s: not in what was read back' % (where, key))
            else:
                out += differences(value, have[key], '%s.%s' % (where, key))
        return out
    if isinstance(want, list):
        if not isinstance(have, list) or len(have) != len(want):
            return ['%s: %d elements set, %s read back' % (where, len(want), len(have) if isinstance(have, list)
                                                             else repr(have))]
        return [d for i, (w, h) in enumerate(zip(want, have)) for d in differences(w, h, '%s[%d]' % (where, i))]
    if isinstance(have, (dict, list)):
        return ['%s: %r set, a %s read back' % (where, want, 'struct' if isinstance(have, dict) else 'list')]
    return [] if _same_atom(want, have) else ['%s: %r set, %r read back' % (where, want, have)]


def _value_gaps(text, read, where):
    try:
        return differences(parse_export(text), read, where)
    except ValueError as e:
        return ['%s: %s' % (where, e)]


def _pin_gaps(text, default, where):
    """A visible pin's default (pin text, untyped) against an atom the spec set. A struct on a pin has its own text
    form ("x,y,z"); it is left to the compiled value, which the compiler takes from that pin."""
    try:
        want = parse_export(text)
    except ValueError as e:
        return ['%s: %s' % (where, e)]
    if not isinstance(want, str):
        return []
    typed = default
    if default.strip().lower() in ('true', 'false'):
        typed = default.strip().lower() == 'true'
    else:
        try:
            typed = float(default)
        except ValueError:
            pass
    return [] if _same_atom(want, typed) else ['%s: %r set, %r read back' % (where, want, default)]


# --- the comparison ----------------------------------------------------------------------------------------------

def mismatch(a_spec, described, folded_ok=()):
    """[difference] between a spec and DescribeAnimGraph's reply for it (asked with paths(a_spec)); empty accepts the
    build. Every spec node, property, exposed pin, variable and link must be there, each node compiled into the
    class, each value in the compiled class as well as the editor graph; and the graph must hold no node, link,
    variable or binding the spec does not.

    folded_ok: "id:path" entries whose compiled value Describe reports as a folded constant (the engine does not
    let a reader at those). Only that reason is waived, and only for the entries named: a value unread for any
    other reason always fails."""
    if not described.get('ok'):
        return ['describe: %s' % described.get('error')]
    out = []
    if described.get('compile_status') not in COMPILED:
        out.append('the blueprint is not compiled: status %s' % described.get('compile_status'))
    if described.get('ambiguous_ids'):
        out.append('node ids used twice in the graph: %s' % sorted(described['ambiguous_ids']))
    if described.get('skeleton') != a_spec['skeleton'].split('.')[0]:
        out.append('skeleton %s, wanted %s' % (described.get('skeleton'), a_spec['skeleton']))
    parent = a_spec.get('parent_class') or DEFAULT_PARENT
    if not _same_reference(parent, described.get('parent_class', '')):
        out.append('parent class %s, wanted %s' % (described.get('parent_class'), parent))

    have_vars = {v['name']: v for v in described.get('variables', [])}
    surplus = sorted(set(have_vars) - {v['name'] for v in a_spec['variables']})
    if surplus:
        out.append('variables the spec does not declare: %s' % surplus)
    for var in a_spec['variables']:
        got = have_vars.get(var['name'])
        if not got:
            out.append('variable %s: missing' % var['name'])
        elif got['type'] != VARIABLE_TYPES.get(var['type']):
            out.append('variable %s: type %s, wanted %s' % (var['name'], got['type'], var['type']))
        elif 'default' not in got:
            out.append('variable %s: its default was not read from the compiled class' % var['name'])
        elif not _same_atom(var['default'] or '0', got['default']):
            out.append('variable %s default: %r set, %r read back' % (var['name'], var['default'], got['default']))
        elif got.get('instance_editable') is not bool(var.get('instance_editable')):
            out.append('variable %s instance-editable: %s set, %r read back from the compiled class'
                       % (var['name'], bool(var.get('instance_editable')), got.get('instance_editable')))

    nodes = {}
    for n in described.get('nodes', []):
        nodes.setdefault(n['id'], n)
    declared = {n['id'] for n in a_spec['nodes']} | {g['id'] for g in a_spec['variable_gets']} | {OUTPUT}
    extra = sorted(set(nodes) - declared)
    if extra:
        out.append('nodes the spec does not declare: %s' % extra)
    for get in a_spec['variable_gets']:
        got = nodes.get(get['id'])
        if not got:
            out.append('variable get %s: missing' % get['id'])
        elif got['class'] != VARIABLE_GET or got.get('variable') != get['variable']:
            out.append('variable get %s: a %s of %s, wanted a get of %s' % (get['id'], got['class'],
                                                                            got.get('variable'), get['variable']))

    linked = {(to, pin) for _f, _fp, to, pin in a_spec['links']}
    for n in a_spec['nodes']:
        got = nodes.get(n['id'])
        if not got:
            out.append('node %s: missing' % n['id'])
            continue
        if got['class'] != n['class']:
            out.append('node %s: class %s, wanted %s' % (n['id'], got['class'], n['class']))
        if not got.get('compiled_node'):
            out.append('node %s: not in the compiled class (the output does not reach it)' % n['id'])
        if got.get('bindings'):
            out.append('node %s: bound pins %s; a spec links, it does not bind' % (n['id'], sorted(got['bindings'])))
        pins = {p['name']: p for p in got.get('pins', []) if p['direction'] == 'in'}
        for name in n['exposed']:
            if name not in pins:
                out.append('node %s: %s is not an exposed pin' % (n['id'], name))
        if 'poses' in n:
            poses = sum(1 for name in pins if name.startswith('BlendPoses_'))
            if poses != n['poses']:
                out.append('node %s: %d blend poses, wanted %d' % (n['id'], poses, n['poses']))
        unread = got.get('unread', {})
        for path, text in n['properties']:
            where = '%s:%s' % (n['id'], path)
            # An unlinked visible pin is what the compiler reads, and showing one can move the value out of the
            # struct: for such a member the pin and the compiled class are the evidence, not the editor struct.
            pin = pins.get(path)
            on_pin = pin is not None and (n['id'], path) not in linked and 'default' in pin
            if on_pin:
                out += _pin_gaps(text, pin['default'], 'pin default ' + where)
            elif path in got.get('editor', {}):
                out += _value_gaps(text, got['editor'][path], 'editor ' + where)
            else:
                out.append('editor %s: not read (%s)' % (where, unread.get('editor:' + path)))
            if path in got.get('compiled', {}):
                out += _value_gaps(text, got['compiled'][path], 'compiled ' + where)
            else:
                why = unread.get('compiled:' + path) or ''
                if not (where in folded_ok and why.startswith(FOLDED)):
                    out.append('compiled %s: not read (%s)' % (where, why or None))

    want_links = sorted([list(link) for link in a_spec['links']] + [a_spec['output'] + [OUTPUT, OUTPUT_PIN]])
    have_links = sorted(list(link) for link in described.get('links', []))
    if have_links != want_links:
        missing = [link for link in want_links if link not in have_links]
        surplus = [link for link in have_links if link not in want_links]
        out.append('links differ: missing %s, not in the spec %s' % (missing, surplus))
    return out


BUILD_TOOLS = 'MotionTerminal.AnimGraphBuildTools'
READ_TOOLS = 'MotionTerminal.AnimGraphTools'

