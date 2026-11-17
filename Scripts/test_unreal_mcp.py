"""Host tests for unreal_mcp: request shape, reply framing, and the NotReachable / OutcomeUnknown line.

Each test runs an in-process stub HTTP server (http.server in a thread, ephemeral port) that writes raw reply bytes,
so framing faults (a cut body, a byte-at-a-time stream, a reply that never comes) are exact. No editor needed.

    python -m pytest Scripts/test_unreal_mcp.py -q
"""
import http.server
import json
import socket
import threading
import time

import pytest

import unreal_mcp
from unreal_mcp import (ChunkedDecoder, Client, HttpError, McpError, NotReachable, NotRun, OutcomeUnknown, RpcError,
                        ScriptError, SessionRejected, SseDecoder, ToolError, ToolResult)

SID = 'stub-session-1'
# a socket left open shows up as a ResourceWarning raised in a finalizer
pytestmark = pytest.mark.filterwarnings('error::ResourceWarning', 'error::pytest.PytestUnraisableExceptionWarning')


# -- stub server --

class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _handle(self, verb):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        record = {'verb': verb, 'path': self.path, 'headers': {k.lower(): v for k, v in self.headers.items()},
                  'raw': body, 'msg': json.loads(body) if body else None}
        self.server.stub.seen.append(record)
        self.server.stub.behaviour(self, record)

    def do_POST(self):
        self._handle('POST')

    def do_DELETE(self):
        self._handle('DELETE')


class Stub:
    """Runs `behaviour(handler, record)` per request; `seen` lists every request received, in order."""

    def __init__(self, behaviour, port=0):
        self.behaviour, self.seen = behaviour, []
        self.release = threading.Event()        # set at teardown so a stalling behaviour returns
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port), _Handler)
        self.httpd.daemon_threads = False       # server_close() then joins every handler thread
        self.httpd.stub = self
        self.url = 'http://127.0.0.1:%d/mcp' % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.05})
        self.thread.start()

    def stop(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)

    def methods(self, method=None):
        got = [r['msg'].get('method') if r['msg'] else r['verb'] for r in self.seen]
        return got if method is None else got.count(method)


def raw(handler, status, body=b'', ctype='application/json', headers=(), length=True, bytewise=False):
    head = ['HTTP/1.1 %d X' % status, 'Content-Type: ' + ctype] + list(headers)
    if length:
        head.append('Content-Length: %d' % len(body))
    data = ('\r\n'.join(head) + '\r\n\r\n').encode() + body
    if bytewise:
        handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for i in range(len(data)):
            handler.wfile.write(data[i:i + 1])
    else:
        handler.wfile.write(data)
    handler.close_connection = True


def chunked(handler, chunks, ctype='application/json', end=b'0\r\n\r\n', bytewise=False, hold=None):
    """A 200 reply framed as the 5.9 server frames a stream: Transfer-Encoding: chunked on a kept-alive connection.
    `chunks` are bytes (one chunk each) or (bytes, extension) pairs; `end` follows the last one. With `hold` (an
    Event), the connection stays open until it is set, so a client that waits for a close would hang."""
    body = b''
    for c in chunks:
        data, ext = c if isinstance(c, tuple) else (c, b'')
        body += b'%X%s\r\n%s\r\n' % (len(data), ext, data)
    data = ('HTTP/1.1 200 OK\r\nContent-Type: %s\r\nTransfer-Encoding: chunked\r\nConnection: keep-alive\r\n'
            'Mcp-Session-Id: %s\r\n\r\n' % (ctype, SID)).encode() + body + end
    handler.close_connection = True
    try:
        if bytewise:
            handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            for i in range(len(data)):
                handler.wfile.write(data[i:i + 1])
        else:
            handler.wfile.write(data)
    except ConnectionError:
        return                                  # the client stops reading at the result event and closes
    if hold is not None:
        hold.wait(5)


