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
        check("env var selects the config", mcpc.config_files(None) == [path])
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


def test_merge():
    """A project file overlays the global one: same name overridden, new names inherited."""
    with tempfile.TemporaryDirectory() as tmp:
        global_file = os.path.join(tmp, "config.json")
        project_file = os.path.join(tmp, ".mcp.json")
        with open(global_file, "w") as handle:
            json.dump({"mcpServers": {"shared": {"command": "global"}, "only_global": {"command": "g"}}}, handle)
        with open(project_file, "w") as handle:
            json.dump({"mcpServers": {"shared": {"command": "project"}, "only_project": {"command": "p"}}}, handle)

        merged = mcpc.load_servers([global_file, project_file])
        check("a project entry overrides a global one", merged["shared"]["command"] == "project")
        check("a global entry the project omits is inherited", merged["only_global"]["command"] == "g")
        check("a project-only entry is added", merged["only_project"]["command"] == "p")

        # Order is the priority: the later file wins.
        reversed_merge = mcpc.load_servers([project_file, global_file])
        check("list order decides the override", reversed_merge["shared"]["command"] == "global")

        # One file still works, and a missing file is not an error when another provides servers.
        check("a single file still loads", mcpc.load_servers([global_file])["shared"]["command"] == "global")
        check("a bare path is accepted", mcpc.load_servers(global_file)["shared"]["command"] == "global")
        partial = mcpc.load_servers([os.path.join(tmp, "absent.json"), project_file])
        check("a missing file is skipped when another exists", partial["only_project"]["command"] == "p")

        # Nothing readable at all is still a configuration error.
        try:
            mcpc.load_servers([os.path.join(tmp, "absent.json"), os.path.join(tmp, "also-absent.json")])
            check("all files missing is rejected", False)
        except SystemExit as exc:
            check("all files missing is rejected", exc.code == 2)

        # Malformed JSON is fatal even when the other file is fine.
        broken = os.path.join(tmp, "broken.json")
        with open(broken, "w") as handle:
            handle.write("{not json")
        try:
            mcpc.load_servers([broken, project_file])
            check("malformed JSON is rejected", False)
        except SystemExit as exc:
            check("malformed JSON is rejected", exc.code == 2)


