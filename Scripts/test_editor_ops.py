import json
import os

import pytest

import editor_ops as eo
import dyn_run
import unreal_mcp


def _wire(value):
    """A script's final value as the 5.9 server sends it (python_scripting.py _result_text: None empty, a string as
    it is, anything else JSON), as a real ToolResult: the decoding under test is the client's own."""
    text = '' if value is None else value if isinstance(value, str) else json.dumps(value)
    return unreal_mcp.ToolResult({'content': [{'type': 'text', 'text': text}]})


class _Client:
    """Answers execute_python from (substring, reply) rules, in order, and toolset calls from `tools` (each reply a
    dict the C++ tool would serialize to JSON); records every script and call."""

    def __init__(self, rules=(), tools=None):
        self.rules, self.tools, self.scripts, self.calls = list(rules), tools or {}, [], []

    def execute_python(self, script, timeout=None):
        self.scripts.append(script)
        for key, reply in self.rules:
            if key in script:
                return _wire(reply() if callable(reply) else reply)
        return _wire(None)

    def call_tool(self, name, arguments=None, timeout=None):
        assert name == 'call_tool' and arguments['toolset_name'] == eo.SESSION_TOOLS
        tool, args = arguments['tool_name'], arguments['arguments']
        self.calls.append((tool, args))
        reply = self.tools[tool]
        reply = reply(**args) if callable(reply) else reply
        return _wire({'returnValue': json.dumps(reply)})     # an FString of JSON, wrapped as the server wraps it


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(eo, 'POLL_S', 0.0)
    monkeypatch.setattr(eo.time, 'sleep', lambda s: None)


def test_a_json_string_result_arrives_decoded_once():
    # SetPlayWorldRendering returns a JSON FString; py() hands back the dict, and must not be decoded again
    client = _Client([('set_play_world_rendering', json.dumps({'ok': True, 'viewports': 1}))])
    assert eo.EditorOps(client).py('unreal.RenderSessionTools.set_play_world_rendering(False)') == \
        {'ok': True, 'viewports': 1}


def test_load_level_states_its_unsaved_policy_and_raises_when_refused():
    client = _Client([('load_level', False)])
    with pytest.raises(eo.EditorOpsError, match='refused'):
        eo.EditorOps(client).load_level('/Game/A', unsaved='discard')
    assert 'LevelEditorUnsavedChangesPolicy.DISCARD' in client.scripts[-1]
    with pytest.raises(ValueError):
        eo.EditorOps(client).load_level('/Game/A', unsaved='lose')


def test_reset_level_discards_only_its_own_map():
    client = _Client([('get_outermost', '/Game/Other'), ('load_level', True)])
    eo.EditorOps(client).reset_level('/Game/Stage')
    assert 'Policy.FAIL' in client.scripts[-1]
    client = _Client([('get_outermost', '/Game/Stage'), ('load_level', True)])
    eo.EditorOps(client).reset_level('/Game/Stage')
    assert 'Policy.DISCARD' in client.scripts[-1]


def test_load_level_without_reload_keeps_the_open_map():
    client = _Client([('get_outermost', '/Game/A'), ('load_level', True)])
    assert eo.EditorOps(client).load_level('/Game/A', reload=False) is False
    assert not any('load_level' in s for s in client.scripts)
    assert eo.EditorOps(client).load_level('/Game/B', reload=False) is True


def _pie(appear_after=3):
    state = {'world': None, 'polls': 0}

    def world():
        state['polls'] += 1
        if state['polls'] == appear_after and state.get('requested'):
            state['world'] = 'UEDPIE_0_Lvl'
        return state['world']

    def begin():
        state['requested'] = True

    def end():
        state['world'] = None
    rules = [('editor_request_begin_play', begin), ('editor_request_end_play', end),
             ('bool(', lambda: state['world'] is not None), ('get_game_world', world)]
    return state, _Client(rules)


def test_start_pie_waits_for_the_world_and_stop_pie_for_its_end():
    state, client = _pie()
    ops = eo.EditorOps(client)
    assert ops.start_pie() == 'UEDPIE_0_Lvl'
    ops.stop_pie()
    assert state['world'] is None
    ops.stop_pie()                                    # none left: no second request
    assert sum('editor_request_end_play' in s for s in client.scripts) == 1


def test_start_pie_raises_when_no_world_appears(monkeypatch):
    clock = iter(range(0, 10000, 50))
    monkeypatch.setattr(eo.time, 'monotonic', lambda: next(clock))
    client = _Client([('bool(', False), ('get_game_world', None)])
    with pytest.raises(eo.EditorOpsError, match='PIE world'):
        eo.EditorOps(client).start_pie(timeout=120)


def test_a_transient_poll_failure_is_a_poll_that_saw_nothing():
    replies = iter([unreal_mcp.OutcomeUnknown('lost'), 'ready'])

    def check(t):
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return r
    assert eo.EditorOps(_Client()).wait('x', check, timeout=10) == 'ready'

    def broken(t):
        raise unreal_mcp.ScriptError('the script raised')
    with pytest.raises(unreal_mcp.ScriptError):
        eo.EditorOps(_Client()).wait('x', broken, timeout=10)


