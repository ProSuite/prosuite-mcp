# CLI Coding Agents

Both options below assume you create a project directory first:

```bash
mkdir mytest
cd mytest
uv init --python 3.12
uv add prosuite-mcp
```

### Claude Code CLI

Register the server from inside `mytest`, then start Claude:

```bash
claude mcp add prosuite \
  -e PROSUITE_HOST=localhost \
  -e PROSUITE_PORT=5151 \
  -- uv run prosuite-mcp

claude
```

The `-- uv run prosuite-mcp` tells Claude Code to start the MCP server via `uv run` in the current project, so prosuite-mcp is resolved from the local `.venv`. Run `claude` from the same `mytest` directory each time.

Long verifications should use `start_verification` or `start_xml_verification`.
They return a `run_id` immediately, avoiding client tool-call timeouts. Ask
Claude to poll `get_verification_status(run_id)` and retrieve the finished run
with `get_verification_result(run_id)`. The background worker persists status
and results under `PROSUITE_MCP_STATE_DIR`; it processes one run at a time by
default.

### Copilot CLI

Register the server from inside `mytest`, then start Copilot:

```bash
copilot mcp add prosuite \
  -e PROSUITE_HOST=localhost \
  -e PROSUITE_PORT=5151 \
  -- uv run prosuite-mcp
```

### opencode

Add an `opencode.jsonc` inside `mytest`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "prosuite": {
      "type": "local",
      "command": ["uv", "run", "prosuite-mcp"],
      "enabled": true,
      "environment": {
        "PROSUITE_HOST": "localhost",
        "PROSUITE_PORT": "5151"
      }
    }
  }
}
```

Then run `opencode` from inside `mytest`.
