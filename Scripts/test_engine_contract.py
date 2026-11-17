"""The committed engine contract is exactly what today's graph specs name.

    python Scripts/test_engine_contract.py --write     # after a spec starts or stops using an engine name

Regenerating is deliberate: the diff of engine_contract.json is the list of engine names the change depends on.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import animgraph_spec as ag  # noqa: E402
import engine_contract as ec  # noqa: E402


def produced():
    """{class: paths} over every graph spec this project builds, each in its fullest form."""
    import test_dynamics_graph as dyn
    specs = [dyn._spec(**case)[2] for case in dyn.CASES]
    return ec.merge(*[ec.from_spec(s) for s in specs])


def test_member_paths_follow_structs_and_arrays_of_structs():
    value = ag.parse_export('((StartBone=(BoneName="a"),bFlag=True),(StartBone=(BoneName="b")))')
    assert ec.member_paths(value, 'Limbs') == {'Limbs', 'Limbs[]', 'Limbs[].StartBone', 'Limbs[].StartBone.BoneName',
                                                'Limbs[].bFlag'}
    assert ec.member_paths('True', 'bOn') == {'bOn'}
    assert ec.member_paths(ag.parse_export('(A=(X=1,Y=2))'), 'P.Q') == {'P.Q', 'P.Q.A', 'P.Q.A.X', 'P.Q.A.Y'}
    assert ec.member_paths(ag.parse_export('(1,2,3)'), 'V') == {'V'}        # an array of atoms names no member


def test_a_spec_gives_its_classes_property_members_and_struct_backed_pins_but_not_exposed_pins():
    spec = ag.spec('/Game/S', [
        ag.node('in', '/Script/M.In'),
        ag.node('layer', '/Script/M.Layer', [('LayerSetup', '((BranchFilters=((BoneName="b",BlendDepth=0))))')]),
        ag.node('rig', '/Script/M.Rig', [('Ref', '(Class="/Game/C.C_C")')], exposed=['Alpha2'])],
        [('in', 'Pose', 'layer', 'BasePose'), ('in', 'Pose', 'layer', 'BlendPoses_0'),
         ('layer', 'Pose', 'rig', 'Source'), ('get', 'Alpha2', 'rig', 'Alpha2')],
        ('rig', 'Pose'), variable_gets=[('get', 'Alpha2')])
    got = ec.from_spec(spec)
    assert got['/Script/M.In'] == set()
    assert got['/Script/M.Layer'] == {'LayerSetup', 'LayerSetup[]', 'LayerSetup[].BranchFilters',
                                     'LayerSetup[].BranchFilters[]', 'LayerSetup[].BranchFilters[].BoneName',
                                     'LayerSetup[].BranchFilters[].BlendDepth', 'BasePose', 'BlendPoses[]'}
    assert got['/Script/M.Rig'] == {'Ref', 'Ref.Class', 'Source'}


def test_the_committed_contract_is_what_the_specs_produce():
    want = ec.document(produced())
    assert ec.read() == want, 'engine_contract.json is stale: python Scripts/test_engine_contract.py --write'
    # it covers the graph the project actually builds, not a trivial one
    nodes = want['anim_nodes']
    assert len(nodes) >= 9 and sum(len(p) for p in nodes.values()) >= 40


if __name__ == '__main__':
    if '--write' not in sys.argv:
        raise SystemExit(__doc__)
    ec.write(produced())
    print('wrote %s' % ec.PATH)
