import json

import pytest

import animgraph_spec as ag

COPY = '/Script/AnimGraph.AnimGraphNode_CopyBone'
L2C = '/Script/AnimGraph.AnimGraphNode_LocalToComponentSpace'
RIG = '/Script/ControlRigDeveloper.AnimGraphNode_ControlRig'
IN = '/Script/AnimGraph.AnimGraphNode_LinkedInputPose'
EVAL = '/Script/AnimGraph.AnimGraphNode_SequenceEvaluator'
RIG_CLASS = '/Game/Hero/CR_Hero.CR_Hero_C'
FOLDED_WHY = "folded constant: the compiled value is in the class's constant data, which is not readable here"


def _spec():
    return ag.spec(
        '/Game/Hero/SK_Hero',
        [ag.node('in', IN), ag.node('l2c', L2C),
         ag.node('copy', COPY, [('SourceBone', '(BoneName="foot_l")'), ('bCopyTranslation', 'True'),
                                ('ControlSpace', 'BCS_ComponentSpace')]),
         ag.node('rig', RIG, [('ControlRigAssetReference', '(BlueprintRigClass="%s")' % RIG_CLASS)],
                 exposed=['DynamicsAlpha'])],
        [('in', 'Pose', 'l2c', 'LocalPose'), ('l2c', 'ComponentPose', 'copy', 'ComponentPose'),
         ('copy', 'Pose', 'rig', 'Source'), ('alpha', 'DynamicsAlpha', 'rig', 'DynamicsAlpha')],
        ('rig', 'Pose'), variables=[('DynamicsAlpha', 'float', '0.5')], variable_gets=[('alpha', 'DynamicsAlpha')])


def _described(spec):
    """What DescribeAnimGraph says of a faithful build: typed values, every struct member present, objects with
    their class, and a visible pin carrying the flag."""
    def pin(name, default=None):
        p = {'name': name, 'direction': 'in', 'type': 'x'}
        if default is not None:
            p['default'] = default
        return p
    values = {'SourceBone': {'BoneName': 'foot_l'}, 'bCopyTranslation': True, 'ControlSpace': 'BCS_ComponentSpace',
              'ControlRigAssetReference': {
                  'BlueprintRigClass': "/Script/Engine.BlueprintGeneratedClass'%s'" % RIG_CLASS,
                  'ControlRigAsset': 'None'}}
    nodes = []
    for n in spec['nodes']:
        got = {p: values[p] for p, _v in n['properties']}
        pins = [pin(name) for name in n['exposed']]
        if n['id'] == 'copy':
            pins.append(pin('bCopyTranslation', 'true'))
        nodes.append({'id': n['id'], 'name': 'N_' + n['id'], 'class': n['class'], 'pins': pins, 'bindings': {},
                      'compiled_node': True, 'editor': dict(got), 'compiled': dict(got), 'unread': {}})
    nodes += [{'id': 'alpha', 'name': 'K2Node_VariableGet_0', 'class': ag.VARIABLE_GET, 'variable': 'DynamicsAlpha',
               'pins': []},
              {'id': ag.OUTPUT, 'name': 'AnimGraphNode_Root_0', 'class': '/Script/AnimGraph.AnimGraphNode_Root',
               'pins': [], 'bindings': {}, 'compiled_node': True}]
    links = [list(link) for link in spec['links']] + [spec['output'] + [ag.OUTPUT, ag.OUTPUT_PIN]]
    return {'ok': True, 'compile_status': 'clean', 'ambiguous_ids': [], 'skeleton': '/Game/Hero/SK_Hero',
            'parent_class': '/Script/Engine.AnimInstance',
            'variables': [{'name': 'DynamicsAlpha', 'type': 'real', 'default': 0.5, 'instance_editable': True}],
            'nodes': nodes, 'links': sorted(links)}


def _node(described, node_id):
    return next(n for n in described['nodes'] if n['id'] == node_id)


def test_export_text_parses_structs_arrays_and_quoted_names():
    assert ag.parse_export('(BoneName="foot_l")') == {'BoneName': 'foot_l'}
    assert ag.parse_export('((A=1,B=(X=2.5,Y=0)),(A=3))') == [{'A': '1', 'B': {'X': '2.5', 'Y': '0'}}, {'A': '3'}]
    assert ag.parse_export('(Name="a,b (c)",Data=(V=1))') == {'Name': 'a,b (c)', 'Data': {'V': '1'}}
    assert ag.parse_export('True') == 'True'
    assert ag.parse_export("/Script/Engine.PhysicsAsset'/Game/A.A'") == "/Script/Engine.PhysicsAsset'/Game/A.A'"
    assert ag.parse_export('()') == []


