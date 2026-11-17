"""Host-side client for Epic's Unreal MCP server (the ModelContextProtocol plugin, default :8000/mcp).

    from unreal_mcp import Client
    with Client() as mcp:
        print(mcp.list_toolsets())
        print(mcp.call('SomeToolset', 'some_tool', asset_path='/Game/...').json())

This is the protocol layer only: JSON-RPC 2.0 over MCP Streamable HTTP, one POST per message on a fresh connection,
standard library only. Run it from a normal shell, never from inside the editor: the server answers on the game thread.

Why the error types matter: a tool call can change the editor (assets, worlds, files on disk). After a failure the
caller must know whether the call can have run, and every error states which side of that line it is on:

    NotReachable     the TCP connection was never established, so no byte of the request left this process. The call
                     provably did not run. It is the only error that is retried (within connect_patience).
    OutcomeUnknown   the connection was established and request bytes may have reached the server, then the reply was
                     lost, cut short, timed out or unreadable. The call may have run, partly or fully. Never retried.
    SessionRejected  HTTP 404 for the session id sent. Never replayed: after an editor restart, state that earlier calls
                     set up may be gone, so whether a replay is safe is the caller's decision. connect() starts anew.
    HttpError        any other non-success HTTP status without a JSON-RPC error body.
    RpcError         the server answered with a JSON-RPC error object.
    ToolError        the tool ran and returned a result with isError set.
      NotRun         the server refused the tool call because a modal dialog or slow task blocked the editor; the
                     tool did not run and made no changes, so it is safe to retry once the editor is free.
      ScriptError    execute_python ran the script and it raised; `traceback` is the server's formatted text.

Wire behaviour relied on, from the UE 5.9 source (ModelContextProtocolServer.cpp unless noted):
  - initialize needs no session; the reply carries the new session id in an Mcp-Session-Id header. Every later request
    must send that header (400 if missing, 404 if unknown; 5.9 sends that 404 with an empty body) and may send
    Mcp-Protocol-Version, which must equal the negotiated version (400 otherwise). Notifications are answered 202.
  - A reply is one application/json body with a Content-Length, except a tools/call whose params carry
    _meta.progressToken: that is answered as text/event-stream with Transfer-Encoding: chunked and Connection:
    keep-alive (HttpConnectionResponseWriteContext.cpp in HTTPServer), carries notifications/progress heartbeats (a
    counter, no total; every ProgressIntervalSeconds, 1 s by default in ModelContextProtocol.cpp) and possibly
    notifications/tools/list_changed, and ends with the result event and the terminal chunk. The server does not close
    the connection, so a stream is read until the matching id; the end of the body or EOF before it is a lost reply.
  - A tools/call refused because of a modal dialog or slow task is an isError result whose text starts "Not run:" and
    says "This call made no changes"; it is sent before any tool code runs.
  - The server cancels a tool call whose client connection closes (POST routes detect client disconnect), so closing
    the socket after a read timeout may cancel the call, or it may already have finished.
  - JSON-RPC errors raised while parsing the request carry id null; tool failures come back as results with isError.
  - With tool search on (the default), toolset tools are reached through the meta-tools list_toolsets,
    describe_toolsets, describe_tools and call_tool (ModelContextProtocolToolSearch.h).
Earlier builds framed streams differently: 5.8 closed the connection after an unframed stream, and 5.8.0 answered every
tools/call that way (no Content-Length, no chunking). This client reads chunked, Content-Length and close-delimited bodies,
for either content type, whatever was asked for.
"""
import ast
import contextlib
import itertools
import json
import socket
import threading
import time
import urllib.parse

