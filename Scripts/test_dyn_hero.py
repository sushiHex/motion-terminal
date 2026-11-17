"""dyn_hero: a hero's dynamics assets come from its profile and the naming convention, never from code."""
import json

import pytest

import dyn_hero


def _profile(tmp_path, rig='ParagonTestHero', mesh='/Game/X/Meshes/Hero', skel='/Game/X/Meshes/Hero_Skeleton'):
    p = tmp_path / (rig + '.json')
    p.write_text(json.dumps({'status': 'ok', 'primary': {'mesh': mesh, 'skeleton': skel}}))
    return str(p)


def test_assets_follow_the_convention_and_the_profile(tmp_path):
    h = dyn_hero.resolve('ParagonTestHero', _profile(tmp_path), str(tmp_path / 'none.json'))
    assert h['short'] == 'TestHero'
    assert h['cr_path'] == '/Game/MT/TestHero/CR_MT_TestHeroDynamics'
    assert h['abp_class'] == '/Game/MT/TestHero/ABP_MT_TestHeroDynamics.ABP_MT_TestHeroDynamics_C'
    assert h['pawn_class'] == '/Game/MT/TestHero/BP_MT_TestPawn_TestHero.BP_MT_TestPawn_TestHero_C'
    assert h['anim_class'] == '/Game/MT/TestHero/InPlace/ABP_MT_TestHero_InPlace.ABP_MT_TestHero_InPlace_C'
    assert h['solver_component'] == 'MT_TestHeroDynamicsSolver'
    assert (h['mesh'], h['skeleton']) == ('/Game/X/Meshes/Hero', '/Game/X/Meshes/Hero_Skeleton')
    assert h['grip'] is None and h['solver_settings'] == {}
    assert h['clip_prefix'] == 'TestHero_'
    assert h['bs'] == '/Game/MT/TestHero/TestHero_BS_Locomotion_2D'
    assert h['bs_inplace'] == '/Game/MT/TestHero/InPlace/TestHero_BS_Locomotion_2D_InPlace'
    assert h['ik_rig'] == '/Game/MT/TestHero/Retarget/IK_MT_TestHero'
    assert h['retargeter'] == '/Game/MT/TestHero/Retarget/RTG_MT_Manny_to_TestHero'
    assert h['abp_main_class'] == '/Game/MT/TestHero/ABP_MT_TestHero.ABP_MT_TestHero_C'
    assert (h['retarget_dir'], h['inplace_dir']) == ('/Game/MT/TestHero/Retargeted/', '/Game/MT/TestHero/InPlace/')
    assert dyn_hero.record_path(h, 's1').endswith('dyn_ParagonTestHero_s1.json')
    assert h['manifest'].endswith('dyn_ParagonTestHero_build.json')


def test_colliding_asset_namespaces_are_refused(tmp_path):
    for rig in ('ParagonAurora', 'Aurora'):
        _profile(tmp_path, rig)
    with pytest.raises(ValueError, match='share the asset namespace'):
        dyn_hero.check_unique_shorts(str(tmp_path))
    assert dyn_hero.check_unique_shorts()['Khaimera'] == 'ParagonKhaimera'     # the shipped profiles are unique


def test_shipped_profiles_keep_their_existing_asset_names():
    # Every shipped profile resolves; Khaimera's convention names are the assets already in Content.
    k = dyn_hero.resolve('ParagonKhaimera')
    assert k['cr_path'] == '/Game/MT/Khaimera/CR_MT_KhaimeraDynamics'
    assert k['pawn_class'] == '/Game/MT/Khaimera/BP_MT_TestPawn_Khaimera.BP_MT_TestPawn_Khaimera_C'
    assert k['grip'] and k['grip']['hands']
    assert k['bs'] == '/Game/MT/Khaimera/Khai_BS_Locomotion_2D'                  # his legacy prefix, from his extras
    assert k['bs_inplace'] == '/Game/MT/Khaimera/InPlace/Khai_BS_Locomotion_2D_InPlace'
    assert k['abp_main'] == '/Game/MT/Khaimera/ABP_MT_Khaimera'
    for rig in ('ParagonAurora', 'ParagonIggyScorch', 'ParagonPhase'):
        assert dyn_hero.resolve(rig)['short'] == rig[len('Paragon'):]


def test_a_missing_hero_is_an_error_not_a_default(monkeypatch, tmp_path):
    monkeypatch.delenv(dyn_hero.ENV, raising=False)
    with pytest.raises(ValueError, match='no hero'):
        dyn_hero.resolve()
    monkeypatch.setenv(dyn_hero.ENV, 'ParagonTestHero')
    assert dyn_hero.hero_name() == 'ParagonTestHero'
    with pytest.raises(ValueError, match='hero_intake'):
        dyn_hero.resolve('ParagonNobody', str(tmp_path / 'ParagonNobody.json'))


def test_hero_arg_parses_and_refuses_an_empty_value(monkeypatch):
    monkeypatch.setenv(dyn_hero.ENV, 'ParagonEnvHero')
    assert dyn_hero.hero_arg(['--hero', 'ParagonA', 'x.json']) == ('ParagonA', ['x.json'])
    assert dyn_hero.hero_arg(['x.json', '--hero=ParagonB']) == ('ParagonB', ['x.json'])
    assert dyn_hero.hero_arg(['x.json']) == ('ParagonEnvHero', ['x.json'])     # no flag: the environment
    for bad in (['x.json', '--hero'], ['--hero=', 'x.json'], ['--hero', '--gate']):
        with pytest.raises(ValueError, match='needs a rig'):
            dyn_hero.hero_arg(bad)


def test_non_paragon_names_and_bad_names():
    assert dyn_hero.short_name('MixamoYBot') == 'MixamoYBot'
    assert dyn_hero.short_name('Paragonish') == 'Paragonish'       # only the 'Paragon' + capital prefix is dropped
    with pytest.raises(ValueError):
        dyn_hero.short_name('Paragon Hero')


def test_extras_are_validated(tmp_path):
    prof = _profile(tmp_path)
    good = tmp_path / 'good.json'
    good.write_text(json.dumps({'grip': {'pose': '/Game/A/Jog', 'hands': ['hand_l'], 'why': 'open hands'}}))
    assert dyn_hero.resolve('ParagonTestHero', prof, str(good))['grip']['pose'] == '/Game/A/Jog'
    for bad, msg in (({'camera': {}}, 'unknown'), ({'grip': {'pose': 'x', 'why': 'y'}}, 'exactly'),
                     ({'grip': {'pose': 'x', 'hands': [], 'why': ' '}}, 'why')):
        p = tmp_path / 'bad.json'
        p.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match=msg):
            dyn_hero.resolve('ParagonTestHero', prof, str(p))


def test_a_render_never_replaces_a_gated_runs_record(tmp_path):
    # A review render that reused a gated run's tag replaced the run's record: the gate's evidence was gone.
    path = tmp_path / 'dyn_X_t.json'
    run, render = {'fixed_hz': 60.0}, {'fixed_hz': None}
    assert dyn_hero.overwrite_refusal(str(path), render) is None          # nothing there yet
    path.write_text(json.dumps({'config': run}))
    assert 'fixed 60' in dyn_hero.overwrite_refusal(str(path), render)
    assert dyn_hero.overwrite_refusal(str(path), {'fixed_hz': 60.0}) is None   # a rerun of the same kind may
    path.write_text(json.dumps({'config': render}))
    assert 'mrq' in dyn_hero.overwrite_refusal(str(path), run)
    path.write_text('{not json')
    assert 'cannot be read' in dyn_hero.overwrite_refusal(str(path), run)
