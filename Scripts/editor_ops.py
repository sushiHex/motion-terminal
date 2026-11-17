"""The editor operations a host-side driver needs, over Epic's MCP server (unreal_mcp.Client).

    ops = editor_ops.shared()
    log = ops.log()                    # evidence starts at a marker written now
    ops.reset_level(STAGE)
    ops.start_pie()
    ...
    for line in log.new_lines(): ...
    ops.stop_pie()

Every editor-side step runs as one short execute_python call, which runs on the game thread inside one undo
transaction (editor_toolset python_scripting.py, _execute): nothing here waits inside the editor. A wait for the
editor to change state (PIE starting or ending, stat frames arriving) is a host-side poll with a deadline, and each
poll call is itself bounded by what is left of that deadline, so a deadline that passes raises EditorOpsError naming
what never happened.
"""
import json
import os
import re
import time
import uuid

import unreal_mcp

SESSION_TOOLS = 'MotionTerminal.EditorSessionTools'
UNSAVED = {'fail': 'FAIL', 'save': 'SAVE', 'discard': 'DISCARD'}   # unreal.LevelEditorUnsavedChangesPolicy
POLL_S = 0.5
POLL_CALL_S = 60.0       # the longest one poll call may take
MARK_WAIT_S = 15.0       # the log marker must reach the file within this
# Failures that say nothing about the editor's own state: a poll may try again. Anything else (the script raised,
# the session was rejected) is permanent and propagates.
TRANSIENT = (unreal_mcp.NotReachable, unreal_mcp.OutcomeUnknown, unreal_mcp.NotRun, OSError)
# A call the server refused because a slow task or modal held the editor did not run and changed nothing
# (unreal_mcp.NotRun), so every primitive sends it again until the editor is free, for at most this long. Only
# NotRun: a call whose outcome is unknown may have run, and is never repeated here.
NOT_RUN_WAIT_S = 300.0
NOT_RUN_RETRY_S = 2.0

_LES = 'unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)'
_UES = 'unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)'


class EditorOpsError(RuntimeError):
    """The editor refused an operation, or did not reach the state it was asked for in time."""


_SHARED = None


def shared(timeout=1800):
    """This process's EditorOps, on one MCP session opened at first use and closed at exit. `timeout` is the
    client's default reply deadline, which a single call can override."""
    global _SHARED
    if _SHARED is None:
        import atexit
        client = unreal_mcp.Client(timeout=timeout).connect()
        atexit.register(client.close)
        _SHARED = EditorOps(client)
    return _SHARED