def test_a_call_the_busy_editor_did_not_run_is_sent_again_and_nothing_else_is():
    ops, now, slept = eo.EditorOps(_Client()), [0.0], []

    def sleep(s):
        slept.append(s)
        now[0] += s

    def sender(outcomes):
        sent = []

        def send():
            sent.append(1)
            r = outcomes.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return send, sent
    busy = unreal_mcp.NotRun('Not run: the Unreal Editor is running a slow task')
    send, sent = sender([busy, busy, 'done'])
    assert ops._unblocked(send, wait_s=60, sleep=sleep, clock=lambda: now[0]) == 'done' and len(sent) == 3
    # past the wait the refusal propagates
    send, sent = sender([busy] * 100)
    with pytest.raises(unreal_mcp.NotRun):
        ops._unblocked(send, wait_s=5, sleep=sleep, clock=lambda: now[0])
    # a call whose outcome is unknown may have run: never sent twice
    send, sent = sender([unreal_mcp.OutcomeUnknown('lost'), 'done'])
    with pytest.raises(unreal_mcp.OutcomeUnknown):
        ops._unblocked(send, wait_s=60, sleep=sleep, clock=lambda: now[0])
    assert len(sent) == 1


def test_sample_stats_owns_its_window_and_always_closes_it():
    frames = iter([0, 5, 12])
    client = _Client(tools={
        'StatSampleBegin': {'ok': True},
        'StatSampleRead': lambda Window, Frames: {'ok': True, 'frames': next(frames), 'collecting': True,
                                                  'stats': {}},
        'StatSampleEnd': {'ok': True}})
    assert eo.EditorOps(client).sample_stats('Anim', 12)['frames'] == 12
    assert [c[0] for c in client.calls] == ['StatSampleBegin'] + ['StatSampleRead'] * 3 + ['StatSampleEnd']
    windows = {c[1]['Window'] for c in client.calls}
    assert len(windows) == 1 and windows.pop().startswith('mt-')

    client.calls.clear()
    client.tools['StatSampleBegin'] = lambda Window, Group: (_ for _ in ()).throw(unreal_mcp.OutcomeUnknown('lost'))
    with pytest.raises(unreal_mcp.OutcomeUnknown):
        eo.EditorOps(client).sample_stats('Anim', 12)
    assert client.calls[-1][0] == 'StatSampleEnd'        # closed even though the open's reply was lost


def _log(tmp_path, before=b''):
    path = tmp_path / 'Editor.log'
    path.write_bytes(before)
    written = []
    log = eo.EditorLog.begin(str(path), written.append)
    return path, log, written


def test_log_counts_only_lines_after_its_marker(tmp_path):
    path, log, written = _log(tmp_path, b'[t][  1]LogPython: KHAIDYN_DRIVE {"tag": "old"}\n')
    with open(path, 'ab') as fh:     # an earlier run's buffered line lands after begin() noted the size
        fh.write(b'[t][  2]LogPython: KHAIDYN_DRIVE {"tag": "stale"}\n')
        fh.write(('[t][  3]LogPython: %s\n' % written[0]).encode())
        fh.write(b'[t][  4]LogPython: KHAIDYN_DRIVE {"tag": "new"}\n')
    assert [eo.json_after(line, 'KHAIDYN_DRIVE')['tag'] for line in log.new_lines()] == ['new']


def test_log_holds_a_partial_line_until_its_newline(tmp_path):
    path, log, written = _log(tmp_path)
    with open(path, 'ab') as fh:
        fh.write(('[t][  1]LogPython: %s\r\n' % written[0]).encode())
        fh.write(b'[t][  2]LogPython: KHAIDYN_DRIVE {"tag": ')
    assert log.new_lines() == []
    with open(path, 'ab') as fh:
        fh.write(b'"x"}\r\n')
    line, = log.new_lines()
    assert eo.json_after(line, 'KHAIDYN_DRIVE') == {'tag': 'x'}
    assert log.wait_for(lambda l: 'KHAIDYN' in l, timeout=0) == line


def test_log_raises_when_the_file_is_replaced_even_at_a_larger_size(tmp_path):
    path, log, _w = _log(tmp_path, b'x' * 10)
    os.remove(path)
    path.write_bytes(b'y' * 100)
    with pytest.raises(eo.EditorOpsError, match='replaced'):
        log.new_lines()


def test_a_marker_that_never_arrives_raises(tmp_path, monkeypatch):
    path = tmp_path / 'Editor.log'
    path.write_bytes(b'')
    monkeypatch.setattr(eo, 'MARK_WAIT_S', 0.0)
    client = _Client(tools={'GetLogFile': {'ok': True, 'path': str(path)}})
    with pytest.raises(eo.EditorOpsError, match='never reached'):
        eo.EditorOps(client).log()
    assert 'unreal.log(' in client.scripts[-1]


def test_message_strips_the_log_prefix():
    assert eo.EditorLog.message('[2026.11.11-10.00.00:123][ 42]LogPython: Warning: army=46') == 'army=46'
    assert eo.EditorLog.message('[2026.11.11-10.00.00:123][ 42]LogTemp: LANE x=1') == 'LANE x=1'


def test_the_run_matches_only_its_own_summary_and_error_lines():
    match = dyn_run._summary('s1', 'ParagonAurora')
    assert match('[t][1]LogPython: KHAIDYN_DRIVE {"tag": "s1", "hero": "ParagonAurora", "path": "p"}')
    assert not match('[t][1]LogPython: KHAIDYN_DRIVE {"tag": "s2", "hero": "ParagonAurora"}')
    assert match('[t][1]LogPython: Error: KHAIDYN_DRIVE error {"tag": "s1", "hero": "ParagonAurora"} '
                 "ValueError('x')")
    assert not match('[t][1]LogPython: Error: KHAIDYN_DRIVE error {"tag": "s9", "hero": "ParagonAurora"} '
                     "ValueError('x')")
    assert not match('[t][1]LogPython: KHAIDYN_DRIVE registered {"tag": "s1", "hero": "ParagonAurora"}')