def test_export_text_that_is_not_read_to_its_end_raises():
    for bad in ('(A=1', '(A=1) trailing', '(A=1,2)', '(Name="open)', 'TrueTrue)'):
        with pytest.raises(ValueError):
            ag.parse_export(bad)


def test_a_partial_struct_is_held_by_the_full_typed_struct_read_back():
    want = ag.parse_export('(AngularStrength=5,LinearStrength=0,Sub=(X=1),bOn=False)')
    have = {'LinearStrength': 0.0, 'AngularStrength': 5.0, 'Sub': {'X': 1.0, 'Y': 7.0}, 'bOn': False}
    assert ag.differences(want, have, 'p') == []
    assert ag.differences(ag.parse_export('(AngularStrength=6)'), have, 'p') == \
        ["p.AngularStrength: '6' set, 5.0 read back"]
    assert ag.differences(ag.parse_export('(Gone=1)'), have, 'p') == ['p.Gone: not in what was read back']
    assert ag.differences(ag.parse_export('(bOn=True)'), have, 'p') == ["p.bOn: 'True' set, False read back"]


def test_arrays_compare_by_length_and_position():
    want = ag.parse_export('((Name="a"),(Name="b"))')
    assert ag.differences(want, [{'Name': 'a'}, {'Name': 'b'}], 'p') == []
    assert ag.differences(want, [{'Name': 'b'}, {'Name': 'a'}], 'p') != []    # order is behaviour
    assert ag.differences(want, [{'Name': 'a'}], 'p') == ['p: 2 elements set, 1 read back']
    assert ag.differences(ag.parse_export('()'), [], 'p') == []


def test_an_atom_is_compared_as_the_type_the_engine_read_back():
    assert ag.differences('01', 1, 'p') == []                 # a number
    assert ag.differences('01', '1', 'p') != []               # two names
    assert ag.differences('2', 2.00001, 'p') == []
    assert ag.differences('abc', 2.0, 'p') != []
    assert ag.differences('EBoneControlSpace::BCS_ComponentSpace', 'BCS_ComponentSpace', 'p') == []
    assert ag.differences('None', None, 'p') == [] and ag.differences('None', 'None', 'p') == []
    assert ag.differences('True', 'True', 'p') == []          # a name that happens to read True


def test_an_object_matches_with_or_without_its_class_but_an_asset_is_not_its_class():
    assert ag.differences('/Game/A/PA_X', "/Script/Engine.PhysicsAsset'/Game/A/PA_X.PA_X'", 'p') == []
    assert ag.differences('/Game/A/PA_X.PA_X', "/Script/Engine.PhysicsAsset'/Game/A/PA_X.PA_X'", 'p') == []
    assert ag.differences('/Game/A/PA_X', "/Script/Engine.PhysicsAsset'/Game/A/PA_Y.PA_Y'", 'p') != []
    assert ag.differences('/Game/A/CR.CR_C', "/Script/Engine.BlueprintGeneratedClass'/Game/A/CR.CR'", 'p') != []
    assert ag.differences('/Game/A/CR', '/Game/A/CR.CR_C', 'p') != []


def test_a_faithful_build_has_no_mismatch():
    spec = _spec()
    assert ag.paths(spec) == {'copy': ['SourceBone', 'bCopyTranslation', 'ControlSpace'],
                              'rig': ['ControlRigAssetReference']}
    assert ag.mismatch(spec, _described(spec)) == []
    warned = _described(spec)
    warned['compile_status'] = 'warnings'            # a compiled graph; Build returns the warnings themselves
    assert ag.mismatch(spec, warned) == []


def test_a_value_the_editor_holds_and_the_compiled_class_does_not_is_a_mismatch():
    spec = _spec()
    d = _described(spec)
    _node(d, 'copy')['compiled']['bCopyTranslation'] = False
    assert ag.mismatch(spec, d) == ["compiled copy:bCopyTranslation: 'True' set, False read back"]


def test_a_visible_pin_is_the_evidence_for_its_member_not_the_editor_struct():
    spec = _spec()
    d = _described(spec)
    _node(d, 'copy')['pins'][0]['default'] = 'false'
    assert ag.mismatch(spec, d) == ["pin default copy:bCopyTranslation: 'True' set, 'false' read back"]
    d = _described(spec)
    _node(d, 'copy')['editor']['bCopyTranslation'] = False     # showing a pin can move the value out of the struct
    assert ag.mismatch(spec, d) == []