DEFAULT_URL = 'http://127.0.0.1:8000/mcp'
PROTOCOL_VERSION = '2025-06-18'     # an initialization version on both 5.8 and 5.9 (ModelContextProtocolVersion.cpp)
PYTHON_TOOLSET = 'editor_toolset.toolsets.python_scripting.PythonScriptingTools'   # EditorToolset plugin, 5.9
# what the 5.9 server writes, and only then, when it refuses a tools/call because the editor is blocked
_NOT_RUN_PREFIX, _NOT_RUN_MARK = 'Not run: the Unreal Editor is ', 'This call made no changes.'
CLIENT_INFO = {'name': 'unreal_mcp', 'version': '1'}
CONNECT_TIMEOUT_S = 5.0             # a connect that takes longer is treated as not reachable (nothing was sent)
BACKOFF_FIRST_S, BACKOFF_CAP_S = 0.2, 5.0
CLOSE_TIMEOUT_S = 5.0
REPLY_LIMIT = 256 * 1024 * 1024     # bytes per reply; a larger one is treated as unreadable
_HEAD_LIMIT = 64 * 1024
_RECV_SIZE = 65536


class McpError(Exception):
    """Base of every error this module raises. `sent` is False only when no byte of the request left this process."""

    sent = True

    def __init__(self, message, *, method=None, sent=None):
        super().__init__(message)
        self.method = method
        if sent is not None:
            self.sent = sent


class NotReachable(McpError):
    """The connection to the server could not be established (refused, connect timeout, no route).

    Raised before any request byte is written, so the request provably never arrived and cannot have run anything.
    This is the only error retried, with capped backoff, for up to `connect_patience` seconds."""

    sent = False


class OutcomeUnknown(McpError):
    """The connection was established, so request bytes may have reached the server, and then no usable reply came:
    a send or read failure, a read timeout, the connection closed or reset mid-reply, or a truncated, malformed or
    mismatched body. The request may have run, partly or fully, and may still be running. `sent` is True.

    After a read timeout this client closes the connection, and the 5.9 server cancels a tool call whose connection
    closed. Cancellation is cooperative (a tool that never yields runs on) and races completion, so the call may have
    been cancelled part way, may have completed, or may still be running. A timeout long enough for the work is the
    only way to get its result.

    Never retried: replaying a call whose first attempt may have run can apply it twice."""

    sent = True


class SessionRejected(McpError):
    """HTTP 404 for the Mcp-Session-Id sent: the server does not know the session (typically the editor restarted).

    The server rejects it before dispatching anything, but the session's earlier state may be gone, so the request is
    not replayed. Call connect() for a new session and decide whether anything needs redoing."""

    def __init__(self, message, *, method=None, session_id=None):
        super().__init__(message, method=method)
        self.session_id = session_id


class HttpError(McpError):
    """A non-success HTTP status other than the session 404, whose body is not a JSON-RPC error. `status`, `body`."""

    def __init__(self, message, *, method=None, status=None, body=b''):
        super().__init__(message, method=method)
        self.status = status
        self.body = body


class RpcError(McpError):
    """The server answered with a JSON-RPC error object: `code`, `rpc_message`, `data`, and the HTTP `status` it came
    with (the 5.8 server sends most of them as 400)."""

    def __init__(self, message, *, method=None, code=None, rpc_message='', data=None, status=200):
        super().__init__(message, method=method)
        self.code = code
        self.rpc_message = rpc_message
        self.data = data
        self.status = status


class ToolError(McpError):
    """The tool ran and returned a result with isError set. `result` is the ToolResult; `ran` is True."""

    ran = True

    def __init__(self, message, *, method=None, result=None):
        super().__init__(message, method=method)
        self.result = result


class NotRun(ToolError):
    """The server refused the tool call before running any tool code, because the editor was showing a modal dialog
    or running a slow task. The request was sent (`sent` is True) but the tool did not run (`ran` is False) and made
    no changes, so the same call can be retried once the editor is free; retrying while it is blocked returns this
    again."""

    ran = False


class ScriptError(ToolError):
    """execute_python ran the script and it raised (or was rejected before running, e.g. a SyntaxError). `traceback`
    is the server's text: the exception and, when the script's own frames are on it, a traceback of those frames."""

    def __init__(self, message, *, method=None, result=None, traceback=None):
        super().__init__(message, method=method, result=result)
        self.traceback = traceback if traceback is not None else result.text if result is not None else ''


