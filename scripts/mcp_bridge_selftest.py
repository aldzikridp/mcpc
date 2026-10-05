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
            json.dump({"mcpServers": {"t": {"command": "cat"}}}, handle)
        servers = mcpc.load_servers(path)
        check("standard mcpServers key loads", mcpc.entry_for(servers, "t")["command"] == "cat")
        check("servers lists every name", mcpc.list_servers(servers, False) == 0)
        check("servers accepts no server argument", mcpc.parse_args(["servers"]).command == "servers")
        os.environ["HAX_MCP_CONFIG"] = path
        check("env var selects the config", mcpc.config_path(None) == path)
        for argv in (["servers", "t"], ["list"]):
            try:
                mcpc.parse_args(argv)
                check("%s is rejected by the parser" % " ".join(argv), False)
            except SystemExit as exc:
                check("%s is rejected by the parser" % " ".join(argv), exc.code == 2)

        # "servers" is the older spelling and must keep working.
        with open(path, "w") as handle:
            json.dump({"servers": {"t": {"command": ["cat", "-u"]}}}, handle)
        check("legacy servers key still loads", mcpc.entry_for(mcpc.load_servers(path), "t"))

        # Unknown root keys (a VS Code "inputs" block, say) must not break loading.
        with open(path, "w") as handle:
            json.dump({"mcpServers": {}, "inputs": []}, handle)
        check("empty mcpServers object loads", mcpc.load_servers(path) == {})

        for body, message in (({"inputs": []}, "no mcpServers key"), ({"mcpServers": []}, "list key")):
            with open(path, "w") as handle:
                json.dump(body, handle)
            try:
                mcpc.load_servers(path)
                check("%s is rejected" % message, False)
            except SystemExit as exc:
                check("%s is rejected" % message, exc.code == 2)


def test_entries():
    """Both spellings of command, and env merging, without spawning anything."""
    check(
        "standard command + args",
        mcpc.argv_for("t", {"command": "npx", "args": ["-y", "server"]})
        == ["npx", "-y", "server"],
    )
    check("args alone follow command", mcpc.argv_for("t", {"command": "cat"}) == ["cat"])
    check("legacy argv array", mcpc.argv_for("t", {"command": ["cat", "-u"]}) == ["cat", "-u"])
    check(
        "argv array and args combine",
        mcpc.argv_for("t", {"command": ["cat"], "args": ["-u"]}) == ["cat", "-u"],
    )
    check("non-string argv members are converted", mcpc.argv_for("t", {"command": ["cat", 3]}) == ["cat", "3"])
    for entry, message in (
        ({}, "missing command"),
        ({"command": 3}, "non-string command"),
        ({"command": "cat", "args": "nope"}, "non-array args"),
    ):
        try:
            mcpc.argv_for("t", entry)
            check("%s is rejected" % message, False)
        except SystemExit as exc:
            check("%s is rejected" % message, exc.code == 2)

    check("env is absent by default", mcpc.env_for("t", {}) is None)
    env = mcpc.env_for("t", {"env": {"A": "1", "B": 2}})
    check("env merges over the inherited environment", env["A"] == "1" and env["B"] == "2")
    check("env keeps inherited variables", "PATH" in env)
    check("env does not mutate os.environ", "A" not in os.environ)
    for entry, message in (
        ({"env": []}, "non-object env"),
        ({"env": {"A": True}}, "boolean env"),
        ({"env": {"A": None}}, "null env"),
        ({"env": {"A": {"b": 1}}}, "nested env"),
    ):
        try:
            mcpc.env_for("t", entry)
            check("%s is rejected" % message, False)
        except SystemExit as exc:
            check("%s is rejected" % message, exc.code == 2)


def test_stdio_roundtrip():
    """A real JSON-RPC exchange with a stub server on stdin/stdout."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(
            "import json,sys,os\n"
            "for line in sys.stdin:\n"
            "    msg = json.loads(line)\n"
            "    if 'id' not in msg:\n"
            "        continue\n"
            "    if msg['method'] == 'initialize':\n"
            "        result = {'protocolVersion': '2025-06-18', 'capabilities': {}}\n"
            "    elif msg['method'] == 'tools/list':\n"
            "        result = {'tools': [{'name': 'echo', 'description': 'Echo text'}]}\n"
            "    elif msg['method'] == 'tools/call':\n"
            "        text = msg['params']['arguments']['text']\n"
            "        result = {'content': [{'type': 'text', 'text': text + '/' + os.environ.get('STUB_ENV', 'unset')}]}\n"
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
        check("tools/call round-trips", result["content"][0]["text"] == "pong/unset")
        transport.finish()

        # The same server, with env passed through the standard field.
        transport = mcpc.StdioServer(
            "stub",
            [sys.executable, "-u", stub],
            mcpc.env_for("stub", {"env": {"STUB_ENV": "from-config"}}),
        )
        transport.request(
            "initialize", {"protocolVersion": mcpc.PROTOCOL_VERSION, "capabilities": {}}
        )
        result = transport.request("tools/call", {"name": "echo", "arguments": {"text": "pong"}})
        check("env reaches the server process", result["content"][0]["text"] == "pong/from-config")
        transport.finish()
    finally:
        os.unlink(stub)


def test_stop():
    """stop must not signal a pid it cannot recognize, and must clear a stale record."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_STATE_HOME"] = tmp

        def write_pid(pid, argv):
            os.makedirs(mcpc.state_dir(), exist_ok=True)
            with open(mcpc.pid_path("t"), "w") as handle:
                json.dump({"pid": pid, "argv": argv}, handle)

        check("stop without a record is a no-op", mcpc.stop_server("t") == 0)

        # A pid whose command line does not match what was recorded: leave it alone.
        write_pid(os.getpid(), ["/definitely/not/this/process"])
        check("stop refuses a mismatched pid", mcpc.stop_server("t") == 1)
        check("stop forgets the refused record", mcpc.recorded_pid("t") is None)

        # A pid that is long gone must be reported, not claimed as stopped.
        write_pid(2 ** 22, ["/nope"])
        check("stop clears a stale record", mcpc.stop_server("t") == 0)
        check("stale record is forgotten", mcpc.recorded_pid("t") is None)


if __name__ == "__main__":
    test_samples()
    test_config()
    test_entries()
    test_stdio_roundtrip()
    test_stop()
    print("all checks passed")