def test_only_a_folded_constant_can_be_waived_and_only_by_name():
    spec = _spec()
    spec['nodes'].append(ag.node('grip', EVAL, [('Sequence', '/Game/Hero/Jog')]))
    d = _described(_spec())
    d['nodes'].append({'id': 'grip', 'name': 'N_grip', 'class': EVAL, 'pins': [], 'bindings': {},
                       'compiled_node': True, 'editor': {'Sequence': "/Script/Engine.AnimSequence'/Game/Hero/Jog.Jog'"},
                       'compiled': {}, 'unread': {'compiled:Sequence': FOLDED_WHY}})
    assert ag.mismatch(spec, d) == ['compiled grip:Sequence: not read (%s)' % FOLDED_WHY]
    assert ag.mismatch(spec, d, folded_ok=('grip:Sequence',)) == []
    # the same waiver does not cover any other reason a value went unread
    _node(d, 'grip')['unread']['compiled:Sequence'] = 'the node has no compiled counterpart'
    assert ag.mismatch(spec, d, folded_ok=('grip:Sequence',)) == \
        ['compiled grip:Sequence: not read (the node has no compiled counterpart)']


def test_a_node_the_compiler_pruned_is_a_mismatch_even_with_no_property_asked():
    spec = _spec()
    d = _described(spec)
    _node(d, 'l2c')['compiled_node'] = False
    assert ag.mismatch(spec, d) == ['node l2c: not in the compiled class (the output does not reach it)']


def test_links_must_be_exactly_the_specs():
    spec = _spec()
    d = _described(spec)
    d['links'].remove(['in', 'Pose', 'l2c', 'LocalPose'])
    d['links'].append(['in', 'Pose', 'copy', 'ComponentPose'])
    gaps = ag.mismatch(spec, d)
    assert len(gaps) == 1 and "missing [['in', 'Pose', 'l2c', 'LocalPose']]" in gaps[0]
    assert "not in the spec [['in', 'Pose', 'copy', 'ComponentPose']]" in gaps[0]


def test_a_variable_the_host_drives_must_read_back_instance_editable():
    spec = _spec()
    assert all(v['instance_editable'] is True for v in spec['variables'])
    for got in (False, None):
        d = _described(spec)
        d['variables'][0]['instance_editable'] = got
        assert ag.mismatch(spec, d) == ['variable DynamicsAlpha instance-editable: True set, %r read back from the '
                                        'compiled class' % got]


def test_what_the_spec_does_not_declare_is_named_parent_variables_gets_bindings_nodes():
    spec = _spec()
    d = _described(spec)
    d['parent_class'] = '/Game/Other/ABP_Parent.ABP_Parent_C'
    d['variables'].append({'name': 'Stray', 'type': 'bool', 'default': False})
    d['variables'][0]['default'] = 0.0                         # the default lives on the compiled class
    _node(d, 'alpha')['variable'] = 'Stray'
    _node(d, 'rig')['bindings'] = {'Alpha': 'Speed'}
    _node(d, 'rig')['pins'] = []
    d['nodes'] = [n for n in d['nodes'] if n['id'] != 'l2c'] + [{'id': 'stray', 'name': 's', 'class': 'c', 'pins': []}]
    d['compile_status'] = 'error'
    gaps = ag.mismatch(spec, d)
    for want in ('the blueprint is not compiled: status error',
                 'parent class /Game/Other/ABP_Parent.ABP_Parent_C, wanted /Script/Engine.AnimInstance',
                 "variables the spec does not declare: ['Stray']",
                 "variable DynamicsAlpha default: '0.5' set, 0.0 read back",
                 'variable get alpha: a %s of Stray, wanted a get of DynamicsAlpha' % ag.VARIABLE_GET,
                 "nodes the spec does not declare: ['stray']", 'node l2c: missing',
                 "node rig: bound pins ['Alpha']; a spec links, it does not bind",
                 'node rig: DynamicsAlpha is not an exposed pin'):
        assert want in gaps, want


def test_a_failed_describe_is_the_only_difference_reported():
    assert ag.mismatch(_spec(), {'ok': False, 'error': 'no graph'}) == ['describe: no graph']


def test_the_spec_is_plain_json():
    spec = _spec()
    assert json.loads(json.dumps(spec)) == spec