def reply(handler, record, result=None, error=None, **kw):
    msg = {'jsonrpc': '2.0', 'id': record['msg']['id']}
    msg.update({'error': error} if error is not None else {'result': result})
    raw(handler, 200, json.dumps(msg).encode(), headers=['Mcp-Session-Id: ' + SID], **kw)


def mcp(tool_behaviour):
    """A server that does the handshake and DELETE like Epic's, and hands every other request to tool_behaviour."""
    def behaviour(h, record):
        msg = record['msg']
        if record['verb'] == 'DELETE':
            raw(h, 202)
        elif msg['method'] == 'initialize':
            reply(h, record, {'protocolVersion': msg['params']['protocolVersion'], 'capabilities': {'tools': {}},
                              'serverInfo': {'name': 'stub'}})
        elif msg['method'] == 'notifications/initialized':
            raw(h, 202)
        else:
            tool_behaviour(h, record)
    return behaviour


def text_result(text, **extra):
    return dict({'content': [{'type': 'text', 'text': text}]}, **extra)


def sse_event(message, crlf=True):
    nl = '\r\n' if crlf else '\n'
    return ('event: message%sdata: %s%s%s' % (nl, json.dumps(message, ensure_ascii=False), nl, nl)).encode()


@pytest.fixture(autouse=True)
def no_leftover_threads():
    before = set(threading.enumerate())
    yield
    deadline = time.monotonic() + 2
    while set(threading.enumerate()) - before and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not set(threading.enumerate()) - before


@pytest.fixture
def stub():
    servers = []

    def make(behaviour, port=0):
        servers.append(Stub(behaviour, port))
        return servers[-1]
    yield make
    for s in servers:
        s.stop()


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


# -- request shape --

def test_initialize_and_tools_call_shape(stub):
    s = stub(mcp(lambda h, r: reply(h, r, text_result('ok'))))
    with Client(s.url) as c:
        assert c.session_id == SID
        assert c.call_tool('list_toolsets', {}).text == 'ok'
    init, initialized, call, delete = s.seen
    assert init['verb'] == 'POST' and init['path'] == '/mcp'
    assert init['msg']['method'] == 'initialize' and init['msg']['jsonrpc'] == '2.0'
    assert init['msg']['params']['protocolVersion'] == unreal_mcp.PROTOCOL_VERSION
    assert 'clientInfo' in init['msg']['params'] and 'capabilities' in init['msg']['params']
    assert 'mcp-session-id' not in init['headers']
    for r in (init, call):
        assert r['headers']['content-type'] == 'application/json'
        assert 'application/json' in r['headers']['accept'] and 'text/event-stream' in r['headers']['accept']
    assert initialized['msg'] == {'jsonrpc': '2.0', 'method': 'notifications/initialized'}
    assert initialized['headers']['mcp-session-id'] == SID
    assert call['headers']['mcp-session-id'] == SID
    assert call['headers']['mcp-protocol-version'] == unreal_mcp.PROTOCOL_VERSION
    assert call['msg']['method'] == 'tools/call' and isinstance(call['msg']['id'], int)
    assert call['msg']['params'] == {'name': 'list_toolsets', 'arguments': {}}
    assert init['msg']['id'] != call['msg']['id']
    assert delete['verb'] == 'DELETE' and delete['headers']['mcp-session-id'] == SID
    assert c.session_id is None


def test_call_uses_the_call_tool_meta_tool(stub):
    s = stub(mcp(lambda h, r: reply(h, r, text_result('{"n": 3}'))))
    with Client(s.url) as c:
        assert c.call('MotionTerminal.Tools', 'count', asset_path='/Game/A', limit=3).json() == {'n': 3}
        assert c.list_toolsets() == '{"n": 3}'
    call = s.seen[2]['msg']['params']
    assert call == {'name': 'call_tool', 'arguments': {'toolset_name': 'MotionTerminal.Tools', 'tool_name': 'count',
                                                       'arguments': {'asset_path': '/Game/A', 'limit': 3}}}
    assert s.seen[3]['msg']['params'] == {'name': 'list_toolsets', 'arguments': {}}


