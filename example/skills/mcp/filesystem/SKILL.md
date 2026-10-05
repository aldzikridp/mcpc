---
name: filesystem
description: Read, write, search, and inspect files under a fixed set of allowed directories
  through the reference filesystem MCP server, reached with the mcpc bridge. Use for local file
  work when the server's sandbox — not the shell — should decide what is reachable.
---

# Filesystem

Server: `filesystem` — stdio, spawned per call via `npx`, confined to the directories listed in
the config's `args`. The example entry allows `/tmp`; it has no access to anything else, so a path
outside the list fails rather than being silently rewritten.

```sh
mcpc list filesystem
mcpc call filesystem list_allowed_directories '{}'
mcpc call filesystem list_directory '{"path":"/tmp"}'
mcpc call filesystem read_text_file '{"path":"/tmp/notes.txt"}'
```

The server is spawned per call, so every call pays an `npx` start (a few seconds) and keeps no
state. That also means there is no session to close and nothing for `mcpc ps` to show.

## The tools worth knowing

**Reading.** `read_text_file` reads one file as text and handles the encoding; `read_multiple_files`
reads several at once when you need more than one — cheaper than a call each. `read_file` is
deprecated in favour of `read_text_file`, so reach for the latter. `read_media_file` returns a
base64 content block with its MIME type for images and audio; `mcpc call` prints it as
`[image <type>, N base64 chars]` rather than the bytes, so it is not useful for reading an image
directly through the bridge.

**Listing.** `list_directory` is the plain listing, `list_directory_with_sizes` adds sizes, and
`directory_tree` returns the whole tree as JSON. Start with `list_directory` and widen only if you
need to.

**Finding.** `search_files` recurses for glob patterns; `get_file_info` gives metadata for one
path. Both beat guessing a path and reading it.

**Writing.** `write_file` creates or **completely overwrites** a file — it does not merge, so read
before writing if the existing content matters. `edit_file` makes line-based edits and returns a
diff, which is the safer choice for changing a file that already has content. `create_directory`
makes nested directories in one call, and `move_file` moves or renames.

**Access.** `list_allowed_directories` reports the sandbox, and a rejected path says which roots
apply. A denial is a tool error (exit 1), not a crash, so read the text and correct the path:

```sh
mcpc call filesystem read_text_file '{"path":"/etc/hostname"}'
# Access denied - path outside allowed directories: /etc/hostname not in /tmp
```

Prefer these tools over shell file commands when the task should stay inside the sandbox; use the
shell when you need a tool this server does not expose (pipes, `grep -r`, arbitrary commands).
