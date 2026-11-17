"""dyn_behaviours: the library loads and is consistent, every part resolves by the documented rule, and the switch from
the old per-preset table changed nothing for the parts it already covered."""
import json

import pytest

import dyn_behaviours as bh

# The cloth.flap row of the table dynamics_rig.py used before behaviours, merged '<preset>' then
# '<preset>.<kind>' exactly as its native_for did. Frozen here: the behaviour that replaced it must give the part the
# same values.
FEATHER = {'GravityMultiplier': 0.0, 'Strength': 2.5, 'DampingRatio': 0.15, 'AngleLimit': 45.0, 'AngleLimitStrength': 3.0}


def test_switch_keeps_the_settings_of_every_part_the_old_table_covered():
    lib = bh.load()
    assert lib['behaviours'][bh.resolve({'preset': 'cloth', 'kind': 'flap'}, lib)]['native'] == FEATHER


# Retuned on purpose (heavier cloth, after a visual check): the old cloth row changed, so it is pinned to its new
# values here instead of to the old table.
HEAVY = {'AngleLimit': 30.0, 'AngleLimitStrength': 3.0, 'Mass': 8.0, 'bScaleDampingByInverseMass': True,
         'DampingRatio': 0.2}


def test_cloth_chains_are_heavy_and_strips_add_only_the_twist():
    lib = bh.load()
    assert lib['behaviours'][bh.resolve({'preset': 'cloth', 'kind': 'chain'}, lib)]['native'] == HEAVY
    assert lib['behaviours']['cloth_strip']['native'] == HEAVY
    assert lib['behaviours']['cloth_strip']['twist'] == {'TwistGain': 1.0, 'TwistMax': 45.0}


def test_hanging_cords_were_loosened():
    # Retuned on purpose (a visual check found the axe cords too elastic and too damped): 7.0 -> 4.5 /s of drag
    # (Damping is now the whole drag; it used to be 2.0 on top of the solver's 2.5).
    lib = bh.load()
    assert lib['behaviours'][bh.resolve({'preset': 'tail', 'kind': 'chain'}, lib)]['native'] == {
        'Strength': 0.25, 'AngleLimitStrength': 0.0, 'Damping': 4.5}


def test_twist_entry_is_validated():
    with pytest.raises(ValueError, match='twist'):
        bh.load({'new_strip': {'twist': {'TwistGain': 1.0}}})      # overrides merge, so a new behaviour shows the gap
    with pytest.raises(ValueError, match='twist'):
        bh.load({'cloth_strip': {'twist': {'TwistMax': 'big'}}})
    for bad in ({'TwistMax': -45.0}, {'TwistMax': float('inf')}, {'TwistGain': float('nan')}):
        with pytest.raises(ValueError, match='twist'):
            bh.load({'cloth_strip': {'twist': bad}})
    lib = bh.load({'cloth_strip': {'twist': {'TwistGain': -2.5}}})          # negative and over-cranked are allowed
    assert lib['behaviours']['cloth_strip']['twist'] == {'TwistGain': -2.5, 'TwistMax': 45.0}


def test_each_part_keeps_its_drag_and_soft_tissue_has_none():
    # Damping is the particle's whole drag (the rig default AIR_DAMPING_HZ 2.5 when unset): every part keeps the total
    # it had when the solver added 2.5 to its own, soft tissue none
    native = {name: b['native'].get('Damping') for name, b in bh.load()['behaviours'].items()}
    assert native == {'default': None, 'cloth_panel': None, 'cloth_strip': None, 'feather': None, 'hanging_cord': 4.5,
                      'hair': None, 'soft_tissue': 0.0, 'pendant': 2.5}
    with pytest.raises(ValueError, match='unknown entries'):
        bh.load({'hair': {'air_drag': False}})


def test_feather_keeps_its_shipped_flutter():
    lib = bh.load()
    assert lib['behaviours'][bh.resolve({'preset': 'cloth', 'kind': 'flap'}, lib)]['flutter'] == {
        'FlutterRate': 6.0, 'FlutterAmbient': 0.0, 'FlutterSpeedGain': 5.0}


def test_resolution_order_kind_then_preset_then_star():
    lib = bh.load()
    assert bh.resolve({'preset': 'cloth', 'kind': 'flap'}, lib) == 'feather'
    assert bh.resolve({'preset': 'cloth', 'kind': 'chain'}, lib) == 'cloth_panel'
    assert bh.resolve({'preset': 'strap', 'kind': 'flap'}, lib) == 'pendant'
    assert bh.resolve({'preset': 'hair', 'kind': 'chain'}, lib) == 'hair'
    assert bh.resolve({'preset': 'soft', 'kind': 'flap'}, lib) == 'soft_tissue'
    assert bh.resolve({'preset': 'tail', 'kind': 'flap'}, lib) == 'hanging_cord'
    assert bh.resolve({'preset': 'gizmo', 'kind': 'chain'}, lib) == 'default'     # a preset no rule names
    assert bh.resolve({'preset': None, 'kind': 'flap'}, lib) == 'default'


