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
│   ├── mcp_bridge.py           # mcpc: servers, list, call, read, stop
│   ├── mcp_bridge_selftest.py  # python3 mcp_bridge_selftest.py
│   └── mcp.json                # example server definitions (packaged)
└── example/
    └── skills/
        └── mcp/
            └── SKILL.md        # when the agent should reach for mcpc
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

The bridge reads `~/.config/hax/mcp/config.json` by default and `HAX_MCP_CONFIG` or `--config FILE`
when set. A project can shadow the skill by putting its own at `.agents/skills/mcp/SKILL.md`.
Python 3 is the only requirement; there are no packages to install.

`pyproject.toml` packages the bridge as the `mcpc` console script (`pip install ./MCP`,
`pipx install ./MCP`). Installing is optional: the script runs straight from a checkout.

## Use

```sh
mcpc servers                                # configured server names
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

The shipped `scripts/mcp.json` defines working examples. Both are examples; delete or replace
them.

`mcpServers` is the standard key; mcpc also accepts `servers` (its older spelling, and the key
VS Code uses) and ignores root keys it does not know, such as VS Code's `inputs`. So a Claude
Desktop, Cursor, or VS Code file drops in unchanged — except that an entry using a `url` will not
be understood by those clients in turn.

## Tests

```sh
python3 scripts/mcp_bridge_selftest.py
```

Covers result rendering, the SSE reply decoder, config resolution, both spellings of `command`
and `args`, `env` merging and its validation, the `stop` pid checks, and a real JSON-RPC round
trip against a stub server.

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
