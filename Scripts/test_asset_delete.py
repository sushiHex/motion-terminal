"""asset_delete.while_held: a delete the editor still holds is retried; anything else is raised at once."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_delete as ad  # noqa: E402


class _EditorError(RuntimeError):
    """Stands in for whatever the transport raises (unreal_mcp.ScriptError): only its text counts."""


def _flaky(failures, message):
    calls = []

    def fn():
        calls.append(1)
        if len(calls) <= failures:
            raise _EditorError(message)
        return 'deleted'
    return fn, calls


def test_a_held_delete_is_retried_until_it_goes_through(monkeypatch):
    monkeypatch.setattr(ad.time, 'sleep', lambda s: None)
    fn, calls = _flaky(2, 'execute_python: AssertionError: /Game/MT/X/ABP still exists after its delete')
    assert ad.while_held(fn) == 'deleted' and len(calls) == 3


def test_any_other_failure_is_raised_at_once(monkeypatch):
    monkeypatch.setattr(ad.time, 'sleep', lambda s: None)
    fn, calls = _flaky(1, 'execute_python: AssertionError: could not delete /Game/MT/X/ABP')
    with pytest.raises(_EditorError):
        ad.while_held(fn)
    assert len(calls) == 1


def test_a_delete_held_past_the_deadline_is_raised(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(ad.time, 'time', lambda: now[0])
    monkeypatch.setattr(ad.time, 'sleep', lambda s: now.__setitem__(0, now[0] + s))
    fn, calls = _flaky(10 ** 6, ad.HELD)
    with pytest.raises(_EditorError):
        ad.while_held(fn)
    assert len(calls) == ad.HELD_RETRY_S // ad.HELD_WAIT_S + 1        # t = 0, 15 ... 300
