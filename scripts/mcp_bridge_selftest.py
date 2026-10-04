#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Self-check for mcp_bridge.py. Run: python3 mcp_bridge_selftest.py"""

import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mcp_bridge", os.path.join(HERE, "mcp_bridge.py"))
assert spec is not None and spec.loader is not None
mcpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mcpc)


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        raise SystemExit(1)


def test_samples():
    tool = {
        "name": "get_tasks",
        "description": "List tasks\nin a list",
    }
    check("tool line is one line", mcpc.first_line(tool["description"]) == "List tasks in a list")
    check("long description is truncated", len(mcpc.first_line("x" * 400)) == 110)

    result = {"content": [{"type": "text", "text": "hello"}]}
    check("text result is not an error", mcpc.print_result(result, False) == 0)
    check("isError result fails", mcpc.print_result({"isError": True, "content": []}, False) == 1)
    check(
        "structured content falls back",
        mcpc.print_result({"structuredContent": {"a": 1}}, False) == 0,
    )

    body = [
        "event: message",
        'data: {"jsonrpc":"2.0","method":"notifications/message","params":{}}',
        "",
        'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"x"}]}}',
        "",
    ]
    decoded = mcpc.decode_body("text/event-stream", "\n".join(body), "tools/list", 1)
    check("sse reply is matched by id", "tools" in json.dumps(decoded))
    check(
        "json reply decodes",
        mcpc.decode_body("application/json", '{"result":{"ok":1}}', "x")["result"]["ok"] == 1,
    )
    check("empty reply decodes", mcpc.decode_body("application/json", "", "x") == {})


def test_config():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mcp.json")
        with open(path, "w") as handle:
            json.dump({"servers": {"t": {"command": ["cat"]}}}, handle)
        servers = mcpc.load_servers(path)
        check("config loads a server", mcpc.entry_for(servers, "t")["command"] == ["cat"])
        os.environ["HAX_MCP_CONFIG"] = path
        check("env var selects the config", mcpc.config_path(None) == path)


def test_stdio_roundtrip():
    """A real JSON-RPC exchange with a stub server on stdin/stdout."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(
            "import json,sys\n"
            "for line in sys.stdin:\n"
            "    msg = json.loads(line)\n"
            "    if 'id' not in msg:\n"
            "        continue\n"
            "    if msg['method'] == 'initialize':\n"
            "        result = {'protocolVersion': '2025-06-18', 'capabilities': {}}\n"
            "    elif msg['method'] == 'tools/list':\n"
            "        result = {'tools': [{'name': 'echo', 'description': 'Echo text'}]}\n"
            "    elif msg['method'] == 'tools/call':\n"
            "        result = {'content': [{'type': 'text', 'text': msg['params']['arguments']['text']}]}\n"
            "    else:\n"
            "        result = {}\n"
            "    print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result}), flush=True)\n"
        )
        stub = handle.name
    try:
        transport = mcpc.StdioServer("stub", [sys.executable, "-u", stub])
        transport.request(
            "initialize", {"protocolVersion": mcpc.PROTOCOL_VERSION, "capabilities": {}}
        )
        transport.request("notifications/initialized", {}, notify=True)
        tools = transport.request("tools/list", {})["tools"]
        check("tools/list returns the stub tool", tools[0]["name"] == "echo")
        result = transport.request("tools/call", {"name": "echo", "arguments": {"text": "pong"}})
        check("tools/call round-trips", result["content"][0]["text"] == "pong")
        transport.finish()
    finally:
        os.unlink(stub)


if __name__ == "__main__":
    test_samples()
    test_config()
    test_stdio_roundtrip()
    print("all checks passed")
