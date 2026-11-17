"""usd_chain_weights / usd_section_islands: the output is named from the input's stem whatever the extension's case,
never resolves to the input (opening it for writing would truncate the source export), and a run over a tiny .usda
leaves the input byte-identical."""
import json
import os

import pytest

import usd_chain_weights
import usd_section_islands

MODULES = (usd_chain_weights, usd_section_islands)

CHAIN_USDA = '''uniform token[] joints = ["root", "root/necklace_01"]
def Mesh "LOD0"
{
    int[] primvars:skel:jointIndices = [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    float[] primvars:skel:jointWeights = [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0]
}
'''

ISLAND_USDA = '''uniform token[] joints = ["root", "root/feather_01_l"]
def Mesh "LOD0"
{
    int[] faceVertexCounts = [3]
    int[] faceVertexIndices = [0, 1, 2]
    point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    int[] primvars:skel:jointIndices = [1, 1, 1] (
        elementSize = 1
    )
    float[] primvars:skel:jointWeights = [1, 1, 1] (
        elementSize = 1
    )
    def GeomSubset "Section0"
    {
        int[] indices = [0]
    }
}
'''


@pytest.mark.parametrize('mod', MODULES)
@pytest.mark.parametrize('name', ['Mesh.usda', 'Mesh.USDA', 'Mesh.Usda', 'Mesh.usd'])
def test_output_replaces_the_extension_whatever_its_case(mod, name, tmp_path):
    src = str(tmp_path / name)
    assert mod.output_path(src, '_x.json') == str(tmp_path / 'Mesh_x.json')


@pytest.mark.parametrize('mod', MODULES)
def test_a_directory_named_like_an_extension_is_left_alone(mod, tmp_path):
    src = str(tmp_path / 'a.usda.d' / 'Mesh.usda')
    assert mod.output_path(src, '_x.json') == str(tmp_path / 'a.usda.d' / 'Mesh_x.json')


@pytest.mark.parametrize('mod', MODULES)
def test_an_output_path_equal_to_the_input_is_refused(mod, tmp_path):
    src = tmp_path / 'Mesh.json'
    src.write_text('{}')
    with pytest.raises(SystemExit, match='REFUSING'):
        mod.output_path(str(src), '.json')
    assert src.read_text() == '{}'


@pytest.mark.parametrize('mod', MODULES)
def test_an_output_that_is_the_same_file_as_the_input_is_refused(mod, tmp_path):
    """A different path naming the same file (here a hard link) would still truncate the source."""
    target = tmp_path / 'Mesh_x.json'
    target.write_text('{}')
    src = tmp_path / 'Mesh'
    try:
        os.link(str(target), str(src))
    except (OSError, NotImplementedError) as e:
        pytest.skip('hard links unavailable here: %s' % e)
    with pytest.raises(SystemExit, match='REFUSING'):
        mod.output_path(str(src), '_x.json')


def test_chain_weights_run_on_an_uppercase_extension_keeps_the_input(tmp_path):
    src = tmp_path / 'Mesh.USDA'
    src.write_text(CHAIN_USDA, encoding='utf-8')
    before = src.read_bytes()
    usd_chain_weights.main(['usd_chain_weights.py', str(src)])
    assert src.read_bytes() == before
    out = json.loads((tmp_path / 'Mesh_s2_chain_weights.json').read_text())
    assert out['lods']['LOD0']['vertices'] == 2
    assert out['lods']['LOD0']['chains']['necklace']['weighted_vertices'] == 1


def test_section_islands_run_on_an_uppercase_extension_keeps_the_input(tmp_path):
    src = tmp_path / 'Mesh.USDA'
    src.write_text(ISLAND_USDA, encoding='utf-8')
    before = src.read_bytes()
    usd_section_islands.main(['usd_section_islands.py', str(src), 'Section0'])
    assert src.read_bytes() == before
    out = json.loads((tmp_path / 'Mesh_Section0_islands.json').read_text())
    assert out == [{'verts': 3, 'centroid_usd': [0.3, 0.3, 0.0], 'bones': [['feather_01_l', 1.0]]}]