class EditorOps:
    def __init__(self, client):
        self.client = client
        self._log_path = None

    # -- primitives --

    def _unblocked(self, send, wait_s=None, sleep=time.sleep, clock=time.monotonic):
        """send(), sent again while the editor refuses it as busy (NotRun: it did not run) until NOT_RUN_WAIT_S have
        passed; then that refusal propagates. Any other outcome is returned or raised as it came."""
        deadline = clock() + (NOT_RUN_WAIT_S if wait_s is None else wait_s)
        while True:
            try:
                return send()
            except unreal_mcp.NotRun:
                if clock() >= deadline:
                    raise
                sleep(NOT_RUN_RETRY_S)

    def py(self, script, timeout=None):
        """Run Python in the editor; returns the script's final expression, decoded (unreal_mcp.ToolResult.value:
        a JSON result arrives as the value, a plain string as the string, None as '')."""
        return self._unblocked(lambda: self.client.execute_python('import unreal\n' + script, timeout=timeout)).value()

    def run_file(self, path, timeout=None, **config):
        """Run an in-editor script file with `config` as its globals; returns its MT_RESULT (unreal_mcp.run_file)."""
        return self._unblocked(lambda: self.client.run_file(path, timeout=timeout, **config))

    def call(self, toolset, name, timeout=None, **arguments):
        """One call of a registered toolset's tool (not through execute_python, so not inside its undo transaction);
        returns the decoded JSON reply, which must be an object. Whether it says ok is the caller's to judge."""
        reply = self._unblocked(lambda: self.client.call_tool(
            'call_tool', {'toolset_name': toolset, 'tool_name': name, 'arguments': arguments},
            timeout=timeout)).value()
        if not isinstance(reply, dict):
            raise EditorOpsError('%s.%s replied %r, not a JSON object' % (toolset, name, reply))
        return reply

    def tool(self, name, timeout=None, **arguments):
        """A MotionTerminal.EditorSessionTools call; returns its decoded JSON reply, raising when it says ok false."""
        reply = self.call(SESSION_TOOLS, name, timeout=timeout, **arguments)
        if not reply.get('ok'):
            raise EditorOpsError('%s: %r' % (name, reply))
        return reply

    def wait(self, what, check, timeout):
        """Poll check(call_timeout) from the host until it returns something truthy (returned), or raise once
        `timeout` s have passed. Each check gets what is left of the deadline (at most POLL_CALL_S) as its own
        call deadline; a transient failure counts as a poll that saw nothing."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            try:
                got = check(max(1.0, min(POLL_CALL_S, left)))
            except TRANSIENT:
                got = None
            if got:
                return got
            if time.monotonic() > deadline:
                raise EditorOpsError('%s: not reached within %.0f s' % (what, timeout))
            time.sleep(POLL_S)

    # -- levels --

    def current_level(self):
        """The package name of the editor world's map, e.g. /Game/MT/Army/Lvl_MT_Army."""
        return self.py('%s.get_editor_world().get_outermost().get_name()' % _UES)

    def load_level(self, path, *, unsaved='fail', reload=True):
        """Open the map at `path`. `unsaved` is what happens to unsaved changes in the open map: 'fail' (the
        default: raise, and lose nothing), 'save', or 'discard'. reload=False leaves an already open `path` as it is,
        unsaved actors included (a caller that put them there on purpose). Returns whether a load ran."""
        if unsaved not in UNSAVED:
            raise ValueError('unsaved must be one of %s' % sorted(UNSAVED))
        if not reload and self.current_level() == path:
            return False
        ok = self.py('%s.load_level(%r, unreal.LevelEditorUnsavedChangesPolicy.%s)' % (_LES, path, UNSAVED[unsaved]))
        if ok is not True:
            raise EditorOpsError('load_level %s refused (unsaved=%s): %r' % (path, unsaved, ok))
        return True

    def reset_level(self, path):
        """Reopen `path` clean, discarding unsaved changes to `path` only: when another map is open, its unsaved
        changes make the load fail rather than being lost."""
        return self.load_level(path, unsaved='discard' if self.current_level() == path else 'fail')

    # -- play in editor --

    def pie_world(self, timeout=POLL_CALL_S):
        """The play world's name, or '' when there is none."""
        return self.py('w = %s.get_game_world()\nw.get_name() if w else None' % _UES, timeout=timeout)

    def pie_active(self, timeout=POLL_CALL_S):
        """True while a PIE session exists in any stage: requested, starting, running or ending."""
        return bool(self.py('bool(%s.is_in_play_in_editor() or %s.get_game_world())' % (_LES, _UES),
                            timeout=timeout))

    def start_pie(self, timeout=120):
        """Request a PIE session and wait for its world. The request needs an active level viewport (without one the
        engine does nothing, LevelEditorSubsystem.cpp EditorRequestBeginPlay); the session starts on a later tick."""
        if self.pie_active():
            raise EditorOpsError('start_pie: a PIE session already exists')
        self.py('%s.editor_request_begin_play()' % _LES)
        return self.wait('PIE world', lambda t: self.pie_world(timeout=t), timeout)

    def stop_pie(self, timeout=120):
        """Request the PIE session's end and wait until none exists. A no-op when none does (a render's own PIE
        session ends with the render)."""
        if not self.pie_active():
            return
        self.py('%s.editor_request_end_play()' % _LES)
        self.wait('PIE ended', lambda t: not self.pie_active(timeout=t), timeout)

    def console(self, command):
        """Run a console command in the play world if one runs, else the editor world. The engine reports nothing
        back, so a caller that needs the command's effect checks for it."""
        self.py('w = %s.get_game_world() or %s.get_editor_world()\n'
                'unreal.SystemLibrary.execute_console_command(w, %r)' % (_UES, _UES, command))

    # -- stats --

    def sample_stats(self, group, frames, timeout=30.0, poll=1.0):
        """Open a stat window over `group` (e.g. 'Anim') under an id of this call's own, wait until `frames` frames
        settled after it opened (or the timeout), read, close. Returns EditorSessionTools.StatSampleRead's reply;
        'frames' may be under `frames` when the timeout passed, and 0 with 'collecting' false when the engine
        produced none. The window is closed whatever happens, even when the open's reply was lost."""
        window = 'mt-%s' % uuid.uuid4().hex
        deadline = time.monotonic() + timeout
        try:
            self.tool('StatSampleBegin', timeout=POLL_CALL_S, Window=window, Group=group)
            while True:
                left = deadline - time.monotonic()
                read = self.tool('StatSampleRead', timeout=max(1.0, min(POLL_CALL_S, left)), Window=window,
                                 Frames=frames)
                if read['frames'] >= frames or time.monotonic() > deadline:
                    return read
                time.sleep(poll)
        finally:
            try:
                self.tool('StatSampleEnd', timeout=POLL_CALL_S, Window=window)
            except (EditorOpsError, unreal_mcp.McpError, OSError) as exc:
                print('EDITOR_OPS stat window %s not closed: %s' % (window, str(exc)[:200]))

    # -- log --

    def log_path(self):
        if self._log_path is None:
            self._log_path = self.tool('GetLogFile')['path']
        return self._log_path

    def log(self):
        """An EditorLog positioned at a marker written now: its lines are the ones logged after this call. Raises
        when the marker does not reach the file (file logging off, LogPython suppressed)."""
        log = EditorLog.begin(self.log_path(), lambda line: self.py('unreal.log(%r)' % line))
        if not log.wait_started(MARK_WAIT_S):
            raise EditorOpsError('log marker never reached %s within %.0f s: is LogPython or file logging off?'
                                 % (log.path, MARK_WAIT_S))
        return log