def test_project_config():
    """The working-directory .mcp.json search, and its precedence."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "repo")
        nested = os.path.join(root, "pkg", "deep")
        os.makedirs(nested)
        os.makedirs(os.path.join(root, ".git"))
        project = os.path.join(root, ".mcp.json")
        check("no .mcp.json anywhere above", mcpc.project_config(nested) is None)

        with open(project, "w") as handle:
            json.dump({"mcpServers": {"p": {"command": "cat"}}}, handle)
        check("a project file is found from a nested directory", mcpc.project_config(nested) == project)
        check("it is found from the root itself", mcpc.project_config(root) == project)

        # A file outside the repository must not be picked up.
        outside = os.path.join(tmp, ".mcp.json")
        with open(outside, "w") as handle:
            json.dump({"mcpServers": {"o": {"command": "cat"}}}, handle)
        check("the walk stops at the repo root", mcpc.project_config(nested) == project)

        # Only the nearest one applies.
        nearer = os.path.join(root, "pkg", ".mcp.json")
        with open(nearer, "w") as handle:
            json.dump({"mcpServers": {"n": {"command": "cat"}}}, handle)
        check("the nearest project file wins", mcpc.project_config(nested) == nearer)

        # Precedence: --config and the env var each stand alone; otherwise the project file
        # is merged over the global default.
        os.environ.pop("HAX_MCP_CONFIG", None)
        global_default = os.path.expanduser(mcpc.DEFAULT_CONFIG)
        cwd = os.getcwd()
        os.chdir(nested)
        try:
            check(
                "the project file is merged over the global default",
                mcpc.config_files(None) == [global_default, nearer],
            )
            os.environ["HAX_MCP_CONFIG"] = "/tmp/from-env.json"
            check("the env var stands alone", mcpc.config_files(None) == ["/tmp/from-env.json"])
            check("--config stands alone", mcpc.config_files("/tmp/explicit.json") == ["/tmp/explicit.json"])
        finally:
            os.chdir(cwd)
            os.environ.pop("HAX_MCP_CONFIG", None)

    # The home directory itself is honoured, but the walk does not ascend past it: HOME is
    # normally not a repository, so ".git" alone would let the search escape into /home.
    with tempfile.TemporaryDirectory() as home:
        real_home = os.environ.get("HOME")
        os.environ["HOME"] = home
        deep = os.path.join(home, "work", "nested")
        os.makedirs(deep)
        above = os.path.join(os.path.dirname(home), mcpc.PROJECT_CONFIG)
        with open(above, "w") as handle:
            json.dump({"mcpServers": {"above": {"command": "cat"}}}, handle)
        try:
            check("a file above $HOME is not found", mcpc.project_config(deep) is None)
            at_home = os.path.join(home, mcpc.PROJECT_CONFIG)
            with open(at_home, "w") as handle:
                json.dump({"mcpServers": {"h": {"command": "cat"}}}, handle)
            check("a file in $HOME is still found", mcpc.project_config(deep) == at_home)
        finally:
            os.unlink(above)
            if real_home is not None:
                os.environ["HOME"] = real_home

    # Where there is no project file, the global default stands.
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, ".git"))  # bound the walk, so the test is hermetic
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            check(
                "the global default stands alone with no project file",
                mcpc.config_files(None) == [os.path.expanduser(mcpc.DEFAULT_CONFIG)],
            )
        finally:
            os.chdir(cwd)


def test_ps():
    """ps reports the pid records, and says which processes are actually still there."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_STATE_HOME"] = tmp
        check("nothing running is an empty list", mcpc.running_servers() == [])
        check("ps with nothing running succeeds", mcpc.list_running(None, False) == 0)

        os.makedirs(mcpc.state_dir(), exist_ok=True)

        # The command line of this test process, as the kernel reports it. argv[0] is the
        # *invocation* name ("python3"), not the resolved sys.executable, which is exactly
        # what mcpc records: the string it handed to Popen.
        self_cmd = mcpc.process_command(os.getpid())

        def write_pid(name, pid, argv, port=None):
            with open(mcpc.pid_path(name), "w") as handle:
                json.dump({"pid": pid, "argv": argv, "port": port}, handle)

        write_pid("live", os.getpid(), [self_cmd, "-c", "pass"], port=3000)
        # A pid past the maximum is not running.
        write_pid("stale", 2 ** 22, ["my-server", "mcp"], port=3001)

        found = {server["name"]: server for server in mcpc.running_servers()}
        check("ps lists every recorded server", set(found) == {"live", "stale"})
        check("a live process is alive", found["live"]["alive"] is True)
        check("its port is reported", found["live"]["port"] == 3000)
        check("its command is reported", found["live"]["command"].startswith(self_cmd))
        check("a dead pid is not alive", found["stale"]["alive"] is False)

        # Same pid, but the command line no longer matches what was recorded.
        write_pid("recycled", os.getpid(), ["/definitely/not/this/process"])
        found = {server["name"]: server for server in mcpc.running_servers()}
        check("a recycled pid is not alive", found["recycled"]["alive"] is False)

        check("ps exits 0 with entries", mcpc.list_running(None, False) == 0)
        os.unlink(mcpc.pid_path("live"))
        os.unlink(mcpc.pid_path("stale"))
        os.unlink(mcpc.pid_path("recycled"))

        # A file that is not a pid record at all must not crash the listing.
        with open(mcpc.pid_path("garbage"), "w") as handle:
            handle.write("{not json")
        check("a malformed record is tolerated", mcpc.running_servers()[0]["pid"] is None)
        os.unlink(mcpc.pid_path("garbage"))

        # A server that is no longer in any config can still be listed and stopped: both act
        # on the recorded state, not on the configuration.
        write_pid("dropped", os.getpid(), ["/definitely/not/this/process"])
        check("a dropped server still appears", [s["name"] for s in mcpc.running_servers()] == ["dropped"])
        check("and can still be stopped", mcpc.stop_server("dropped") == 1)
        check("which clears its record", mcpc.recorded_pid("dropped") is None)

        check("durations render", mcpc.format_duration(5) == "5s")
        check("minutes render", mcpc.format_duration(120) == "2m")
        check("hours render", mcpc.format_duration(3720) == "1h02m")
        check("days render", mcpc.format_duration(90000) == "1d")


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
    test_merge()
    test_project_config()
    test_ps()
    test_stdio_roundtrip()
    test_stop()
    print("all checks passed")
