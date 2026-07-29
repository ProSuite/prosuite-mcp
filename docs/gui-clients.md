# Any other MCP client

`prosuite-mcp` is a standard stdio MCP server with no dependency on any particular client: it doesn't know or care which model or coding agent is driving it.

`uv run prosuite-mcp`, as used in the examples above, only works when launched from inside the `mytest` project directory: that's how `uv run` finds the right `.venv`. Claude Code, Copilot CLI, and opencode all launch the server from a project directory you invoke them from, so this works there. Many other clients (Claude Desktop and other GUI apps in particular) launch the server's command from their own working directory instead, where `uv run prosuite-mcp` would fail to find the project at all.

For those, install `prosuite-mcp` as a standalone tool so it works from any directory:

```bash
uv tool install prosuite-mcp
```

This puts a `prosuite-mcp` executable in `uv`'s tool directory, independent of any project directory. That directory is usually on `PATH` for terminal-launched clients, but GUI apps started from a dock or desktop launcher often run with a narrower environment that doesn't include it, so registering the bare command `prosuite-mcp` may not resolve there. To avoid relying on PATH at all, find the absolute path with:

```bash
uv tool dir --bin
```

and register the full path (e.g. `/home/you/.local/bin/prosuite-mcp`) as the command in your client's configuration, with `PROSUITE_HOST`/`PROSUITE_PORT`/`PROSUITE_SSL_CERT_PATH` as environment variables.

`prosuite-mcp` is a normal PyPI package, so plain `pip` works too:

```bash
pip install --user prosuite-mcp
```

Find the resulting script's absolute path with:

```bash
pip show -f prosuite-mcp
```

which lists it relative to the printed `Location` (e.g. `Location: .../lib/python3.12/site-packages` plus a listed file `../../../bin/prosuite-mcp` resolves to `.../bin/prosuite-mcp`). Register that full path the same way as above.