class ToolResult:
    """A tools/call result. `raw` is the result object as received."""

    def __init__(self, raw):
        self.raw = raw if isinstance(raw, dict) else {}

    @property
    def content(self):
        content = self.raw.get('content')
        return content if isinstance(content, list) else []

    @property
    def text(self):
        """The text parts of the content, joined."""
        return ''.join(c['text'] for c in self.content
                       if isinstance(c, dict) and c.get('type') == 'text' and isinstance(c.get('text'), str))

    @property
    def structured(self):
        """structuredContent if the server sent it, else None."""
        return self.raw.get('structuredContent')

    @property
    def is_error(self):
        # the server's own reading: absent is success, a bool is followed, anything else is an error
        # (ModelContextProtocolAnalytics.cpp IsToolResultSuccess)
        return 'isError' in self.raw and self.raw['isError'] is not False

    def value(self):
        """An execute_python script's final value. The server returns a string bare and anything else as JSON, and
        may wrap either in {"returnValue": ...}; a string that holds JSON is decoded too, so a script ending in
        json.dumps(x) reads back as x whichever form arrived. Text that is not JSON comes back as the string."""
        text = self.text
        for _ in range(3):              # {"returnValue": "<json>"} -> "<json>" -> the value
            try:
                value = json.loads(text)
            except ValueError:
                return text
            if isinstance(value, dict) and set(value) == {'returnValue'}:
                value = value['returnValue']
            if not isinstance(value, str):
                return value
            text = value
        return text

    def json(self):
        """The text parsed as JSON; ValueError if it is not JSON."""
        try:
            return json.loads(self.text)
        except ValueError as exc:
            raise ValueError('tool result text is not JSON: %r' % self.text[:200]) from exc

    def __repr__(self):
        return 'ToolResult(is_error=%s, text=%r)' % (self.is_error, self.text[:80])


class SseDecoder:
    """Incremental text/event-stream decoder. feed() takes bytes split anywhere and yields the data of each event as
    it completes (lazily: a line after the event wanted is not even decoded). Lines end in CRLF, LF or CR; multi-line
    data fields join with LF; comment lines (':' first, used for keepalives) and the event, id and retry fields are
    ignored; an event without data is not dispatched; an event the stream ends inside is dropped."""

    def __init__(self):
        self._buf = b''
        self._data = []
        self._skip_lf = False           # the previous line ended in CR; an LF arriving next is its CRLF's second half

    def feed(self, chunk):
        self._buf += chunk
        while self._buf:
            if self._skip_lf:
                self._skip_lf = False
                if self._buf[:1] == b'\n':
                    self._buf = self._buf[1:]
                    continue
            cr, lf = self._buf.find(b'\r'), self._buf.find(b'\n')
            if cr < 0 and lf < 0:
                return
            end = cr if cr >= 0 and (lf < 0 or cr < lf) else lf
            line, self._buf = self._buf[:end], self._buf[end + 1:]
            self._skip_lf = end == cr
            event = self._line(line.decode('utf-8'))
            if event is not None:
                yield event

    def _line(self, line):
        if not line:
            if not self._data:
                return None
            event, self._data = '\n'.join(self._data), []
            return event
        if line.startswith(':'):
            return None
        field, _, value = line.partition(':')
        if field == 'data':
            self._data.append(value[1:] if value.startswith(' ') else value)
        return None