_PREFIX = re.compile(r'^\[[^\]]*\]\[\s*\d+\]')
_CATEGORY = re.compile(r'^\w+: (?:(?:Display|Warning|Error|Verbose|VeryVerbose|Fatal): )?')


def _identity(st):
    return st.st_dev, st.st_ino


class EditorLog:
    """The editor's log file, read from a marker on.

    begin() notes the file's size and identity, then has the editor log a unique marker line. Only lines after the
    marker count, so a line an earlier run left in the log buffer (written to the file after the size was noted, but
    logged before the marker) never counts as this run's. Only complete lines are returned; a partly written last
    line waits for its newline. A file that is replaced (another identity) or shrinks below the read position
    raises."""

    def __init__(self, path, offset, identity, marker):
        self.path, self.offset, self.identity, self.marker = path, offset, identity, marker
        self.started = False
        self._tail = b''
        self.lines = []          # every line after the marker read so far

    @classmethod
    def begin(cls, path, emit):
        marker = 'MT_LOG_MARK %s' % uuid.uuid4().hex
        st = os.stat(path)
        log = cls(path, st.st_size, _identity(st), marker)
        emit(marker)
        return log

    def new_lines(self):
        """The complete lines after the marker that arrived since the last call."""
        with open(self.path, 'rb') as fh:
            st = os.fstat(fh.fileno())
            if _identity(st) != self.identity or st.st_size < self.offset:
                raise EditorOpsError('editor log %s was replaced while being read' % self.path)
            fh.seek(self.offset)
            chunk = fh.read(st.st_size - self.offset)
        self.offset += len(chunk)
        data = self._tail + chunk
        cut = data.rfind(b'\n') + 1
        self._tail = data[cut:]
        fresh = []
        for raw in data[:cut].splitlines():
            line = raw.decode('utf-8', 'replace').rstrip('\r')
            if not self.started:
                self.started = self.marker in line
                continue
            fresh.append(line)
        self.lines.extend(fresh)
        return fresh

    def wait_started(self, timeout, poll=0.25):
        """Whether the marker reached the file within timeout s."""
        deadline = time.monotonic() + timeout
        while True:
            self.new_lines()
            if self.started or time.monotonic() > deadline:
                return self.started
            time.sleep(poll)

    def wait_for(self, predicate, timeout, poll=1.0):
        """The first line after the marker for which predicate(line) holds, or None after timeout s."""
        deadline = time.monotonic() + timeout
        while True:
            self.new_lines()
            hit = next((line for line in self.lines if predicate(line)), None)
            if hit is not None or time.monotonic() > deadline:
                return hit
            time.sleep(poll)

    @staticmethod
    def message(line):
        """A log line without its timestamp, frame counter, category and verbosity."""
        return _CATEGORY.sub('', _PREFIX.sub('', line), count=1)


def json_after(line, tag):
    """The JSON object after `tag ` in a log line, e.g. json_after(line, 'KHAIDYN_DRIVE')."""
    return json.JSONDecoder().raw_decode(line.split(tag + ' ', 1)[1])[0]
