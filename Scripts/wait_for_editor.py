#!/usr/bin/env python
"""Block until the editor's MCP server (unreal_mcp.PORT) serves the tools this project drives it with.
Exit 0 = ready; exit 1 = 8-minute timeout. Used after every scripted editor launch.

ON TIMEOUT THIS SAYS WHY. A timeout has several causes and they need different actions, so the cause is named
rather than a port number printed: the server does not start by itself (the launch must ask for it), the
toolset plugins are separate plugins that are off unless the project enables them, and an editor that is
still loading answers nothing at all. `editor_session.py start` launches with the right flag and waits the
same way; this file is for an editor started by hand.
"""
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unreal_mcp  # noqa: E402

PORT = unreal_mcp.PORT
TIMEOUT_S = 480
START_FLAG = '-ModelContextProtocolStartServer'
# What the scripts in this folder call: the editor's Python tool, and this project's own tools.
NEEDED = (('the Python tool', unreal_mcp.PYTHON_TOOLSET, 'enable the EditorToolset plugin in MotionTerminal.uproject'),
          ("this project's tools", 'MotionTerminal.', 'build the MotionTerminal plugin (the Quickstart build step)'))


def listening():
    try:
        socket.create_connection(('127.0.0.1', PORT), timeout=1).close()
        return True
    except OSError:
        return False


def missing():
    """The needed toolsets the server does not list, as (what, fix) pairs. Raises unreal_mcp.McpError when the
    server cannot be asked."""
    with unreal_mcp.Client(timeout=30) as client:
        listed = client.list_toolsets()
    return [(what, fix) for what, name, fix in NEEDED if name not in listed]


def diagnose():
    """Print the most likely cause FIRST."""
    if not listening():
        print('CAUSE: nothing is listening on 127.0.0.1:%d.' % PORT)
        print('       The MCP server starts only when the editor is launched with %s' % START_FLAG)
        print('       (`python Scripts/editor_session.py start` passes it), or when it is started from the editor:')
        print('       console command `ModelContextProtocol.StartServer %d`.' % PORT)
        print('       If the editor was launched that way, it is still starting, failed to start, or crashed:')
        print('       check Saved/Logs/MotionTerminal.log. Do NOT Stop-Process it.')
        return
    try:
        gaps = missing()
    except unreal_mcp.McpError as e:
        print('CAUSE: the port is open but the server did not answer an MCP request (%s).' % e)
        print('       The editor is probably still loading; if this persists, check Saved/Logs/MotionTerminal.log.')
        return
    for what, fix in gaps:
        print('CAUSE: the server is up but does not list %s: %s, then relaunch.' % (what, fix))


def main():
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        if listening():
            try:
                if not missing():
                    print('editor up (MCP server :%d, tools listed)' % PORT)
                    return 0
            except unreal_mcp.McpError:
                pass
        time.sleep(5)
    print('TIMEOUT waiting for the editor MCP server :%d after %ds' % (PORT, TIMEOUT_S))
    diagnose()
    return 1


if __name__ == '__main__':
    sys.exit(main())
