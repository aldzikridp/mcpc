---
name: mcp
description: Reach MCP servers — the packaged example is the Obscura browser — through the mcpc
  bridge. Use when a request needs real-browser page automation.
---

# MCP servers

Some capabilities are only reachable through a Model Context Protocol server. `mcpc` bridges one
to the shell so it can be used like any other CLI:

```sh
mcpc servers                                # configured server names
mcpc list <server>                          # tool names and descriptions
mcpc call <server> <tool> '{"json":"args"}' # call a tool (arguments default to {})
mcpc read <server> <uri>                    # read a resource
mcpc stop <server>                          # stop a server mcpc started with url + start
```

Run it as `mcpc` when it is on `PATH`, or as `python3 scripts/mcp_bridge.py` from the mcpc
repository. It needs only Python 3, no packages. `--json` prints the raw JSON-RPC result, and
`--config FILE` selects other server definitions (`~/.config/hax/mcp/config.json` by default),
which follow the MCP JSON configuration standard: an `mcpServers` object of `command`/`args`/`env`
entries.

**Always `mcpc servers` to see which servers exist, then `mcpc list <server>` before the first
call in a session.** Tool names and argument names are server-defined and change without notice;
guessing them wastes a round trip.

## Obscura

Server: `obscura` — local over streamable HTTP at `http://127.0.0.1:3000/mcp`. `mcpc` starts
`obscura mcp --http --port 3000` on first use and leaves it running, which is what keeps the
browser session (and its cookies) alive between calls. `url` and `start` are mcpc extensions, not
part of the configuration standard.

```sh
mcpc list obscura
mcpc call obscura browser_navigate '{"url":"https://example.com"}'
mcpc call obscura browser_markdown '{"max_chars":4000}'
mcpc call obscura browser_click '{"ref":"e12"}'
mcpc call obscura browser_close '{}'
```

Tools take no URL: the server keeps one live page. **Navigate first, then read or act.** Get
element references from `browser_snapshot` and pass them to `browser_click` / `browser_fill`;
references are only valid until the next navigation or interaction that rerenders the page. Use
`browser_markdown` for reading and `browser_snapshot` when you need to interact. Close the page
with `browser_close` when done, and tell the user `mcpc stop obscura` if they want the server
itself stopped.

## Failure modes

| Exit | Meaning | What to do |
| --- | --- | --- |
| 2 | usage, missing config, non-JSON arguments, unknown server | fix the command; `mcpc --help`; the names are in `mcpc servers` |
| 3 | transport, OAuth, or server startup failure | read the message: browser login, missing binary, port busy |
| 1 | the tool ran and reported an error | read the error text and correct the arguments |

`mcpc call` prints only the tool's content. On exit 3 the message names the cause; do not retry a
401 or a rate limit verbatim. If a server tool fails mid-conversation, say what failed instead of
retrying blind.
