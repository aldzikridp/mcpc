---
name: mcp
description: Reach ClickUp tasks and the Obscura browser through MCP via the mcpc bridge. Use
  when a request needs ClickUp (tasks, lists, docs, comments) or real-browser page automation.
---

# MCP servers

Some capabilities are only reachable through a Model Context Protocol server. `mcpc` bridges one
to the shell so it can be used like any other CLI:

```sh
mcpc servers                                # configured server names
mcpc list <server>                          # tool names and descriptions
mcpc call <server> <tool> '{"json":"args"}' # call a tool (arguments default to {})
mcpc read <server> <uri>                    # read a resource
mcpc stop <server>                          # stop a server mcpc started
```

`mcpc servers` is the way to see what exists; pick a name from it instead of guessing. `mcpc`
lives in this repository at `MCP/scripts/mcp_bridge.py`; run it as
`python3 MCP/scripts/mcp_bridge.py` from the repository root (or install it on `PATH`). It needs
only Python 3, no packages. `--json` prints the raw JSON-RPC result, and `--config FILE` selects
other server definitions (`~/.config/hax/mcp/config.json` by default).

**Always `mcpc list <server>` before the first call in a session.** Tool names and argument
names are server-defined and change without notice; guessing them wastes a round trip.

## ClickUp

Server: `clickup` — remote over streamable HTTP at `https://mcp.clickup.com/mcp`, OAuth only.
It is spawned per call through `npx -y mcp-remote`, which caches the OAuth token after the first
browser login.

```sh
mcpc list clickup
mcpc call clickup get_tasks '{"list_id":"901234567"}'
mcpc call clickup create_task '{"list_id":"901234567","name":"Fix flaky test"}'
mcpc call clickup search_tasks '{"query":"sprint"}'
```

**First run needs a human.** `mcp-remote` blocks on `Waiting for authorization`, opens a browser,
and prints a URL. Tell the user to finish the login, then retry — do not loop on the call. A call
that hangs for two minutes is this, not a slow server.

**Rate limits are tight.** Without the Everything AI add-on: 50 calls per 24 hours on Free,
300 on Unlimited, resetting on a rolling 24-hour window. Batch a question into one `get_tasks`
call with `include_closed`/filters rather than paging one task at a time, and never retry a rate
limit. There are no delete tools; deletion is out of scope for this server.

Arguments follow ClickUp's API: `list_id` for a list, `task_id` for a task (`86a1bc2d` hex ids;
`CU-` prefixes are not it), and `team_id` for workspace-wide queries. Output is text; task ids,
names, statuses, due dates, and assignees come back in a readable block. Prefer a task's `url`
from the result when reporting it to the user.

## Obscura

Server: `obscura` — local over streamable HTTP at `http://127.0.0.1:3000/mcp`. `mcpc` starts
`obscura mcp --http --port 3000` on first use and leaves it running, which is what keeps the
browser session (and its cookies) alive between calls. Set `OBSCURA_MCP_TOKEN` and pass it as a
bearer header only if you expose the port beyond loopback.

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
| 2 | usage, missing config, non-JSON arguments | fix the command; `mcpc --help` |
| 3 | transport, OAuth, or server startup failure | read the message: browser login, missing binary, port busy |
| 1 | the tool ran and reported an error | read the error text and correct the arguments |

`mcpc call` prints only the tool's content. On exit 3 the message names the cause; do not retry a
401 or a rate limit verbatim. If a server tool fails mid-conversation, say what failed instead of
retrying blind.
