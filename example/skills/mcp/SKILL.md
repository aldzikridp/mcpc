---
name: mcp
description: Reach MCP servers through the mcpc bridge. Use when a request needs a capability
  that only an MCP server provides, or when a specific server skill applies.
---

# MCP servers

Some capabilities are only reachable through a Model Context Protocol server. `mcpc` bridges one
to the shell so it can be used like any other CLI:

```sh
mcpc servers                                # configured server names
mcpc ps                                     # background servers mcpc started
mcpc list <server>                          # tool names and descriptions
mcpc call <server> <tool> '{"json":"args"}' # call a tool (arguments default to {})
mcpc read <server> <uri>                    # read a resource
mcpc stop <server>                          # stop a server mcpc started with url + start
```

Run it as `mcpc` when it is on `PATH`, or as `python3 scripts/mcp_bridge.py` from the mcpc
repository. It needs only Python 3, no packages. `--json` prints the raw JSON-RPC result, and
`--config FILE` selects one specific file.

Server definitions follow the MCP JSON configuration standard: an `mcpServers` object of
`command`/`args`/`env` entries. By default the global `~/.config/hax/mcp/config.json` is read and a
`.mcp.json` in the project tree is **merged over** it — a name both define comes from the project,
and other global names stay available. `--config` and `$HAX_MCP_CONFIG` each name one file and
stand alone. A `url` (plus optional `start`) entry instead of `command` is an mcpc extension, not
part of the standard, and marks a server that outlives a single call.

**Always `mcpc servers` to see which servers exist, then `mcpc list <server>` before the first
call in a session.** Tool names and argument names are server-defined and change without notice;
guessing them wastes a round trip.

`mcpc ps` shows the background servers mcpc started, with `alive` or `stale`, pid, port, and how
long ago they started. Check it before starting anything, and after a call fails, to see whether a
server is still up. Only `url` + `start` servers ever appear there: a `command` server is spawned
per call and never persists. A `stale` entry is a leftover record for a process that is gone; it
costs nothing to leave, and `mcpc stop <server>` clears it. `stop` works on any server mcpc
started, including one that has since been removed from the config.

## Server skills

This file covers the bridge itself: configuring servers, listing them, calling them, and managing
the ones that run in the background. What each server *does* — its tools, their arguments, and the
conventions for using them — belongs in its own skill beside this file. Read the matching one
before working with a server.

| Server | Skill | Use for |
| --- | --- | --- |
| `filesystem` | `mcp/filesystem/SKILL.md` | Files in the server's allowed directories: read, write, search, inspect |

Add a row, and a directory beside this file, for each server you configure. When a server's tools
change, only its own skill needs updating. Delete the example row if you are not using that server.

## Failure modes

| Exit | Meaning | What to do |
| --- | --- | --- |
| 2 | usage, missing config, non-JSON arguments, unknown server | fix the command; `mcpc --help`; the names are in `mcpc servers` |
| 3 | transport, OAuth, or server startup failure | read the message: browser login, missing binary, port busy |
| 1 | the tool ran and reported an error | read the error text and correct the arguments |

`mcpc call` prints only the tool's content. On exit 3 the message names the cause; do not retry a
401 or a rate limit verbatim. If a server tool fails mid-conversation, say what failed instead of
retrying blind.
