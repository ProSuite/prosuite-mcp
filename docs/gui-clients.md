# Any other MCP client

`prosuite-mcp` is a standard stdio MCP server with no dependency on any particular client: it doesn't know or care which model or coding agent is driving it.

The clients in [docs/cli-clients.md](cli-clients.md) all invoke `uv run prosuite-mcp` from inside a project directory (`mytest` there), which is how `uv run` finds the right `.venv`. Many other clients (Claude Desktop and other GUI apps in particular) launch the server's command from their own working directory instead, where that would fail to find the project at all. For those, install `prosuite-mcp` as a standalone executable and register its absolute path, not just the bare command: GUI apps started from a dock or desktop launcher often run with a narrower environment than a terminal and may not have it on `PATH` even after installing it.

| Method | Install | Find the absolute path |
|---|---|---|
| `uv` | `uv tool install prosuite-mcp` | `uv tool dir --bin` |
| `pip` | `pip install --user prosuite-mcp` | `pip show -f prosuite-mcp` (path is `Location` + the listed `../../../bin/prosuite-mcp`) |

Register the resulting full path (e.g. `/home/you/.local/bin/prosuite-mcp`) as the command in your client's configuration, with `PROSUITE_HOST`/`PROSUITE_PORT`/`PROSUITE_SSL_CERT_PATH` as environment variables.
