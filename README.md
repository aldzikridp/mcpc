# MCP

Reach Model Context Protocol servers from hax without waiting for a native MCP client.

`scripts/mcp_bridge.py` (`mcpc`) is a small MCP client: it spawns stdio servers or talks
streamable HTTP to remote ones, and exposes the protocol as five shell commands.
`example/skills/mcp/` teaches the agent when and how to use it. Nothing here is compiled into
hax and nothing outside this directory changes.

```
MCP/
├── README.md
├── pyproject.toml              # optional: installs mcpc as a console script
├── scripts/
│   ├── mcp_bridge.py           # mcpc: servers, ps, list, call, read, stop
│   ├── mcp_bridge_selftest.py  # python3 mcp_bridge_selftest.py
│   └── mcp.json                # example server definitions (packaged)
└── example/
    └── skills/
        └── mcp/
            ├── SKILL.md            # the bridge: configuring, calling, and managing servers
            └── filesystem/
                └── SKILL.md        # one skill per server, for that server's tools
```

## Install

```sh
# Either install the console script, or copy the single stdlib file onto PATH.
pipx install ./MCP && mcpc --help
install -m755 MCP/scripts/mcp_bridge.py ~/.local/bin/mcpc

# Server definitions, in the MCP JSON configuration standard.
mkdir -p ~/.config/hax/mcp
cp MCP/example/mcp.json ~/.config/hax/mcp/config.json

# Make the agent aware of it (discovered for every project).
mkdir -p ~/.config/hax/skills
cp -r MCP/example/skills/mcp ~/.config/hax/skills/
```

Config resolution, highest priority first: `--config FILE`, then `$HAX_MCP_CONFIG` (each names one
file and stands alone), then the global `~/.config/hax/mcp/config.json` with the nearest `.mcp.json`
at or above the working directory **merged over** it. A project entry whose name matches a global
one replaces it; global names the project does not mention are inherited. The search for
`.mcp.json` stops at the repository root and never ascends past your home directory. A project can
also shadow the skill by putting its own at `.agents/skills/mcp/SKILL.md`. Python 3 is the only
requirement; there are no packages to install.

`pyproject.toml` packages the bridge as the `mcpc` console script (`pip install ./MCP`,
`pipx install ./MCP`). Installing is optional: the script runs straight from a checkout.

## Use

```sh
mcpc servers                                # configured server names
mcpc ps                                     # background servers mcpc started
mcpc list <server>                          # tool names and descriptions
mcpc call <server> <tool> '{"key":"value"}' # call a tool (arguments default to {})
mcpc read <server> <uri>                    # read a resource
mcpc stop <server>                          # stop a server mcpc started with url + start
```

`mcpc --help` documents the rest. Exit codes: `0` success, `1` the tool reported an error,
`2` usage or configuration, `3` transport, authorization, or server startup failure.

## Server definitions

