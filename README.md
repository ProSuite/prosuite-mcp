# prosuite-mcp

MCP server that exposes [Dira ProSuite](https://www.dirageosystems.ch/prosuite?lang=en) quality verification to AI assistants (Claude, etc.).

## Prerequisites

A running ProSuite Quality Verification Server reachable from the host where this server runs.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `PROSUITE_HOST` | `localhost` | ProSuite service host |
| `PROSUITE_PORT` | `5151` | ProSuite service port |
| `PROSUITE_SSL_CERT_PATH` | — | Path to PEM certificate for TLS |
| `PROSUITE_SPEC_PATH` | — | Path to a `.qa.xml` spec file for domain-aware condition search |

## Usage

Both options below assume you create a project directory first:

```bash
mkdir mytest
cd mytest
uv init
uv add prosuite-mcp
```

### Claude Code CLI

Register the server from inside `mytest`, then start Claude:

```bash
claude mcp add prosuite \
  -e PROSUITE_HOST=localhost \
  -e PROSUITE_PORT=5151 \
  -e PROSUITE_SPEC_PATH="C:/path/to/spec.qa.xml" \
  -- uv run prosuite-mcp

claude
```

The `-- uv run prosuite-mcp` tells Claude Code to start the MCP server via `uv run` in the current project, so prosuite-mcp is resolved from the local `.venv`. Run `claude` from the same `mytest` directory each time.

### Claude Desktop

Add to `claude_desktop_config.json` (find it via **Settings → Developer**):

```json
{
  "mcpServers": {
    "prosuite": {
      "command": "uv",
      "args": ["run", "prosuite-mcp"],
      "cwd": "C:\\mytest",
      "env": {
        "PROSUITE_HOST": "localhost",
        "PROSUITE_PORT": "5151",
        "PROSUITE_SPEC_PATH": "C:\\path\\to\\spec.qa.xml"
      }
    }
  }
}
```

`cwd` points to the `mytest` directory so `uv run` can find the local install. Restart Claude Desktop after editing the file.

### VS Code (GitHub Copilot Chat)

Requires the [GitHub Copilot Chat](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot-chat) extension and agent mode enabled in VS Code settings:

```json
"github.copilot.chat.agentMode.enabled": true
```

Open `mytest` as a workspace in VS Code, then create `.vscode/mcp.json` inside it:

```json
{
  "servers": {
    "prosuite": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "${workspaceFolder}", "prosuite-mcp"],
      "env": {
        "PROSUITE_HOST": "localhost",
        "PROSUITE_PORT": "5151",
        "PROSUITE_SPEC_PATH": "C:\\path\\to\\spec.qa.xml"
      }
    }
  }
}
```

VS Code will prompt you to start the server when it detects `.vscode/mcp.json`. Once running, open Copilot Chat, switch to **Agent mode**, and talk to it the same way as with Claude.

## Tools

**`search_spec <query> [max_results]`** — Searches the loaded `.qa.xml` spec for conditions matching a natural-language query (English, German, French, Italian). Returns up to `max_results` (default 20) matching conditions with pre-filled `condition_request` blocks ready to pass directly to `run_verification`, plus the `required_datasets` list. Requires `PROSUITE_SPEC_PATH`.

**`list_conditions [search]`** — Lists available quality conditions. Pass a keyword to filter by name or description.

**`describe_condition <name>`** — Shows the full docstring and parameter list for a condition, including which parameters expect dataset names vs. primitive values.

**`run_verification`** — Runs an ad-hoc quality verification against a workspace. Key parameters:

| Parameter | Type | Description |
|---|---|---|
| `model_catalog_path` | string | Workspace path on the server (`C:/data/my.gdb`, `.sde` file, …) |
| `model_name` | string | Logical name for the data model |
| `datasets` | list | Feature classes/tables: `{name, filter_expression?}` |
| `conditions` | list | Conditions to run: `{condition, params}` |
| `output_dir` | string? | Server-side directory for Issues.gdb and HTML report |
| `envelope` | object? | Spatial filter `{x_min, y_min, x_max, y_max}` |

Returns a summary with `status`, `total_errors`, and a per-condition breakdown.

### Example

Once connected, you talk to Claude in plain language:

> Check road connectivity in `C:/data/tlm.sde`.

With a spec loaded, Claude calls `search_spec` to find the relevant pre-configured conditions from the `.qa.xml` file, then calls `run_verification` with the pre-filled parameters and returns a summary of errors per condition.

Without a spec, Claude uses `list_conditions` and `describe_condition` to find and configure conditions from scratch.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check src
uv run pyright src
```

## License

MIT — see [LICENSE](LICENSE).