def test_chain_curves_are_validated():
    ok = bh.load({'hair': {'chain_curves': {'AngleLimitMultiplier': [[0.0, 0.1], [1.0, 1.0]]}}})
    assert ok['behaviours']['hair']['chain_curves']['AngleLimitMultiplier'][0] == [0.0, 0.1]
    for bad in ({'NotACurve': [[0, 1], [1, 1]]}, {'StrengthMultiplier': [[0.0, 1.0]]},
                {'StrengthMultiplier': [[0.5, 1.0], [0.2, 1.0]]}, {'StrengthMultiplier': [[0.0, -1.0], [1.0, 1.0]]},
                {'StrengthMultiplier': [[0.0, 1.0], [1.5, 1.0]]}):
        with pytest.raises(ValueError, match='chain_curves'):
            bh.load({'hair': {'chain_curves': bad}})


def test_deforms_host_must_be_a_bool():
    with pytest.raises(ValueError, match='deforms_host'):
        bh.load({'soft_tissue': {'deforms_host': 1}})


def test_profile_override_wins_and_must_name_a_behaviour():
    lib = bh.load()
    part = {'id': 'f1', 'preset': 'cloth', 'kind': 'flap'}
    assert bh.resolve(part, lib, {'f1': 'pendant'}) == 'pendant'
    assert bh.resolve(part, lib, {'other': 'pendant'}) == 'feather'                  # a pick for another part only
    with pytest.raises(ValueError):
        bh.resolve(part, lib, {'f1': 'nope'})


@pytest.mark.parametrize('native,err', [({'Radius': 2.0}, 'not a settable'), ({'Strenght': 1.0}, 'not a settable'),
                                        ({'Strength': 'stiff'}, 'not a float'), ({'bAccelerationMode': 1}, 'not a bool')])
def test_native_fields_are_checked_by_name_and_type(native, err):
    with pytest.raises(ValueError, match=err):
        bh.load({'pendant': {'native': native}})


def test_flutter_needs_all_three_inputs():
    with pytest.raises(ValueError, match='flutter'):
        bh.load({'pendant': {'flutter': {'FlutterRate': 4.0}}})


def test_override_merges_field_by_field_and_may_add_a_behaviour():
    lib = bh.load({'pendant': {'native': {'Strength': 0.5}}, 'hair_strand': {'native': {'Strength': 0.4}}})
    assert lib['behaviours']['pendant']['native']['Strength'] == 0.5
    assert lib['behaviours']['pendant']['native']['AngleLimit'] == 35.0          # the rest of the entry kept
    assert lib['behaviours']['hair_strand']['native'] == {'Strength': 0.4}
    assert bh.load()['behaviours']['pendant']['native']['Strength'] == 0.25      # the file is never changed


@pytest.mark.parametrize('doc,err', [
    ({'behaviours': {'a': {'native': {}}}, 'assign': {'*': 'missing'}}, 'unknown behaviours'),
    ({'behaviours': {'a': {'native': {}}}, 'assign': {'x': 'a'}}, "'*'"),
    ({'behaviours': {'a': {'nativ': {}}}, 'assign': {'*': 'a'}}, 'unknown entries'),
])
def test_a_broken_library_fails_loudly(tmp_path, doc, err):
    p = tmp_path / 'lib.json'
    p.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match=err):
        bh.load(path=str(p))


def test_library_names_no_character():
    text = open(bh.PATH).read()
    blob = json.loads(text)
    keys = json.dumps({'behaviours': list(blob['behaviours']), 'assign': blob['assign']})
    for hero in ('Khaimera', 'Aurora', 'Paragon'):
        assert hero not in keys                    # only the free-text 'tuned' notes may say where a setting came from


def test_garment_settings_layer_library_profile_then_override(tmp_path):
    lib = tmp_path / 'lib.json'
    lib.write_text(json.dumps({'garment': {'iterations': 4, 'damping': 0.1, 'local_damping': 0.2, 'tuned': 'x'}}))
    prof = {'secondary': {'garment': {'iterations': 6}}}
    assert bh.garment(prof, {'damping': 0.3, 'local_damping': 0.0}, path=str(lib)) == {
        'iterations': 6, 'damping': 0.3, 'local_damping': 0.0, 'tuned': 'x'}
    assert bh.garment(path=str(lib))['iterations'] == 4


@pytest.mark.parametrize('bad', [{'iterations': 2.5}, {'iterations': -1}, {'damping': 0.1}, {'stiffness': 1.0},
                                 {'iterations': True}])
def test_garment_settings_refuse_what_the_drive_cannot_set(tmp_path, bad):
    lib = tmp_path / 'lib.json'
    lib.write_text(json.dumps({}))
    with pytest.raises(ValueError):
        bh.garment(None, bad, path=str(lib))