class ChunkedDecoder:
    """Incremental Transfer-Encoding: chunked decoder (RFC 9112 section 7.1). feed() takes bytes split anywhere and
    returns the body bytes they complete. Chunk extensions (';' after the size) and trailer fields are skipped.
    `done` is set once the terminal 0 chunk and the CRLF closing the trailer section are read; bytes after that are
    ignored. `ended` is already True after the terminal chunk's size line, since the body is then complete even if
    the peer closes without the final CRLF. Malformed framing raises ValueError."""

    _HEX = frozenset(b'0123456789abcdefABCDEF')

    def __init__(self):
        self._buf = b''
        self._state = 'size'            # size, data, data-end, trailer, done
        self._left = 0                  # chunk data bytes still to come in state 'data'

    @property
    def done(self):
        return self._state == 'done'

    @property
    def ended(self):
        return self._state in ('trailer', 'done')

    def feed(self, data):
        if self.done:
            return b''
        self._buf += data
        out = []
        while not self.done:
            if self._state == 'data':
                if not self._buf:
                    break
                piece, self._buf = self._buf[:self._left], self._buf[self._left:]
                out.append(piece)
                self._left -= len(piece)
                if not self._left:
                    self._state = 'data-end'
            elif self._state == 'data-end':
                if len(self._buf) < 2:
                    break
                if self._buf[:2] != b'\r\n':
                    raise ValueError('chunk data not followed by CRLF')
                self._buf, self._state = self._buf[2:], 'size'
            else:
                end = self._buf.find(b'\r\n')
                if end < 0:
                    if len(self._buf) > _HEAD_LIMIT:
                        raise ValueError('chunk size or trailer line exceeds %d bytes' % _HEAD_LIMIT)
                    break
                line, self._buf = self._buf[:end], self._buf[end + 2:]
                if self._state == 'trailer':
                    if not line:
                        self._state, self._buf = 'done', b''
                    continue
                size = line.split(b';', 1)[0].strip(b' \t')
                if not size or not self._HEX.issuperset(size):
                    raise ValueError('bad chunk size line %r' % line[:80])
                self._left = int(size, 16)
                if self._left > REPLY_LIMIT:
                    raise ValueError('chunk of %d bytes exceeds %d' % (self._left, REPLY_LIMIT))
                self._state = 'data' if self._left else 'trailer'
        return b''.join(out)


