#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""mcpc - a minimal MCP client for hax.

Bridge a Model Context Protocol server to the shell, so a coding agent reaches MCP-only
capabilities through the command tool it already has. Standard library only, no coroutines.

    mcpc servers                        the configured server names, one per line
    mcpc list <server>                  tool names and descriptions, one per line
    mcpc call <server> <tool> [json]    call a tool; arguments default to {}
    mcpc read <server> <uri>            read a resource
    mcpc stop <server>                  stop a server this script started (url + start)

Global options: --config FILE (default $HAX_MCP_CONFIG or ~/.config/hax/mcp/config.json),
--json (print raw JSON-RPC results instead of readable text).

Servers are declared in the config file, using the MCP JSON configuration standard:

    {"mcpServers": {
      "filesystem": {"command": "npx",
                     "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                     "env": {"LOG_LEVEL": "debug"}},
      "obscura": {"url": "http://127.0.0.1:3000/mcp",
                  "start": ["obscura", "mcp", "--http", "--port", "3000"]}}}

`command` names the executable and `args` its arguments; `env` adds environment variables to
the ones already set. A server with `command` is spawned per call and speaks JSON-RPC over
stdin/stdout. A server with `url` is called over streamable HTTP; adding `start` makes this
script launch it on first use and leave it running, which is what keeps a browser session alive
across calls. `url` and `start` are mcpc extensions: the JSON standard describes stdio servers
only.

Exit codes: 0 success, 1 the tool reported an error, 2 usage or configuration, 3 transport,
authorization, or server startup failure.
"""

import argparse
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import NoReturn

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "mcpc", "version": "1"}
DEFAULT_CONFIG = "~/.config/hax/mcp/config.json"
CALL_TIMEOUT = float(os.environ.get("MCPC_TIMEOUT", "180"))
START_TIMEOUT = float(os.environ.get("MCPC_START_TIMEOUT", "20"))


def die(message, code=2) -> NoReturn:
    print("mcpc: " + message, file=sys.stderr)
    raise SystemExit(code)


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "hax", "mcp")


def config_path(explicit):
    return os.path.expanduser(explicit or os.environ.get("HAX_MCP_CONFIG") or DEFAULT_CONFIG)


# "mcpServers" is the MCP JSON configuration standard; "servers" is the older mcpc spelling,
# still read so existing configs keep working. VS Code also uses "servers".
SERVER_KEYS = ("mcpServers", "servers")


def load_servers(path):
    try:
        with open(path) as handle:
            data = json.load(handle)
    except OSError as exc:
        die("cannot read %s: %s" % (path, exc.strerror))
    except ValueError as exc:
        die("%s is not valid JSON: %s" % (path, exc))
    if not isinstance(data, dict):
        die('%s must be a JSON object with an "mcpServers" object' % path)
    for key in SERVER_KEYS:
        if key in data:
            servers = data[key]
            if not isinstance(servers, dict):
                die('%s: "%s" must be an object' % (path, key))
            return servers
    die('%s has no "mcpServers" object; "servers" is also accepted' % path)


def entry_for(servers, name):
    entry = servers.get(name)
    if entry is None:
        die("unknown server '%s' (defined: %s)" % (name, ", ".join(sorted(servers))))
    if not isinstance(entry, dict):
        die("server '%s' must be an object" % name)
    return entry


def argv_for(name, entry):
    """The argv to spawn, from either spelling of `command`.

    The standard splits them: `command` is the executable and `args` an array. mcpc's older
    spelling put the whole argv in `command` as an array, which is still accepted. """
    command = entry.get("command")
    args = entry.get("args") or []
    if isinstance(command, str):
        argv = [command]
    elif isinstance(command, list):
        argv = list(command)
    else:
        die('server \'%s\': "command" must be a string or an array' % name)
    if not isinstance(args, list):
        die('server \'%s\': "args" must be an array' % name)
    return [str(part) for part in argv + args]


def env_for(name, entry):
    """The child environment, or None to inherit this process's unchanged.

    `env` adds to the inherited environment rather than replacing it, matching the standard.
    Values must be strings there; a number is converted, anything else is a configuration
    error rather than something `Popen` would reject with a traceback. """
    env = entry.get("env")
    if env is None:
        return None
    if not isinstance(env, dict):
        die('server \'%s\': "env" must be an object' % name)
    merged = dict(os.environ)
    for key, value in env.items():
        if isinstance(value, bool) or value is None or isinstance(value, (list, dict)):
            die('server \'%s\': env "%s" must be a string' % (name, key))
        merged[str(key)] = str(value)
    return merged


def unwrap(message, method):
    if not isinstance(message, dict):
        die("unparsable reply to %s" % method, 3)
    if "error" in message:
        die("%s failed: %s" % (method, json.dumps(message["error"])), 3)
    return message.get("result") or {}


class StdioServer(object):
    """A server spawned per call, speaking JSON-RPC over stdin/stdout."""

    def __init__(self, name, argv, env=None):
        self.name = name
        self.argv = argv
        self.messages = queue.Queue()
        self.next_id = 1
        try:
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                bufsize=0,
            )
        except OSError as exc:
            die("%s: cannot run %s: %s" % (name, argv[0], exc.strerror), 3)
        if self.proc.stdin is None or self.proc.stdout is None:
            die("%s: could not open the server's pipes" % name, 3)
        self.stdin = self.proc.stdin
        self.stdout = self.proc.stdout
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                pass  # servers are allowed to log to stderr; tolerate stray stdout noise

    def request(self, method, params, notify=False):
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        if notify:
            self._write(message)
            return {}
        request_id = self.next_id
        self.next_id += 1
        message["id"] = request_id
        self._write(message)
        return unwrap(self._await(request_id), method)

    def _write(self, message):
        try:
            self.stdin.write(json.dumps(message).encode() + b"\n")
            self.stdin.flush()
        except OSError:
            die("%s: the server closed its input" % self.name, 3)

    def _await(self, request_id):
        deadline = time.monotonic() + CALL_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                die("%s: no reply to id %d within %.0fs" % (self.name, request_id, CALL_TIMEOUT), 3)
            try:
                message = self.messages.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                # A server that died mid-call would otherwise hang until the deadline.
                if self.proc.poll() is not None:
                    die(
                        "%s: the server exited with status %s before replying"
                        % (self.name, self.proc.returncode),
                        3,
                    )
                continue
            if message.get("id") == request_id:
                return message

    def finish(self):
        for step in (self.stdin.close, self.proc.terminate):
            try:
                step()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


class HttpServer(object):
    """A server reached over streamable HTTP, optionally started on first use."""

    def __init__(self, name, url):
        self.name = name
        self.url = url
        self.session = None
        self.next_id = 1

    def request(self, method, params, notify=False):
        message = {"jsonrpc": "2.0", "method": method, "params": params}
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if not notify:
            message["id"] = self.next_id
            self.next_id += 1
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(
            self.url, data=json.dumps(message).encode(), headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=CALL_TIMEOUT) as response:
                self.session = response.headers.get("Mcp-Session-Id") or self.session
                content_type = response.headers.get("Content-Type", "")
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            if exc.code == 401:
                die("%s: %s requires authorization (HTTP 401): %s" % (self.name, self.url, detail), 3)
            die("%s: HTTP %d from %s: %s" % (self.name, exc.code, self.url, detail), 3)
        except OSError as exc:
            die("%s: cannot reach %s: %s" % (self.name, self.url, exc), 3)
        if notify:
            return {}
        return unwrap(decode_body(content_type, body, method, message.get("id")), method)

    def finish(self):
        pass


def decode_body(content_type, body, method, request_id=None):
    """Return the JSON-RPC message for `request_id`, ignoring notifications a server interleaves.

    An SSE stream can carry several messages; the reply is the one whose id matches. """
    if "text/event-stream" in content_type:
        messages = []
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    messages.append(json.loads(line[5:].strip()))
                except ValueError:
                    continue
        for candidate in messages:
            if candidate.get("id") == request_id:
                return candidate
        for candidate in messages:
            if "id" in candidate:
                return candidate
        if messages:
            return messages[-1]
        die("no JSON-RPC message in the SSE reply to %s" % method, 3)
    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except ValueError:
        die("unparsable reply to %s: %r" % (method, body[:200]), 3)


def port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def pid_path(name):
    return os.path.join(state_dir(), name + ".json")


def recorded_pid(name):
    try:
        with open(pid_path(name)) as handle:
            return int(json.load(handle).get("pid"))
    except (OSError, ValueError, TypeError):
        return None


def recorded_argv(name):
    """The argv this script started for `name`, used to recognize the process again."""
    try:
        with open(pid_path(name)) as handle:
            argv = json.load(handle).get("argv")
    except (OSError, ValueError):
        return None
    return argv if isinstance(argv, list) and argv else None


def forget_pid(name):
    try:
        os.unlink(pid_path(name))
    except OSError:
        pass


def process_alive(pid):
    if not pid or pid <= 0:
        return False
    # A zombie still answers signal 0, but the server it used to be is gone.
    try:
        with open("/proc/%d/stat" % pid) as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ensure_running(name, entry):
    """Start the configured command if the port is not already serving a server of its own."""
    port = urllib.parse.urlsplit(entry["url"]).port or 80
    if port_open(port):
        return "already listening on port %d" % port
    start = entry.get("start")
    if not isinstance(start, list) or not start:
        die('server \'%s\': "start" must be a non-empty array of arguments' % name)
    argv = [str(part) for part in start]
    os.makedirs(state_dir(), exist_ok=True)
    log_path = os.path.join(state_dir(), name + ".log")
    with open(log_path, "ab") as log:
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        except OSError as exc:
            die("%s: cannot run %s: %s" % (name, argv[0], exc.strerror), 3)
    with open(pid_path(name), "w") as handle:
        json.dump({"pid": proc.pid, "port": port, "argv": argv}, handle)

    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if port_open(port):
            return "started %s (pid %d), log %s" % (" ".join(argv), proc.pid, log_path)
        if proc.poll() is not None:
            die(
                "%s: `%s` exited with status %d; see %s"
                % (name, " ".join(argv), proc.returncode, log_path),
                3,
            )
        time.sleep(0.2)
    die(
        "%s: `%s` did not listen on port %d within %.0fs; see %s"
        % (name, " ".join(argv), port, START_TIMEOUT, log_path),
        3,
    )


def process_command(pid):
    """The command line of `pid`, or None if it is gone or unreadable."""
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as handle:
            return handle.read().split(b"\0")[0].decode("utf-8", "replace")
    except OSError:
        return None


def stop_server(name):
    pid = recorded_pid(name)
    if pid is None:
        print("mcpc: no server started by this script for '%s'" % name)
        return 0
    if not process_alive(pid):
        # A pid file outlives a crash or a reboot, and pids get recycled: never signal a
        # process just because its number was recorded here.
        print("mcpc: %s (pid %d) is no longer running; nothing to stop" % (name, pid))
        forget_pid(name)
        return 0
    recorded = recorded_argv(name)
    started = recorded[0] if recorded else None
    if recorded and process_command(pid) != started:
        print(
            "mcpc: pid %d is not %s any more; leaving it alone and forgetting the record"
            % (pid, name),
            file=sys.stderr,
        )
        forget_pid(name)
        return 1
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if process_alive(pid):
        os.kill(pid, signal.SIGKILL)
    forget_pid(name)
    print("stopped %s (pid %d)" % (name, pid))
    return 0


def connect(name, entry):
    if entry.get("url"):
        if entry.get("start"):
            ensure_running(name, entry)
        transport = HttpServer(name, entry["url"])
    elif entry.get("command"):
        transport = StdioServer(name, argv_for(name, entry), env_for(name, entry))
    else:
        die("server '%s' needs either \"url\" or \"command\"" % name)
    transport.request(
        "initialize",
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        },
    )
    transport.request("notifications/initialized", {}, notify=True)
    return transport


def list_servers(servers, as_json):
    if as_json:
        print(json.dumps({"servers": servers}, indent=2))
        return 0
    for name in sorted(servers):
        print(name)
    return 0


def first_line(text, width=110):
    if not text:
        return ""
    line = " ".join(str(text).split())
    return line if len(line) <= width else line[: width - 1] + "\u2026"


def list_tools(transport, as_json):
    tools = transport.request("tools/list", {}).get("tools") or []
    if as_json:
        print(json.dumps({"tools": tools}, indent=2))
        return 0
    for tool in tools:
        summary = first_line(tool.get("description"))
        print(tool.get("name", "?") + (": " + summary if summary else ""))
    if not tools:
        print("mcpc: the server advertises no tools", file=sys.stderr)
    return 0


def print_result(result, as_json):
    if as_json:
        print(json.dumps(result, indent=2))
        return 1 if result.get("isError") else 0
    blocks = result.get("content") or []
    lines = []
    for block in blocks:
        kind = block.get("type")
        if kind == "text":
            lines.append(block.get("text", ""))
        elif kind == "image":
            lines.append("[image %s, %d base64 chars]" % (block.get("mimeType", "?"), len(block.get("data", ""))))
        elif kind == "resource_link":
            lines.append("[resource %s]" % block.get("uri", "?"))
        elif kind == "resource":
            lines.append(json.dumps(block.get("resource", block), indent=2))
        else:
            lines.append(json.dumps(block, indent=2))
    if not lines and result.get("structuredContent") is not None:
        lines.append(json.dumps(result["structuredContent"], indent=2))
    text = "\n".join(lines).strip()
    if text:
        print(text)
    elif not result.get("isError"):
        print(json.dumps(result, indent=2))
    return 1 if result.get("isError") else 0


def read_resource(transport, uri, as_json):
    result = transport.request("resources/read", {"uri": uri})
    if as_json:
        print(json.dumps(result, indent=2))
        return 0
    printed = False
    for content in result.get("contents") or []:
        if content.get("text") is not None:
            print(content["text"])
        elif content.get("blob") is not None:
            print("[blob %s, %d base64 chars]" % (content.get("mimeType", "?"), len(content["blob"])))
        printed = True
    if not printed:
        print(json.dumps(result, indent=2))
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="mcpc", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", metavar="FILE", help="server definitions (JSON)")
    parser.add_argument("--json", action="store_true", help="print raw JSON-RPC results")
    parser.add_argument("command", choices=["servers", "list", "call", "read", "stop"])
    parser.add_argument("server", nargs="?")
    parser.add_argument("rest", nargs="*", help="tool name and JSON arguments, or a resource URI")
    args = parser.parse_args(argv)
    if args.command == "servers" and (args.server or args.rest):
        parser.error("servers takes no arguments")
    if args.command != "servers" and not args.server:
        parser.error("%s needs a server" % args.command)
    if args.command == "call" and len(args.rest) < 1:
        parser.error("call needs a tool name")
    if args.command in ("call", "read") and len(args.rest) > 2:
        parser.error("%s takes at most two arguments" % args.command)
    return args


def main(argv):
    args = parse_args(argv)
    servers = load_servers(config_path(args.config))
    if args.command == "servers":
        return list_servers(servers, args.json)
    entry = entry_for(servers, args.server)
    if args.command == "stop":
        return stop_server(args.server)

    transport = connect(args.server, entry)
    try:
        if args.command == "list":
            return list_tools(transport, args.json)
        if args.command == "read":
            return read_resource(transport, args.rest[0], args.json)
        arguments = args.rest[1] if len(args.rest) > 1 else "{}"
        try:
            parsed = json.loads(arguments)
        except ValueError as exc:
            die("arguments for %s are not valid JSON: %s" % (args.rest[0], exc))
        if not isinstance(parsed, dict):
            die("arguments for %s must be a JSON object" % args.rest[0])
        result = transport.request("tools/call", {"name": args.rest[0], "arguments": parsed})
        return print_result(result, args.json)
    finally:
        transport.finish()


def leave(signum, frame):
    raise SystemExit(1)


def cli(argv=None):
    for caught in (signal.SIGTERM, signal.SIGINT):
        signal.signal(caught, leave)
    try:
        return main(sys.argv[1:] if argv is None else argv)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(cli())