Servers live in one JSON file, in the MCP JSON configuration standard. Each entry is either a
command (stdio) or — an mcpc extension — a URL (streamable HTTP):

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {"LOG_LEVEL": "debug"}
    },
    "remote": {"url": "https://example.com/mcp"},
    "local": {
      "url": "http://127.0.0.1:4000/mcp",
      "start": ["my-server", "--http", "--port", "4000"]
    }
  }
}
```

- `command` and `args` name the executable and its arguments. The process is spawned per call and
  speaks JSON-RPC over its stdin/stdout. Use it for local servers, including `npx` wrappers that
  add OAuth to a remote one.
- `env` adds environment variables for that process, on top of the ones already set. Values must
  be strings, as the standard requires; a number is converted, anything else (a boolean, null, a
  list, an object) is a configuration error.
- `url` uses streamable HTTP, which suits servers that keep expensive state — a browser, a
  long-lived session — because one server process serves every call. `url` is not part of the
  standard, which describes stdio servers only: a file that uses it is not portable to other
  clients.
- `start` is optional and complements `url`: when the port is not already listening, `mcpc` runs
  it in the background on first use, records its pid under `$XDG_STATE_HOME/hax/mcp/`, and leaves
  it running until `mcpc stop <server>`. Without `start`, something else must be listening.
  `mcpc stop` only signals a process whose command line still matches the one it recorded.

`url` and `start` are mcpc extensions — the standard describes stdio servers only — so they are
also the two fields no other client will understand. Use `url` when one long-lived process should
serve every call; use `command` when a fresh process per call is fine.

### `url` — a server someone else already runs

```json
{
  "mcpServers": {
    "remote": {"url": "https://example.com/mcp"}
  }
}
```

`url` points at a streamable HTTP endpoint and is used as-is; mcpc never starts anything for it,
so something must already be listening. If nothing is, you get an exit-3 connection error:

```sh
mcpc list remote
mcpc: remote: cannot reach https://example.com/mcp: <urlopen error [Errno 111] Connection refused>
```

Prefer this when the server is remote, or when you start the daemon yourself and want mcpc to
leave its lifetime alone.

### `url` + `start` — mcpc starts it, and keeps it running

```json
{
  "mcpServers": {
    "browser": {
      "url": "http://127.0.0.1:3000/mcp",
      "start": ["my-server", "mcp", "--http", "--port", "3000"]
    }
  }
}
```

`start` is an argv array. On first use, if the URL's port is not already accepting connections,
mcpc runs it in the background (detached, so it outlives the call), appends its output to
`$XDG_STATE_HOME/hax/mcp/<server>.log`, and records its pid. It then waits up to
`$MCPC_START_TIMEOUT` (20s) for the port to open. `start` is always a list — `"start": "my-server"`
is a configuration error, because there would be no way to pass the port and mode flags.

This is the shape for a server that holds state between calls: a browser session, a warm cache. A
server that keeps one live page would lose it (and its cookies) if a fresh process served every
call, so pinning it to one long-lived process is the point of `url` + `start`.

It is idempotent, and a port already listening is taken as the server:

```sh
mcpc list browser      # starts it if needed, then lists tools
mcpc ps                # browser  alive, pid 31144, port 3000, up 2m
mcpc stop browser      # stops it, and clears the record
```

Read the failure modes off the message, which names the cause:

```sh
mcpc: broken: cannot run definitely-not-a-real-binary: No such file or directory   # exit 3
mcpc: broken: `my-server --http` did not listen on port 4000 within 20s; see ...log # exit 3
mcpc: broken: `my-server --http` exited with status 1; see ...log                   # exit 3
```

`start` needs no privilege mcpc does not already have, but it does mean the process survives the
command — that is the point — so use `mcpc ps` and `mcpc stop` to manage it rather than expecting
it to go away on its own.

`mcpc ps` lists the servers mcpc started, one per line, with whether the process is still there
(`alive` or `stale`), its pid, its port if it has one, and how long ago it was started. It reads
the same records `mcpc stop` acts on, needs no config file, and so still lists a server that has
since been removed from the configuration. A stale record — a crash, a reboot, a recycled pid — is
shown rather than hidden, and `mcpc stop <server>` clears it. Only `url` (+ `start`) servers ever
appear there: a `command` server is spawned per call and never persists.

The shipped `scripts/mcp.json` shows the two shapes that need no private setup — a stdio `command`
(`filesystem`) and a bare `url` (`remote`). The `url` + `start` form is shown above; it is for a
server mcpc should launch and keep running. All of these are examples; delete or replace them.

`mcpServers` is the standard key; mcpc also accepts `servers` (its older spelling, and the key
VS Code uses) and ignores root keys it does not know, such as VS Code's `inputs`. So a Claude
Desktop, Cursor, or VS Code file drops in unchanged — except that an entry using a `url` will not
be understood by those clients in turn.

A `.mcp.json` in a project is picked up automatically (see [Install](#install)), which makes a
per-project server list — including a shared, checked-in one — work without touching the global
config. It merges over the global file: same name wins from the project, other names are
inherited. Keep credentials out of a checked-in project file: put them in an untracked file, or
in the global config.

## Tests

```sh
python3 scripts/mcp_bridge_selftest.py
```

Covers result rendering, the SSE reply decoder, config resolution (both spellings of `command`
and `args`, `env` merging and its validation, and the `.mcp.json` search, merge, and precedence),
the `ps` listing and `stop` pid checks, and a real JSON-RPC round trip against a stub server.

## Why not build it into hax

`docs/philosophy.md` says a capability the model needs is a CLI tool with a README, and MCP is
exactly that capability: the protocol is JSON-RPC over a pipe, which a subprocess already
provides. A native client would add an event source, a dynamic tool registry, per-endpoint OAuth,
and a config schema to the core loop for the same reachable tools. Revisit that trade when the
bridge's limits start to bite:

- every stdio call pays a process spawn and a fresh `initialize` (fine for occasional calls,
  wasteful for chatty servers);
- tool results arrive as text through the shell, so no inline image or diff rendering;
- the agent chooses tools by name from a skill instead of seeing their schemas in the request.

Until then the bridge is the smaller, testable, out-of-process answer.