def test_request_before_connect_sends_nothing(stub):
    s = stub(mcp(lambda h, r: reply(h, r, {})))
    with pytest.raises(McpError) as e:
        Client(s.url).request('tools/list')
    assert e.value.sent is False and s.seen == []


# -- replies --

def test_json_reply_and_tool_result(stub):
    s = stub(mcp(lambda h, r: reply(h, r, text_result('a', structuredContent={'k': 1}))))
    with Client(s.url) as c:
        res = c.call_tool('x', {})
    assert res.text == 'a' and res.structured == {'k': 1} and not res.is_error


def test_sse_reply_one_byte_at_a_time(stub, monkeypatch):
    monkeypatch.setattr(unreal_mcp, '_RECV_SIZE', 1)

    def tool(h, r):
        body = sse_event({'jsonrpc': '2.0', 'id': r['msg']['id'], 'result': text_result('streamed é')})
        raw(h, 200, body, ctype='text/event-stream', length=False, bytewise=True)
    s = stub(mcp(tool))
    with Client(s.url) as c:
        assert c.call_tool('x').text == 'streamed é'


def test_sse_crlf_comments_multiline_notifications_and_progress(stub):
    def tool(h, r):
        rid, token = r['msg']['id'], r['msg']['params']['_meta']['progressToken']
        result = json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': text_result('done')}, indent=1)
        body = (b': keepalive\r\n\r\n'
                + sse_event({'jsonrpc': '2.0', 'method': 'notifications/progress',
                             'params': {'progressToken': token, 'progress': 1}})
                + sse_event({'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed', 'params': {}}, False)
                + sse_event({'jsonrpc': '2.0', 'method': 'notifications/progress',
                             'params': {'progressToken': 'someone-else', 'progress': 9}})
                + sse_event({'jsonrpc': '2.0', 'id': rid + 1000, 'result': text_result('not ours')})
                + sse_event({'jsonrpc': '2.0', 'method': 'notifications/progress',
                             'params': {'progressToken': token, 'progress': 2}}, False)
                + b'event: message\r\n' + b''.join(b'data: ' + line.encode() + b'\r\n'
                                                   for line in result.split('\n')) + b'\r\n')
        raw(h, 200, body, ctype='text/event-stream; charset=utf-8', length=False)
    s = stub(mcp(tool))
    seen = []
    with Client(s.url) as c:
        def on_progress(params):
            assert not c._cond._is_owned()          # no client lock held
            seen.append(params['progress'])
        assert c.call_tool('x', on_progress=on_progress).text == 'done'
    assert seen == [1, 2]


def test_progress_callback_that_raises_is_outcome_unknown(stub):
    def tool(h, r):
        token = r['msg']['params']['_meta']['progressToken']
        raw(h, 200, sse_event({'jsonrpc': '2.0', 'method': 'notifications/progress',
                               'params': {'progressToken': token, 'progress': 1}}),
            ctype='text/event-stream', length=False)
    s = stub(mcp(tool))
    with Client(s.url) as c:
        with pytest.raises(OutcomeUnknown) as e:
            c.call_tool('x', on_progress=lambda p: 1 / 0)
    assert isinstance(e.value.__cause__, ZeroDivisionError)


def test_sse_decoder_byte_splits_and_line_endings():
    stream = ': c\r\ndata: a\r\ndata:b\rid: 1\n\ndata: {"x":"é"}\r\n\r\nevent: e\n\ndata\r\r'.encode()
    whole = list(SseDecoder().feed(stream))
    d, split = SseDecoder(), []
    for i in range(len(stream)):
        split += d.feed(stream[i:i + 1])
    assert whole == split == ['a\nb', '{"x":"é"}', '']


def test_sse_decoder_cr_terminated_event_needs_no_further_byte():
    assert list(SseDecoder().feed(b'data: x\r\r')) == ['x']


def test_sse_decoder_is_lazy_past_the_wanted_event():
    events = SseDecoder().feed(b'data: ok\n\n\xff\xfe\n')
    assert next(events) == 'ok'