class _Reader:
    """recv() against one overall deadline; a timeout or socket error surfaces as OSError for the caller to map."""

    def __init__(self, sock, deadline):
        self.sock, self.deadline, self.total = sock, deadline, 0

    def recv(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise socket.timeout('reply deadline passed')
        self.sock.settimeout(left)
        chunk = self.sock.recv(_RECV_SIZE)
        self.total += len(chunk)
        if self.total > REPLY_LIMIT:
            raise ValueError('reply exceeds %d bytes' % REPLY_LIMIT)
        return chunk


def _same_id(a, b):
    return type(a) is type(b) and a == b


_EDITOR_SCRIPT = '''import json
import traceback
_mt_ns = {config}
_mt_ns.update(__name__='__main__', __file__={path!r})
_mt_outcome = {{'error': None, 'result': None}}
try:
    with open({path!r}, encoding='utf-8') as _mt_file:
        _mt_code = compile(_mt_file.read(), {path!r}, 'exec')
    exec(_mt_code, _mt_ns)
    _mt_outcome['result'] = _mt_ns.get('MT_RESULT')
except BaseException:
    _mt_outcome['error'] = traceback.format_exc()
json.dumps(_mt_outcome, default=str)
'''


def editor_script(path, config):
    """The execute_python source that runs the file at `path` in the editor with `config` as its globals.

    The file runs in a namespace of its own (one dict, so its functions see its top-level names whatever namespace
    the server runs the submitted text in) and is compiled under its own path, so a traceback names the file's lines;
    the server keeps only the submitted text's frames, so the traceback is caught here and returned as data. The
    server's checks read only this text, never the file. `config` values must be Python literals."""
    for name in config:
        if not name.isidentifier() or name.startswith('__'):
            raise ValueError('config name is not a plain identifier: %r' % name)
    literal = repr(dict(config))
    try:
        same = ast.literal_eval(literal) == dict(config)
    except (ValueError, SyntaxError):
        same = False
    if not same:
        raise ValueError('config values must be Python literals (str, number, bool, None, list, dict)')
    return _EDITOR_SCRIPT.format(config=literal, path=str(path).replace('\\', '/'))


class Client:
    """A client for one MCP session on Epic's Unreal MCP server.

    One request at a time per client: a call made while another thread's call is in flight waits for it to finish.
    The internal lock guards only that hand-over; the network exchange and any progress callback run without a lock
    held. A progress callback must not call back into the same client (that raises McpError at once). This orders
    requests on this side only: after OutcomeUnknown the earlier call may still be running in the editor when the next
    one is sent.

    `sent` on an error describes the one HTTP exchange that failed. connect() is two exchanges; if the second fails,
    the session is discarded and the error is raised as it came.

    `timeout` is the default overall deadline, in seconds, for one reply once connected. `connect_patience` is how
    long a refused or unreachable connection keeps being retried (0: not at all); only that case is ever retried.
    """

    def __init__(self, url=DEFAULT_URL, timeout=120, *, connect_patience=0):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'http' or not parts.hostname:
            raise ValueError('only http://host[:port]/path URLs are supported: %r' % url)
        self.url = url
        self.host, self.port = parts.hostname, parts.port or 80
        self.path = (parts.path or '/') + ('?' + parts.query if parts.query else '')
        self.timeout = timeout
        self.connect_patience = connect_patience
        self.session_id = None
        self.protocol_version = None
        self.server_info = None
        self._ids = itertools.count(1)
        self._cond = threading.Condition()
        self._owner = None

    # -- lifecycle --

    def connect(self):
        """MCP handshake: initialize, store the session id, send notifications/initialized. Starts a new session
        (and new state on the server) every time it is called."""
        with self._turn():
            reply, headers = self._rpc('initialize', {'protocolVersion': PROTOCOL_VERSION, 'capabilities': {},
                                                      'clientInfo': CLIENT_INFO}, self.timeout, None, session=False)
            sid = headers.get('mcp-session-id')
            if not sid:
                raise OutcomeUnknown('initialize: the reply carried no Mcp-Session-Id header', method='initialize')
            info = reply if isinstance(reply, dict) else {}
            version = info.get('protocolVersion')
            if version is not None and not (isinstance(version, str) and version.isprintable() and version.isascii()):
                raise OutcomeUnknown('initialize: unusable protocolVersion %r' % (version,), method='initialize')
            self.session_id, self.server_info, self.protocol_version = sid, info, version
            try:
                self._notify('notifications/initialized', None)
            except McpError:
                # a half-made session is not used: the server created it, but it never saw it initialized
                self.session_id = self.protocol_version = None
                raise
        return self

    def close(self):
        """End the session on the server (HTTP DELETE) and forget it. Best effort: a failure here is ignored, since
        the server drops unknown sessions anyway and nothing can be done about it from here."""
        with self._turn():
            sid, self.session_id = self.session_id, None
            if sid is None:
                return
            try:
                self._exchange('DELETE', b'', sid, CLOSE_TIMEOUT_S, None, None, None, patience=0)
            except McpError:
                pass

    def __enter__(self):
        if self.session_id is None:
            self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- calls --

    def request(self, method, params=None, *, timeout=None, on_progress=None):
        """Send one JSON-RPC request in the current session and return its result.

        on_progress(params) is called with each notifications/progress for this request, from the calling thread and
        with no lock held; passing it adds a progressToken, which makes the server stream the reply. If it raises, the
        reply is abandoned and OutcomeUnknown is raised from that exception."""
        with self._turn():
            if self.session_id is None:
                raise McpError('%s: not connected; call connect() first' % method, method=method, sent=False)
            reply, _ = self._rpc(method, params, self.timeout if timeout is None else timeout, on_progress)
            return reply

    def call_tool(self, name, arguments=None, *, timeout=None, on_progress=None):
        """tools/call of a top-level server tool. Returns a ToolResult; raises ToolError if it has isError, NotRun
        if the server refused it because the editor was blocked."""
        return self._tool(name, arguments, timeout, on_progress, ToolError)

    def call(self, toolset, tool, /, **arguments):
        """A toolset tool through the server's call_tool meta-tool (toolset_name, tool_name, arguments). Uses the
        client's default timeout; for another timeout or progress, call call_tool('call_tool', {...}) directly."""
        return self.call_tool('call_tool', {'toolset_name': toolset, 'tool_name': tool, 'arguments': arguments})

    def list_toolsets(self):
        """The list_toolsets meta-tool's text: one '- Name: description' line per toolset."""
        return self.call_tool('list_toolsets', {}).text

    def describe_toolsets(self, names, include_schemas=False):
        """The describe_toolsets meta-tool (5.9) for one or more toolset names, parsed:
        {'toolsets': {name: description}, 'errors': {name: message}}. Schemas only with include_schemas."""
        return self.call_tool('describe_toolsets', {'toolset_names': _names(names),
                                                    'include_schemas': bool(include_schemas)}).json()

    def describe_tools(self, names):
        """The describe_tools meta-tool (5.9) for one or more 'Toolset.Tool' (or bare top-level) names, parsed:
        {'tools': {name: descriptor with schemas}, 'errors': {name: message}}."""
        return self.call_tool('describe_tools', {'tool_names': _names(names)}).json()

    def execute_python(self, script, *, timeout=None):
        """Run Python source in the editor through the 5.9 execute_python tool (PYTHON_TOOLSET). Returns the
        ToolResult; raises ScriptError if the script raised or was rejected, NotRun if the editor was blocked."""
        return self._tool('call_tool', {'toolset_name': PYTHON_TOOLSET, 'tool_name': 'execute_python',
                                        'arguments': {'script': {'content': script}}}, timeout, None, ScriptError,
                          label='execute_python')

    def run_file(self, path, *, timeout=None, **config):
        """Run a Python file in the editor with `config` as its globals (see editor_script). Returns the value the
        file left in MT_RESULT (None if it set none); raises ScriptError carrying the file's own traceback."""
        outcome = self.execute_python(editor_script(path, config), timeout=timeout).value()
        if not isinstance(outcome, dict) or 'error' not in outcome:
            raise ScriptError('run_file %s: no outcome came back (got %r)' % (path, outcome), method='tools/call')
        if outcome['error']:
            tb = outcome['error']
            raise ScriptError('run_file %s: %s' % (path, tb.strip().splitlines()[-1]), method='tools/call',
                              traceback=tb)
        return outcome.get('result')

    # -- internals --

    def _tool(self, name, arguments, timeout, on_progress, error, *, label=None):
        reply = self.request('tools/call', {'name': name, 'arguments': arguments or {}}, timeout=timeout,
                             on_progress=on_progress)
        result = ToolResult(reply)
        if result.is_error:
            text = result.text
            cls = NotRun if text.startswith(_NOT_RUN_PREFIX) and _NOT_RUN_MARK in text else error
            raise cls('%s: %s' % (label or name, text[:2000]), method='tools/call', result=result)
        return result

    @contextlib.contextmanager
    def _turn(self):
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                raise McpError('re-entrant call on the same client (from a progress callback?)', sent=False)
            while self._owner is not None:
                self._cond.wait()
            self._owner = me
        try:
            yield
        finally:
            with self._cond:
                self._owner = None
                self._cond.notify()

    def _rpc(self, method, params, timeout, on_progress, *, session=True):
        """One request/response; returns (result, reply headers)."""
        req_id = next(self._ids)
        message = {'jsonrpc': '2.0', 'id': req_id, 'method': method}
        token = None
        if on_progress is not None:
            token = 'progress-%d' % req_id
            params = dict(params or {})
            params['_meta'] = dict(params.get('_meta') or {}, progressToken=token)
        if params is not None:
            message['params'] = params
        label = method + (' ' + params['name'] if method == 'tools/call' and isinstance(params, dict)
                          and isinstance(params.get('name'), str) else '')
        status, headers, reply = self._exchange('POST', _encode(message), self.session_id if session else None,
                                                timeout, req_id, token, on_progress, label=label)
        if status != 200 or reply is None:
            raise OutcomeUnknown('%s: HTTP %d without a reply message' % (label, status), method=method)
        if 'error' in reply:
            raise _rpc_error(label, method, reply['error'], 200)
        if 'result' not in reply:
            raise OutcomeUnknown('%s: reply has neither result nor error' % label, method=method)
        return reply['result'], headers

    def _notify(self, method, params):
        message = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            message['params'] = params
        self._exchange('POST', _encode(message), self.session_id, self.timeout, None, None, None, label=method)

    def _exchange(self, verb, payload, session, timeout, req_id, token, on_progress, *, label=None, patience=None):
        """One HTTP exchange on a fresh connection; returns (status, headers, reply message or None).

        The line between NotReachable and OutcomeUnknown is drawn here: every failure before the connection is
        established is NotReachable (nothing written yet), every failure after it is OutcomeUnknown."""
        label = label or verb
        head = ['%s %s HTTP/1.1' % (verb, self.path), 'Host: %s:%d' % (self.host, self.port),
                'Content-Type: application/json', 'Accept: application/json, text/event-stream',
                'Content-Length: %d' % len(payload), 'Connection: close']
        if session:
            head.append('Mcp-Session-Id: ' + session)
            if self.protocol_version:
                head.append('Mcp-Protocol-Version: ' + self.protocol_version)
        try:
            request = ('\r\n'.join(head) + '\r\n\r\n').encode('latin-1') + payload
        except UnicodeEncodeError as exc:
            raise McpError('%s: request head not encodable: %s' % (label, exc), method=label, sent=False) from exc
        sock = self._open(self.connect_patience if patience is None else patience, timeout, label)
        try:
            reader = _Reader(sock, time.monotonic() + timeout)
            try:
                sock.settimeout(timeout)
                sock.sendall(request)
                status, headers, rest = _read_head(reader)
                if status == 200 and _content_type(headers) == 'text/event-stream':
                    return status, headers, _read_stream(_body(reader, rest, headers), req_id, token, on_progress)
                if status == 200 or status == 202:
                    body = _read_body(reader, rest, headers)
                else:
                    try:
                        body = _read_body(reader, rest, headers)
                    except (OSError, ValueError):
                        body = b''              # the status alone is the answer
            except McpError:
                raise
            except OSError as exc:
                raise OutcomeUnknown('%s: %s after the request was sent: %s' % (label, type(exc).__name__, exc),
                                     method=label) from exc
            except ValueError as exc:
                raise OutcomeUnknown('%s: unreadable reply: %s' % (label, exc), method=label) from exc
        finally:
            with contextlib.suppress(OSError):
                sock.close()
        if status == 404 and session:
            raise SessionRejected('%s: HTTP 404 for session %s' % (label, session), method=label, session_id=session)
        if status not in (200, 202):
            message = _loads_or_none(body)
            if isinstance(message, dict) and isinstance(message.get('error'), dict):
                raise _rpc_error(label, label, message['error'], status)
            raise HttpError('%s: HTTP %d %r' % (label, status, body[:500]), method=label, status=status, body=body)
        if req_id is None:
            return status, headers, None
        if not body:
            return status, headers, None
        try:
            message = json.loads(body)
        except ValueError as exc:
            raise OutcomeUnknown('%s: reply body is not JSON: %r' % (label, body[:200]), method=label) from exc
        if not _is_reply_to(message, req_id):
            raise OutcomeUnknown('%s: reply is not the response to id %r: %r' % (label, req_id, body[:200]),
                                 method=label)
        return status, headers, message

    def _open(self, patience, timeout, label):
        """Connect, retrying for up to `patience` seconds with capped backoff. Nothing is written before this returns,
        so every failure here is NotReachable. Patience bounds when the last attempt starts; that attempt can still
        take up to CONNECT_TIMEOUT_S (and a refused loopback connect takes about 2 s on Windows)."""
        deadline, wait = time.monotonic() + patience, BACKOFF_FIRST_S
        while True:
            try:
                return socket.create_connection((self.host, self.port), timeout=min(timeout, CONNECT_TIMEOUT_S))
            except OSError as exc:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise NotReachable('%s: no connection to %s:%d: %s' % (label, self.host, self.port, exc),
                                       method=label) from exc
                time.sleep(min(wait, left))
                wait = min(wait * 2.0, BACKOFF_CAP_S)


def _encode(message):
    return json.dumps(message, separators=(',', ':')).encode('utf-8')


def _loads_or_none(body):
    try:
        return json.loads(body)
    except ValueError:
        return None


def _rpc_error(label, method, error, status):
    if not isinstance(error, dict):
        return OutcomeUnknown('%s: malformed error object %r' % (label, error), method=method)
    return RpcError('%s: JSON-RPC error %s: %s' % (label, error.get('code'), error.get('message')), method=method,
                    code=error.get('code'), rpc_message=error.get('message', ''), data=error.get('data'),
                    status=status)


def _is_reply_to(message, req_id):
    """The response to req_id, or an error the server could not attach to any id (it sends those with id null)."""
    if not isinstance(message, dict) or 'method' in message:
        return False
    if _same_id(message.get('id'), req_id):
        return 'result' in message or 'error' in message
    return message.get('id') is None and 'error' in message


def _content_type(headers):
    return headers.get('content-type', '').split(';')[0].strip().lower()


def _read_head(reader):
    buf = b''
    while b'\r\n\r\n' not in buf:
        if len(buf) > _HEAD_LIMIT:
            raise ValueError('reply headers exceed %d bytes' % _HEAD_LIMIT)
        chunk = reader.recv()
        if not chunk:
            raise ConnectionAbortedError('connection closed before a complete reply head (%d bytes)' % len(buf))
        buf += chunk
    head, rest = buf.split(b'\r\n\r\n', 1)
    lines = head.decode('latin-1').split('\r\n')
    version, _, tail = lines[0].partition(' ')
    code = tail.partition(' ')[0]
    if not version.startswith('HTTP/') or not code.isdigit():
        raise ValueError('not an HTTP status line: %r' % lines[0])
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(':')
        if not sep:
            raise ValueError('malformed header line: %r' % line)
        headers[name.strip().lower()] = value.strip()
    return int(code), headers, rest


def _body(reader, rest, headers):
    """The body's bytes as they arrive, for any framing: chunked, Content-Length, or (with neither) everything up to
    the server's close. Stops at the end of a framed body; a close inside one raises ConnectionAbortedError."""
    coding = headers.get('transfer-encoding', 'identity').strip().lower()
    if coding == 'chunked':                     # takes precedence over any Content-Length (RFC 9112 section 6.3)
        decoder, data = ChunkedDecoder(), rest
        while True:
            piece = decoder.feed(data)
            if piece:
                yield piece
            if decoder.done:
                return
            data = reader.recv()
            if not data:
                if decoder.ended:
                    return
                raise ConnectionAbortedError('connection closed inside a chunked body')
    if coding != 'identity':
        raise ValueError('unsupported Transfer-Encoding %r' % headers['transfer-encoding'])
    length = headers.get('content-length')
    if length is None:
        if rest:
            yield rest
        while True:
            data = reader.recv()
            if not data:
                return
            yield data
    if not length.isdigit():
        raise ValueError('bad Content-Length %r' % length)
    length, got, data = int(length), 0, rest
    while True:
        piece = data[:length - got]
        if piece:
            got += len(piece)
            yield piece
        if got >= length:
            return
        data = reader.recv()
        if not data:
            raise ConnectionAbortedError('body cut short at %d of %d bytes' % (got, length))


def _read_body(reader, rest, headers):
    return b''.join(_body(reader, rest, headers))


def _read_stream(body, req_id, token, on_progress):
    """Events until the response to req_id. Progress for our token goes to on_progress; every other notification
    and any message for another id is ignored. The body ending first is a lost reply."""
    decoder = SseDecoder()
    for chunk in body:
        for data in decoder.feed(chunk):
            message = json.loads(data)
            if _is_reply_to(message, req_id):
                return message
            if (on_progress is not None and isinstance(message, dict)
                    and message.get('method') == 'notifications/progress'
                    and isinstance(message.get('params'), dict)
                    and message['params'].get('progressToken') == token):
                try:
                    on_progress(message['params'])
                except Exception as exc:
                    raise OutcomeUnknown('progress callback raised %s: %s; the reply was abandoned'
                                         % (type(exc).__name__, exc)) from exc
    raise ConnectionAbortedError('event stream ended before the response to id %r' % req_id)


def _names(names):
    return [names] if isinstance(names, str) else list(names)
