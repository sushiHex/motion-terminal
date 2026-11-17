"""The release pipeline's host-side pieces: the hero list, the verdict summary, the reaction cut and the page build."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import breakdown_page  # noqa: E402
import dyn_hero  # noqa: E402
import hit_reaction_analyze  # noqa: E402
import hit_reaction_release  # noqa: E402
import hit_reaction_sweep  # noqa: E402
import dyn_overlay  # noqa: E402


def test_heroes_arg_reads_every_hero_in_order_and_keeps_the_rest():
    rigs, rest = dyn_hero.heroes_arg(['--hero', 'A', 'cands.json', '--hero=B', '--strength=9'])
    assert rigs == ['A', 'B'] and rest == ['cands.json', '--strength=9']


def test_heroes_arg_refuses_a_hero_flag_without_a_name():
    with pytest.raises(ValueError):
        dyn_hero.heroes_arg(['--hero', '--strength=9'])


def test_heroes_arg_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv(dyn_hero.ENV, 'C')
    assert dyn_hero.heroes_arg(['x']) == (['C'], ['x'])


def _verdict():
    hit = {'peak_cm': 35.64, 'peak_deg': 69.4, 'at_fade_cm': 6.21, 'last_peak_cm': 30.08, 'return_s': 1.117,
           'legs_cm': 0.0001, 'legs_deg': 0.195, 'secondary_moved': 14, 'secondary_parts': 14,
           'impacts': [{'jump_cm': 14.04}, {'jump_cm': 2.36}],
           'delivery': {'applied': 2, 'planned': 2, 'late_s': [0.0, 1 / 60.0]}}
    return {'hit': hit, 'reaction_off': {'peak_cm': 0.0}, 'no_impulse': {'peak_cm': 0.04},
            'fidelity': {'max_cm': 0.739, 'max_deg': 0.84, 'worst_bone': 'hand_l'},
            'articulation': {'sides': {'l': {'clavicle': 20.2, 'shoulder': 40.8, 'elbow': 15.6, 'wrist': 6.3},
                                       'r': {'clavicle': 14.6, 'shoulder': 41.9, 'elbow': 25.2, 'wrist': 17.2}},
                             'hips_deg': 12.05},
            'penetration': {'excess': {'a|b': -3.2, 'c|d': 0.648}},
            'verdict': {'pass': True, 'rows': [{'ok': True}, {'ok': True}]}}


def test_summary_reports_each_joint_by_its_larger_side_and_the_worst_excess():
    s = hit_reaction_analyze.summary(_verdict())
    assert (s['clavicle_deg'], s['shoulder_deg'], s['elbow_deg'], s['wrist_deg']) == (20, 42, 25, 17)
    assert s['penetration_cm'] == 0.6 and s['impacts_cm'] == [14.0, 2.4] and s['late_frames'] == 1
    assert (s['checks_passed'], s['checks'], s['pass']) == (2, 2, True)


def test_summary_penetration_is_zero_when_every_pair_stays_clear():
    v = _verdict()
    v['penetration']['excess'] = {'a|b': -1.0}
    assert hit_reaction_analyze.summary(v)['penetration_cm'] == 0.0


def _record():
    frames = [{'f': i, 't': round(i / 60.0, 5)} for i in range(600)]
    return {'frames': frames, 'config': {'reaction_schedule': {'arm_s': 0.05, 'hold_s': 1.0, 'fade_s': 0.3}},
            'events': {'hits': [{'frame': 120, 't': 2.0}, {'frame': 144, 't': 2.4}]}}


def test_reaction_cut_spans_the_lead_to_the_tail_after_the_fade_in_rendered_frames():
    a, b = dyn_overlay.reaction_cut(_record(), offset=-2)
    assert a == round((2.0 - dyn_overlay.CUT_LEAD_S) * 60) - 2
    assert b == round((2.4 + 1.0 + 0.3 + dyn_overlay.CUT_TAIL_S) * 60) - 2


def test_reaction_cut_stops_at_the_record_end():
    rec = _record()
    rec['frames'] = rec['frames'][:200]
    assert dyn_overlay.reaction_cut(rec, offset=0)[1] == 199


def _release(tmp_path):
    take = tmp_path / 'aurora' / 'take.mp4'
    take.parent.mkdir(exist_ok=True)
    take.write_bytes(b'v')
    return {'ParagonAurora': {'status': 'PASS', 'summary': {'peak_cm': 35.6, 'impacts_cm': [14.0, 2.4], 'pass': True},
                              'media': {'volley': 'aurora/take.mp4'}}}


def test_build_takes_nothing_from_an_entry_whose_latest_release_did_not_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(breakdown_page.hit_reaction_release, 'OUT', str(tmp_path))
    for status in ('RUNNING', 'ERROR', 'FAIL'):
        rel = _release(tmp_path)
        rel['ParagonAurora']['status'] = status       # an older run's summary and media still in the entry
        _html, missing = breakdown_page.build('{{ParagonAurora.peak_cm}} {{ParagonAurora.media.volley}}', rel,
                                              str(tmp_path))
        assert missing == ['ParagonAurora.peak_cm', 'ParagonAurora.media.volley'], status


def _take(**kw):
    rec = {'config': {'hits': [{}, {}]}, 'events': {'hits': [{'t': 1.0}, {'t': 1.4}]}}
    rec.update(kw)
    return rec


def test_a_take_whose_volley_is_not_the_matrix_volley_is_refused():
    # twice the strength passed every check on its own
    matrix = _take(config={'hits': [{'bone': 'head', 'strength': 2000.0}, {'bone': 'pelvis', 'strength': 2000.0}]})
    same = _take(config={'hits': [{'bone': 'head', 'strength': 2000.0}, {'bone': 'pelvis', 'strength': 2000.0}]})
    harder = _take(config={'hits': [{'bone': 'head', 'strength': 4000.0}, {'bone': 'pelvis', 'strength': 4000.0}]})
    frames = ['frame_0001.png']
    assert hit_reaction_release.take_problems(same, 10.0, 5.0, frames, matrix) == []
    assert any('volley' in p for p in hit_reaction_release.take_problems(harder, 10.0, 5.0, frames, matrix))


def test_a_fresh_complete_take_has_no_problems():
    assert hit_reaction_release.take_problems(_take(), 10.0, 5.0, ['frame_0007.png', 'frame_0008.png']) == []


@pytest.mark.parametrize('rec, written, names, why', [
    (_take(), 4.0, ['frame_0001.png'], 'predates'),
    (_take(error='boom'), 10.0, ['frame_0001.png'], 'failed'),
    (_take(events={'hits': [{'t': 1.0}]}), 10.0, ['frame_0001.png'], '1 of 2'),
    (_take(config={}), 10.0, ['frame_0001.png'], '2 of 0'),
    (_take(), 10.0, ['frame_0001.png', 'frame_0003.png'], 'contiguous'),
    (_take(), 10.0, [], 'contiguous'),
])
def test_a_take_that_is_not_the_passed_volley_is_refused(rec, written, names, why):
    assert any(why in p for p in hit_reaction_release.take_problems(rec, written, 5.0, names))


def test_build_resolves_figures_lists_and_media(tmp_path, monkeypatch):
    monkeypatch.setattr(breakdown_page.hit_reaction_release, 'OUT', str(tmp_path))
    out = tmp_path / 'page'
    out.mkdir()
    html, missing = breakdown_page.build('{{ParagonAurora.peak_cm}} | {{ ParagonAurora.impacts_cm }} | '
                                         '{{ParagonAurora.media.volley}}', _release(tmp_path), str(out))
    assert missing == [] and html == '35.6 | 14.0 · 2.4 | aurora_volley.mp4'
    assert (out / 'aurora_volley.mp4').read_bytes() == b'v'


def test_build_names_every_unresolved_token(tmp_path, monkeypatch):
    monkeypatch.setattr(breakdown_page.hit_reaction_release, 'OUT', str(tmp_path))
    rel = _release(tmp_path)
    rel['ParagonKhaimera'] = {'verdict': {}}            # a failed release: no summary, no media
    _html, missing = breakdown_page.build('{{ParagonAurora.nope}} {{ParagonKhaimera.peak_cm}} '
                                          '{{ParagonKhaimera.media.volley}} {{file:no/such.mp4}}', rel, str(tmp_path))
    assert missing == ['ParagonAurora.nope', 'ParagonKhaimera.peak_cm', 'ParagonKhaimera.media.volley',
                       'file:no/such.mp4']


def test_build_refuses_two_files_under_one_published_name(tmp_path, monkeypatch):
    monkeypatch.setattr(breakdown_page, 'REPO', str(tmp_path))
    for d in ('a', 'b'):
        (tmp_path / d).mkdir()
        (tmp_path / d / 'x.mp4').write_bytes(d.encode())
    out = tmp_path / 'page'
    out.mkdir()
    with pytest.raises(ValueError):
        breakdown_page.build('{{file:a/x.mp4}} {{file:b/x.mp4}}', {}, str(out))


def test_a_sweep_row_from_records_that_failed_validation_is_a_failed_row():
    r = hit_reaction_sweep.row({'verdict': {'pass': False, 'rows': [{'check': 'records valid', 'ok': False}]},
                                'problems': ['no frames']})
    assert not r['pass'] and r['failed'] == ['records valid', 'no frames'] and r['hit'] == {}


def test_a_stalled_drive_is_a_printed_failed_row_and_the_sweep_goes_on(monkeypatch, capsys):
    monkeypatch.setattr(hit_reaction_sweep, 'build', lambda rig, over=None: None)

    def run(rig, tag, strength, impacts=None):
        if tag == 'a':
            raise TimeoutError('timed out')
        return {'verdict': {'pass': True, 'rows': [{'check': 'x', 'ok': True}]}, 'hit': {'impacts': []},
                'fidelity': {}, 'articulation': {'sides': {'l': {'clavicle': 1, 'shoulder': 9, 'elbow': 6,
                                                                 'wrist': 5}}}}
    monkeypatch.setattr(hit_reaction_sweep.hit_reaction_run, 'run', run)
    rows, winners = hit_reaction_sweep.main(['H'], {'a': {}, 'b': {}}, 1.0)
    out = capsys.readouterr().out
    assert winners == ['b'] and not rows['a', 'H']['pass'] and 'timed out' in out and "'l': [1, 9, 6, 5]" in out


def test_an_unmeasured_impact_prints_as_none(capsys):
    r = hit_reaction_sweep.row({'verdict': {'pass': False, 'rows': []}, 'hit': {'impacts': [{'jump_cm': None}]},
                                'fidelity': {}, 'articulation': {'sides': {'l': {'clavicle': None}}}})
    hit_reaction_sweep.print_row('t', 'H', {}, r)
    assert 'impacts [None] cm' in capsys.readouterr().out


def test_a_timed_out_drive_is_retried_under_its_own_tag(monkeypatch):
    import hit_reaction_run
    tags = []

    def run(rig, tag, alphas, extra):
        tags.append(tag)
        if tag == 'x_hit':
            raise TimeoutError('timed out')
        return {'path': tag + '.json'}
    monkeypatch.setattr(hit_reaction_run, 'dyn_hero', type('D', (), {'resolve': staticmethod(
        lambda rig: {'profile': {'roles': {}}})}))
    monkeypatch.setattr(hit_reaction_run, '_reaction_spec', lambda h: {})
    monkeypatch.setattr(hit_reaction_run, 'volley', lambda *a: [])
    monkeypatch.setattr(hit_reaction_run, '_extra', lambda *a: {})
    monkeypatch.setattr(hit_reaction_run.dyn_run, 'run', run)
    monkeypatch.setattr(hit_reaction_run.hit_reaction_analyze, 'main', lambda paths: {'verdict': {'pass': True}})
    out = hit_reaction_run.run('R', 'x')
    assert tags == ['x_hit', 'x_hit_retry', 'x_off', 'x_noimp']
    assert out['records'] == ['x_hit_retry.json', 'x_off.json', 'x_noimp.json']


def test_a_page_mixing_two_generators_is_not_built(tmp_path, monkeypatch):
    monkeypatch.setattr(breakdown_page.hit_reaction_release, 'OUT', str(tmp_path))
    rel = _release(tmp_path)
    rel['ParagonAurora']['generator'] = 'aaa'
    rel['ParagonKhaimera'] = dict(rel['ParagonAurora'], generator='bbb')
    _html, missing = breakdown_page.build('{{ParagonAurora.peak_cm}} {{ParagonKhaimera.peak_cm}}', rel, str(tmp_path))
    assert any(m.startswith('one generator') for m in missing)
    rel['ParagonKhaimera']['generator'] = 'aaa'
    assert breakdown_page.build('{{ParagonAurora.peak_cm}} {{ParagonKhaimera.peak_cm}}', rel, str(tmp_path))[1] == []


def test_the_shipped_build_clears_every_experiment_override(monkeypatch):
    seen = {}

    class Done:
        returncode = 0

    def fake_run(cmd, env, **kw):
        seen.update(env)
        return Done()
    for k in ('KDYN_REACTION_JSON', 'KDYN_BEHAVIOURS_JSON', 'KDYN_AIR_JSON'):
        monkeypatch.setenv(k, '{}')
    monkeypatch.setattr(hit_reaction_sweep.subprocess, 'run', fake_run)
    hit_reaction_sweep.build('X')
    assert not [k for k in seen if k.startswith('KDYN_')]
    hit_reaction_sweep.build('X', {'strength': 3})
    assert [k for k in seen if k.startswith('KDYN_')] == ['KDYN_REACTION_JSON']


def test_the_shipped_template_names_only_known_summary_keys():
    keys = set(hit_reaction_analyze.summary(_verdict())) | set(hit_reaction_release.release_figures(
        {'bodies': [{'physics_type': 'simulated'}]}, {'checks': 1, 'failed': []}))
    template = open(breakdown_page.TEMPLATE, encoding='utf-8').read()
    used = {t.split('.', 1)[1] for t in breakdown_page.TOKEN.findall(template)
            if '.' in t and not t.startswith('file:') and '.media.' not in t}
    assert used and used <= keys, sorted(used - keys)


def test_only_the_dynamics_takes_the_page_shows_are_rendered(tmp_path):
    page = tmp_path / 'page.html'
    page.write_text('{{A.media.dynamics_heat}} {{ A.media.volley }} {{B.media.dynamics}} {{AB.media.dynamics}}')
    assert hit_reaction_release.page_media('A', str(page)) == {'dynamics_heat', 'volley'}
    assert hit_reaction_release.page_media('B', str(page)) == {'dynamics'}
    shipped = {r: hit_reaction_release.page_media(r) for r in ('ParagonAurora', 'ParagonKhaimera')}
    assert 'dynamics_heat' in shipped['ParagonAurora'] and 'dynamics_overlay' in shipped['ParagonKhaimera']
    # the lineup: every hero renders its run strip, and a page without one renders none
    assert all('run' in s for s in shipped.values()) and 'run' not in hit_reaction_release.page_media('A', str(page))
    page.write_text('{{lineup}}')
    assert hit_reaction_release.page_media('Any', str(page)) == {'run'}


def test_the_lineup_is_every_released_hero_with_a_strip_and_needs_two(tmp_path, monkeypatch):
    monkeypatch.setattr(hit_reaction_release, 'OUT', str(tmp_path))
    (tmp_path / 'a.mp4').write_bytes(b'x')
    (tmp_path / 'b.mp4').write_bytes(b'x')
    rel = {'B': {'status': 'PASS', 'media': {'run': 'b.mp4'}}, 'A': {'status': 'PASS', 'media': {'run': 'a.mp4'}},
           'C': {'status': 'FAIL', 'media': {'run': 'a.mp4'}}, 'D': {'status': 'PASS', 'media': {'run': 'gone.mp4'}},
           'E': {'status': 'PASS', 'media': {}}}
    assert breakdown_page.lineup_heroes(rel) == ['A', 'B']
    assert breakdown_page.lineup({'A': rel['A']}, str(tmp_path)) is None           # one hero is not a lineup
