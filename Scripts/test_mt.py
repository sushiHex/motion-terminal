import os

import pytest

import mt


def _args(*argv):
    out = mt.command(list(argv))
    return os.path.basename(out[0]), out[1:]


def test_each_operation_runs_its_script_with_the_callers_arguments_passed_through():
    assert _args('onboard', 'ParagonAurora', '--resume') == ('hero_onboard.py', ['--hero', 'ParagonAurora', '--resume'])
    assert _args('status', 'ParagonAurora') == ('hero_onboard.py', ['--hero', 'ParagonAurora', '--status'])
    assert _args('build', 'ParagonAurora', '--no-colliders') == ('dynamics_graph.py',
                                                                 ['--hero', 'ParagonAurora', '--no-colliders'])
    assert _args('run', 'ParagonAurora', 's1', '--gate') == ('dyn_run.py', ['--hero', 'ParagonAurora', 's1', '--gate'])
    assert _args('render', 'ParagonAurora', 'captures/x') == ('dyn_render.py', ['--hero', 'ParagonAurora', 'captures/x'])
    assert _args('verify', '--tier', 'pie') == ('run_verification.py', ['--tier', 'pie'])
    assert _args('editor', 'start', '--full') == ('editor_session.py', ['start', '--full'])
    assert _args('doctor') == ('editor_doctor.py', [])
    assert _args('intake', '/Game/Pack') == ('hero_intake.py', ['/Game/Pack'])


def test_every_operation_names_a_script_that_exists_and_the_help_lists_every_operation():
    for name, (script, _fixed, _hero, _editor) in mt.OPERATIONS.items():
        assert os.path.isfile(os.path.join(mt.HERE, script)), script
        assert 'mt.py %s' % name in mt.__doc__, name


def test_a_hero_operation_without_a_hero_and_an_unknown_operation_are_usage_errors(capsys):
    for bad in (['onboard'], ['onboard', '--resume'], ['fly'], [], ['--help']):
        with pytest.raises(mt.Usage):
            mt.command(bad)
    assert mt.main(['fly']) == 2 and 'unknown operation' in capsys.readouterr().out


def test_main_returns_the_scripts_exit_code(monkeypatch):
    seen = []
    monkeypatch.setattr(mt.subprocess, 'call', lambda args: seen.append(args) or 7)
    assert mt.main(['status', 'ParagonAurora']) == 7
    assert seen[0][0] == mt.sys.executable and seen[0][1].endswith('hero_onboard.py')
