"""What the editor's MCP server (:8000/mcp) exposes, from a normal shell with the editor up, and whether
MotionTerminal's toolsets are among them.

    python Scripts/epic_mcp_probe.py                              list toolsets; exit 0 iff ours are listed
    python Scripts/epic_mcp_probe.py <toolset> <tool> ['<json>']  call one toolset tool through the server

A thin command line over unreal_mcp, which owns the protocol.
Exit 0 = ok; 1 = MotionTerminal toolsets not listed / tool error; 2 = no server.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unreal_mcp  # noqa: E402

OURS = 'MotionTerminal.'


def main(argv):
    try:
        client = unreal_mcp.Client(timeout=600).connect()
    except unreal_mcp.McpError as exc:
        print('EPIC_MCP no server at %s: %s' % (unreal_mcp.DEFAULT_URL, exc))
        return 2
    with client:
        if len(argv) >= 2:
            try:
                print(client.call(argv[0], argv[1], **(json.loads(argv[2]) if len(argv) > 2 else {})).text)
            except unreal_mcp.ToolError as exc:
                print('EPIC_MCP tool error: %s' % exc)
                return 1
            return 0
        listing = client.list_toolsets()
    ours = [line.split(':', 1)[0].lstrip('- ') for line in listing.splitlines() if line.startswith('- ' + OURS)]
    print('EPIC_MCP toolsets listed: %d; MotionTerminal: %s' % (listing.count('\n- ') + 1, ours))
    return 0 if ours else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
