import os

import pytest

import asset_pipeline as ap


def _kind(log, gaps=(), fail=None):
    def step(name, result=None):
        def run(*_a):
            log.append(name)
            if fail == name:
                raise RuntimeError(name + ' failed')
            return result
        return run
    return ap.Kind('thing', build=step('build'), describe=step('describe', {'seen': 1}),
                   mismatch=lambda spec, described: (log.append('mismatch'), list(gaps))[1], save=step('save'))


def _run(kind, log, **kw):
    return ap.realize('ops', kind, '/Game/MT/X/A', {'b': 2, 'a': 1},
                      delete=lambda ops, path: log.append('delete'),
                      ignored=lambda path: log.append('ignored'), **kw)


def test_an_accepted_asset_goes_through_every_step_in_order_and_is_recorded():
    log = []
    record = _run(_kind(log), log)
    assert log == ['ignored', 'delete', 'build', 'describe', 'mismatch', 'save']
    assert record == {'kind': 'thing', 'path': '/Game/MT/X/A', 'spec_digest': ap.digest({'a': 1, 'b': 2}),
                      'described': {'seen': 1}}


def test_a_description_that_is_not_the_spec_is_never_saved_and_is_deleted():
    log = []
    with pytest.raises(ap.NotTheSpec) as caught:
        _run(_kind(log, gaps=['x differs', 'y missing']), log)
    assert log == ['ignored', 'delete', 'build', 'describe', 'mismatch', 'delete']
    assert caught.value.gaps == ['x differs', 'y missing'] and 'not saved' in str(caught.value)


@pytest.mark.parametrize('step, after', [('build', ['build']), ('describe', ['build', 'describe']),
                                         ('save', ['build', 'describe', 'mismatch', 'save'])])
def test_any_failing_step_deletes_what_it_left_and_raises_its_own_error(step, after):
    log = []
    with pytest.raises(RuntimeError, match=step + ' failed'):
        _run(_kind(log, fail=step), log)
    assert log == ['ignored', 'delete'] + after + ['delete']


def test_a_cleanup_that_fails_does_not_hide_the_failure_it_follows(capsys):
    log, deletes = [], []

    def delete(ops, path):
        deletes.append(path)
        if len(deletes) == 2:
            raise RuntimeError('editor gone')
    with pytest.raises(RuntimeError, match='build failed'):
        ap.realize('ops', _kind(log, fail='build'), '/Game/MT/X/A', {}, delete=delete, ignored=lambda path: None)
    assert 'was not deleted: editor gone' in capsys.readouterr().out


def test_a_target_git_would_track_is_refused_before_anything_is_deleted_or_built():
    log = []

    def tracked(path):
        raise RuntimeError('REFUSING')
    with pytest.raises(RuntimeError, match='REFUSING'):
        ap.realize('ops', _kind(log), '/Game/MT/X/A', {}, delete=lambda ops, path: log.append('delete'),
                   ignored=tracked)
    assert log == []


def test_a_spec_that_cannot_be_recorded_is_refused_before_anything_is_deleted_or_saved():
    log = []
    with pytest.raises(TypeError):
        ap.realize('ops', _kind(log), '/Game/MT/X/A', {'bad': {1, 2}},
                   delete=lambda ops, path: log.append('delete'), ignored=lambda path: log.append('ignored'))
    assert log == []


def test_content_file_takes_game_asset_paths_only():
    assert ap.content_file('/Game/MT/X/A', 'R') == os.path.join('R', 'Content', 'MT', 'X', 'A') + '.uasset'
    for bad in ('/Engine/A', '/Game/MT/A.A', '/Game/MT/', 'Game/MT/A'):
        with pytest.raises(ValueError):
            ap.content_file(bad, 'R')


def test_require_gitignored_fails_closed(monkeypatch):
    monkeypatch.setattr(ap.subprocess, 'call', lambda *a, **k: 1)
    with pytest.raises(RuntimeError, match='not git-ignored'):
        ap.require_gitignored('/Game/MT/X/A')
    monkeypatch.setattr(ap.subprocess, 'call', lambda *a, **k: 0)
    ap.require_gitignored('/Game/MT/X/A')

    def missing(*a, **k):
        raise OSError('no git')
    monkeypatch.setattr(ap.subprocess, 'call', missing)
    with pytest.raises(RuntimeError, match='could not run'):
        ap.require_gitignored('/Game/MT/X/A')


def test_digest_ignores_key_order_and_sees_values():
    assert ap.digest({'a': 1, 'b': [1, 2]}) == ap.digest({'b': [1, 2], 'a': 1})
    assert ap.digest({'a': 1}) != ap.digest({'a': 2})


class _Ops:
    def __init__(self, exists_after=False):
        self.scripts, self.exists_after = [], exists_after

    def py(self, script):
        self.scripts.append(script)
        if 'delete_asset' in script and self.exists_after:
            raise RuntimeError('AssertionError: /Game/MT/X/A ' + ap.HELD)
        return True


def test_delete_loads_in_one_call_and_deletes_in_the_next_and_retries_only_while_held(monkeypatch):
    ops = _Ops()
    ap.delete_asset(ops, '/Game/MT/X/A')
    assert len(ops.scripts) == 2 and 'load_asset' in ops.scripts[0] and 'load_asset' not in ops.scripts[1]
    assert 'os.path.exists(f)' in ops.scripts[1] and 'wait_for_completion' in ops.scripts[0]

    import asset_delete
    monkeypatch.setattr(asset_delete, 'HELD_RETRY_S', 0)
    with pytest.raises(RuntimeError, match=ap.HELD):
        ap.delete_asset(_Ops(exists_after=True), '/Game/MT/X/A')


def test_the_animgraph_kind_calls_its_tools_and_raises_on_a_refusal():
    calls = []

    class Ops:
        def call(self, toolset, name, **arguments):
            calls.append(name)
            return {'ok': name != 'SaveAnimBlueprint', 'error': 'disk full'}
    kind = ap.animgraph()
    spec = {'nodes': []}
    kind.build(Ops(), '/Game/MT/X/A', spec)
    assert kind.describe(Ops(), '/Game/MT/X/A', spec)['ok']
    with pytest.raises(RuntimeError, match='SaveAnimBlueprint: disk full'):
        kind.save(Ops(), '/Game/MT/X/A')
    assert calls == ['BuildAnimBlueprint', 'DescribeAnimGraph', 'SaveAnimBlueprint']