def test_sse_stream_ending_before_the_reply_is_outcome_unknown(stub):
    def tool(h, r):
        raw(h, 200, b': only a keepalive\n\n', ctype='text/event-stream', length=False)
    s = stub(mcp(tool))
    with Client(s.url) as c:
        with pytest.raises(OutcomeUnknown):
            c.call_tool('x')
    assert s.methods('tools/call') == 1


# -- error taxonomy --

def test_unknown_session_404_is_rejected_and_not_replayed(stub):
    def tool(h, r):
        raw(h, 404, json.dumps({'jsonrpc': '2.0', 'id': r['msg']['id'],
                                'error': {'code': -32600, 'message': 'Unknown session id'}}).encode())
    s = stub(mcp(tool))
    with Client(s.url, connect_patience=5) as c:
        with pytest.raises(SessionRejected) as e:
            c.call_tool('x')
        assert e.value.session_id == SID
    assert s.methods('tools/call') == 1 and s.methods('initialize') == 1


def test_connection_refused_is_not_reachable_and_not_retried_without_patience(stub):
    c = Client('http://127.0.0.1:%d/mcp' % free_port(), timeout=2)
    t0 = time.monotonic()
    with pytest.raises(NotReachable) as e:
        c.connect()
    assert e.value.sent is False
    assert time.monotonic() - t0 < 4


def test_connection_refused_is_retried_within_patience(stub, monkeypatch):
    refused, real = [], socket.create_connection

    def counting(*a, **kw):
        try:
            return real(*a, **kw)
        except ConnectionRefusedError:
            refused.append(1)
            raise
    monkeypatch.setattr(unreal_mcp.socket, 'create_connection', counting)
    # Windows retries a refused loopback SYN for about 2 s inside one connect, so the server comes up after that
    port, servers = free_port(), []
    late = threading.Timer(2.5, lambda: servers.append(stub(mcp(lambda h, r: reply(h, r, text_result('up'))), port)))
    late.start()
    try:
        with Client('http://127.0.0.1:%d/mcp' % port, connect_patience=8) as c:
            assert c.call_tool('x').text == 'up'
    finally:
        late.join()
    assert refused                              # the first attempts were refused and retried
    assert servers[0].methods('initialize') == 1 and servers[0].methods('tools/call') == 1


def test_patience_runs_out(stub, monkeypatch):
    calls = []

    def counting(*a, **kw):
        calls.append(1)
        raise ConnectionRefusedError('refused')
    monkeypatch.setattr(unreal_mcp.socket, 'create_connection', counting)
    t0 = time.monotonic()
    with pytest.raises(NotReachable):
        Client('http://127.0.0.1:1/mcp', connect_patience=0.7).connect()
    assert 0.6 < time.monotonic() - t0 < 2 and len(calls) >= 3


def test_close_mid_body_is_outcome_unknown_and_sent_once(stub):
    def tool(h, r):
        h.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 200\r\n\r\n{"jsonr')
        h.close_connection = True
    s = stub(mcp(tool))
    with Client(s.url, connect_patience=5) as c:
        with pytest.raises(OutcomeUnknown) as e:
            c.call_tool('x')
    assert e.value.sent is True and s.methods('tools/call') == 1


def test_close_before_any_reply_is_outcome_unknown(stub):
    def tool(h, r):
        h.close_connection = True
    s = stub(mcp(tool))
    with Client(s.url, connect_patience=5) as c:
        with pytest.raises(OutcomeUnknown):
            c.call_tool('x')
    assert s.methods('tools/call') == 1


def test_read_timeout_is_outcome_unknown(stub):
    def tool(h, r):
        s.release.wait(5)
        h.close_connection = True
    s = stub(mcp(tool))
    with Client(s.url, connect_patience=5) as c:
        t0 = time.monotonic()
        with pytest.raises(OutcomeUnknown):
            c.call_tool('x', timeout=0.3)
        assert time.monotonic() - t0 < 2
        s.release.set()
    assert s.methods('tools/call') == 1


def test_malformed_json_is_outcome_unknown(stub):
    s = stub(mcp(lambda h, r: raw(h, 200, b'{"jsonrpc": "2.0", "id": ')))
    with Client(s.url) as c:
        with pytest.raises(OutcomeUnknown):
            c.call_tool('x')


def test_reply_for_another_id_is_outcome_unknown(stub):
    s = stub(mcp(lambda h, r: raw(h, 200, json.dumps({'jsonrpc': '2.0', 'id': 999, 'result': {}}).encode())))
    with Client(s.url) as c:
        with pytest.raises(OutcomeUnknown):
            c.call_tool('x')


def test_rpc_error(stub):
    s = stub(mcp(lambda h, r: reply(h, r, error={'code': -32602, 'message': 'Unknown tool: x'})))
    with Client(s.url) as c:
        with pytest.raises(RpcError) as e:
            c.call_tool('x')
    assert e.value.code == -32602 and e.value.rpc_message == 'Unknown tool: x' and e.value.status == 200


def test_rpc_error_on_http_400_and_plain_http_error(stub):
    def tool(h, r):
        if r['msg']['params']['name'] == 'a':
            raw(h, 400, json.dumps({'jsonrpc': '2.0', 'id': None,
                                    'error': {'code': -32700, 'message': 'Invalid JSON body!'}}).encode())
        else:
            raw(h, 500, b'boom', ctype='text/plain')
    s = stub(mcp(tool))
    with Client(s.url) as c:
        with pytest.raises(RpcError) as e:
            c.call_tool('a')
        assert e.value.status == 400 and e.value.code == -32700
        with pytest.raises(HttpError) as e:
            c.call_tool('b')
        assert e.value.status == 500 and e.value.body == b'boom'


def test_is_error_result_is_tool_error(stub):
    s = stub(mcp(lambda h, r: reply(h, r, text_result('it broke', isError=True))))
    with Client(s.url) as c:
        with pytest.raises(ToolError) as e:
            c.call_tool('x')
    assert e.value.result.is_error and e.value.result.text == 'it broke'


def test_tool_result_json_on_text_raises():
    with pytest.raises(ValueError):
        ToolResult(text_result('not json')).json()
    assert ToolResult({'content': [], 'isError': 'yes'}).is_error
    assert not ToolResult({'content': [], 'isError': False}).is_error


def test_every_error_is_an_mcp_error():
    for cls in (NotReachable, OutcomeUnknown, SessionRejected, HttpError, RpcError, ToolError):
        assert issubclass(cls, McpError)
    assert NotReachable.sent is False and OutcomeUnknown.sent is True


# -- thread-safety --

def test_one_request_at_a_time(stub):
    active, peak, lock, results = [0], [0], threading.Lock(), []

    def tool(h, r):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        reply(h, r, text_result(r['msg']['params']['arguments']['n']))
        with lock:
            active[0] -= 1                      # after the reply is written: the client can only now proceed
    s = stub(mcp(tool))
    with Client(s.url) as c:
        threads = [threading.Thread(target=lambda n=n: results.append(c.call_tool('x', {'n': n}).text))
                   for n in 'abcd']
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
    assert sorted(results) == list('abcd') and peak[0] == 1


def test_session_404_is_rejected_whatever_its_framing(stub):
    s = stub(mcp(lambda h, r: raw(h, 404, b'5\r\nhello\r\n0\r\n\r\n', headers=['Transfer-Encoding: chunked'],
                                  length=False)))
    with Client(s.url) as c:
        with pytest.raises(SessionRejected):
            c.call_tool('x')


def test_odd_tool_result_shapes_do_not_escape_as_raw_errors(stub):
    s = stub(mcp(lambda h, r: reply(h, r, {'content': [{'type': 'text', 'text': None}, 'junk'], 'isError': True})))
    with Client(s.url) as c:
        with pytest.raises(ToolError) as e:
            c.call_tool('x')
    assert e.value.result.text == ''


def test_failed_initialized_notification_discards_the_session(stub):
    def behaviour(h, r):
        if r['msg']['method'] == 'initialize':
            reply(h, r, {'protocolVersion': unreal_mcp.PROTOCOL_VERSION})
        else:
            raw(h, 500, b'no')
    s = stub(behaviour)
    c = Client(s.url)
    with pytest.raises(HttpError):
        c.connect()
    assert c.session_id is None


def test_unusable_protocol_version_is_outcome_unknown(stub):
    s = stub(mcp(lambda h, r: None))
    s.behaviour = lambda h, r: reply(h, r, {'protocolVersion': 42})
    with pytest.raises(OutcomeUnknown):
        Client(s.url).connect()
    assert s.methods() == ['initialize']


# -- 5.9: chunked framing --

def test_chunked_decoder_byte_splits_extensions_trailers():
    wire = b'4;name=value\r\nWiki\r\nA ; x\r\n0123456789\r\n0;last\r\nX-Trailer: 1\r\n\r\n\r\n\r\nafter'
    whole = ChunkedDecoder()
    assert whole.feed(wire) == b'Wiki0123456789' and whole.done
    d, out = ChunkedDecoder(), b''
    for i in range(len(wire)):
        out += d.feed(wire[i:i + 1])
        assert d.done == (i >= wire.index(b'X-Trailer: 1\r\n\r\n') + 15)
    assert out == b'Wiki0123456789'
    ended = ChunkedDecoder()
    ended.feed(b'1\r\nx\r\n0\r\n')
    assert ended.ended and not ended.done
    for bad in (b'zz\r\n', b'\r\n', b'-1\r\n', b'3\r\nabcX\r\n'):
        with pytest.raises(ValueError):
            ChunkedDecoder().feed(bad)


def test_chunked_sse_one_byte_at_a_time_on_a_kept_alive_connection(stub, monkeypatch):
    monkeypatch.setattr(unreal_mcp, '_RECV_SIZE', 1)

    def tool(h, r):
        rid, token = r['msg']['id'], r['msg']['params']['_meta']['progressToken']
        result = sse_event({'jsonrpc': '2.0', 'id': rid, 'result': text_result('chunked é')})
        chunked(h, [(sse_event({'jsonrpc': '2.0', 'method': 'notifications/progress',
                                'params': {'progressToken': token, 'progress': 1}}), b';heartbeat'),
                    result[:7], result[7:]], ctype='text/event-stream', bytewise=True, hold=s.release)
    s = stub(mcp(tool))
    seen = []
    with Client(s.url, timeout=5) as c:
        t0 = time.monotonic()
        assert c.call_tool('x', on_progress=lambda p: seen.append(p['progress'])).text == 'chunked é'
        assert time.monotonic() - t0 < 3           # returned at the result, not at a close that never comes
    assert seen == [1]


def test_chunked_json_reply_stops_at_the_terminal_chunk(stub):
    def tool(h, r):
        body = json.dumps({'jsonrpc': '2.0', 'id': r['msg']['id'], 'result': text_result('{"n": 1}')}).encode()
        chunked(h, [body[:5], (body[5:30], b';a=b'), body[30:]], end=b'0\r\nX-Checksum: no\r\n\r\n', hold=s.release)
    s = stub(mcp(tool))
    with Client(s.url, timeout=5) as c:
        t0 = time.monotonic()
        assert c.call_tool('x').json() == {'n': 1}
        assert time.monotonic() - t0 < 3


@pytest.mark.parametrize('case', ['json cut mid-chunk', 'sse cut mid-chunk', 'sse ends before the reply'])
def test_chunked_body_cut_short_is_outcome_unknown(stub, case):
    def tool(h, r):
        if case == 'sse ends before the reply':
            chunked(h, [b': keepalive\n\n'], ctype='text/event-stream')
            return
        ctype = 'application/json' if case.startswith('json') else 'text/event-stream'
        h.wfile.write(('HTTP/1.1 200 OK\r\nContent-Type: %s\r\nTransfer-Encoding: chunked\r\n\r\n64\r\n' % ctype)
                      .encode() + b'data: {"jsonrpc"')
        h.close_connection = True
    s = stub(mcp(tool))
    with Client(s.url, connect_patience=5) as c:
        with pytest.raises(OutcomeUnknown) as e:
            c.call_tool('x', on_progress=(lambda p: None) if 'sse' in case else None)
    assert e.value.sent is True and s.methods('tools/call') == 1


# -- 5.9: meta-tools, refusals, sessions --

def test_describe_toolsets_and_describe_tools_shapes(stub):
    def tool(h, r):
        args = r['msg']['params']['arguments']
        key = 'toolsets' if 'toolset_names' in args else 'tools'
        names = args.get('toolset_names') or args.get('tool_names')
        reply(h, r, text_result(json.dumps({key: {n: {'description': 'd'} for n in names}, 'errors': {}})))
    s = stub(mcp(tool))
    with Client(s.url) as c:
        assert c.describe_toolsets(['A.B', 'C.D']) == {'toolsets': {'A.B': {'description': 'd'},
                                                                    'C.D': {'description': 'd'}}, 'errors': {}}
        assert c.describe_toolsets('A.B', include_schemas=True)['toolsets'] == {'A.B': {'description': 'd'}}
        assert c.describe_tools(('A.B.tool',)) == {'tools': {'A.B.tool': {'description': 'd'}}, 'errors': {}}
    calls = [r['msg']['params'] for r in s.seen if r['msg'] and r['msg'].get('method') == 'tools/call']
    assert calls == [
        {'name': 'describe_toolsets', 'arguments': {'toolset_names': ['A.B', 'C.D'], 'include_schemas': False}},
        {'name': 'describe_toolsets', 'arguments': {'toolset_names': ['A.B'], 'include_schemas': True}},
        {'name': 'describe_tools', 'arguments': {'tool_names': ['A.B.tool']}}]


# the refusal texts as the 5.9 server writes them (ModelContextProtocolServer.cpp)
MODAL = ('Not run: the Unreal Editor is showing a modal dialog, which blocks editor tools. This call made no changes. '
         'The dialog must close before this call can run, so ask the user to close it, then retry. Retrying while it '
         'is open returns this message again.')
SLOW = ('Not run: the Unreal Editor is running a slow task, which blocks editor tools. This call made no changes. '
        'The task must finish before this call can run, so wait for it to finish, then retry. Retrying while it runs '
        'returns this message again.')


def test_blocked_editor_refusal_is_not_run(stub):
    texts = iter([MODAL, SLOW, 'Not run: something else', MODAL])
    s = stub(mcp(lambda h, r: reply(h, r, text_result(next(texts), isError=True))))
    with Client(s.url) as c:
        for _ in range(2):
            with pytest.raises(NotRun) as e:
                c.call('T.S', 'tool')
            assert e.value.sent is True and e.value.ran is False and isinstance(e.value, ToolError)
        with pytest.raises(ToolError) as e:
            c.call_tool('x')
        assert type(e.value) is ToolError and e.value.ran is True
        with pytest.raises(NotRun):
            c.execute_python('1')                   # a refusal is NotRun, not ScriptError, from execute_python too


@pytest.mark.parametrize('framing', ['content-length 0', 'close-delimited'])
def test_bare_404_is_session_rejected(stub, framing):
    s = stub(mcp(lambda h, r: raw(h, 404, b'', length=framing == 'content-length 0')))
    with Client(s.url) as c:
        with pytest.raises(SessionRejected) as e:
            c.call_tool('x')
        assert e.value.session_id == SID and e.value.sent is True
    assert s.methods('tools/call') == 1


def test_execute_python_request_shape_and_result(stub):
    s = stub(mcp(lambda h, r: reply(h, r, text_result('{"returnValue": "3"}'))))
    with Client(s.url) as c:
        assert c.execute_python('1 + 2\n', timeout=7).json() == {'returnValue': '3'}
    assert s.seen[2]['msg']['params'] == {'name': 'call_tool', 'arguments': {
        'toolset_name': 'editor_toolset.toolsets.python_scripting.PythonScriptingTools',
        'tool_name': 'execute_python', 'arguments': {'script': {'content': '1 + 2\n'}}}}


def test_execute_python_failure_is_script_error(stub):
    tb = ('RuntimeError: line 2: ZeroDivisionError: division by zero\nTraceback (script frames only):\n'
          '  File "<script>", line 2, in <module>')
    s = stub(mcp(lambda h, r: reply(h, r, text_result(tb, isError=True))))
    with Client(s.url) as c:
        with pytest.raises(ScriptError) as e:
            c.execute_python('x = 1\n1 / 0')
    assert e.value.traceback == tb and e.value.ran is True and isinstance(e.value, ToolError)
    assert str(e.value).startswith('execute_python: RuntimeError')


@pytest.mark.parametrize('text, value', [
    ('{"returnValue": "[1, 2]"}', [1, 2]),       # a json.dumps string, wrapped
    ('"[1, 2]"', [1, 2]),                         # the same string, bare JSON
    ('[1, 2]', [1, 2]),
    ('{"returnValue": "C:/x"}', 'C:/x'),         # a plain string, wrapped
    ('C:/x', 'C:/x'),                             # a plain string, bare
    ('{"a": 1}', {'a': 1}),
])
def test_tool_result_value_reads_every_return_form(text, value):
    assert unreal_mcp.ToolResult(text_result(text)).value() == value


def _run_here(source):
    """What execute_python does with the submitted text: run it, return the final expression's value."""
    body, last = source.rstrip('\n').rsplit('\n', 1)
    ns = {}
    exec(body, ns)
    return eval(last, ns)


def test_editor_script_runs_the_file_with_config_as_its_globals(tmp_path):
    f = tmp_path / 'job.py'
    f.write_text('def doubled():\n    return [x * 2 for x in ITEMS]\nMT_RESULT = {"out": doubled(), "name": __name__}\n')
    outcome = json.loads(_run_here(unreal_mcp.editor_script(f, {'ITEMS': [1, 2]})))
    assert outcome == {'error': None, 'result': {'out': [2, 4], 'name': '__main__'}}


def test_editor_script_returns_the_files_own_traceback(tmp_path):
    f = tmp_path / 'job.py'
    f.write_text('x = 1\nraise KeyError("missing")\n')
    outcome = json.loads(_run_here(unreal_mcp.editor_script(f, {})))
    assert outcome['result'] is None
    assert 'job.py", line 2' in outcome['error'] and "KeyError: 'missing'" in outcome['error']


@pytest.mark.parametrize('config', [{'not a name': 1}, {'__file__': 'x'}, {'OBJ': object()}])
def test_editor_script_refuses_config_it_cannot_write_as_a_literal(config):
    with pytest.raises(ValueError):
        unreal_mcp.editor_script('C:/job.py', config)


def test_run_file_returns_the_result_and_raises_the_traceback(stub):
    outcomes = iter([{'error': None, 'result': {'ok': 1}},
                     {'error': 'Traceback ...\n  File "C:/job.py", line 2\nKeyError: \'missing\'\n', 'result': None},
                     'not an outcome'])
    s = stub(mcp(lambda h, r: reply(h, r, text_result(json.dumps({'returnValue': json.dumps(next(outcomes))})))))
    with Client(s.url) as c:
        assert c.run_file('C:/job.py', ROOT='/Game/X') == {'ok': 1}
        with pytest.raises(ScriptError) as e:
            c.run_file('C:/job.py')
        assert str(e.value) == "run_file C:/job.py: KeyError: 'missing'" and 'line 2' in e.value.traceback
        with pytest.raises(ScriptError):
            c.run_file('C:/job.py')
    sent = s.seen[2]['msg']['params']['arguments']['arguments']['script']['content']
    assert "_mt_ns = {'ROOT': '/Game/X'}" in sent
