# prosuite-mcp

MCP server that exposes [Dira ProSuite](https://www.dirageosystems.ch/prosuite?lang=en) quality verification to AI assistants (Claude, etc.).

## Prerequisites

A running ProSuite Quality Verification Server reachable from the host where this server runs.

## Installation

```bash
pip install prosuite-mcp
# or
uv add prosuite-mcp
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `PROSUITE_HOST` | `localhost` | ProSuite service host |
| `PROSUITE_PORT` | `5151` | ProSuite service port |
| `PROSUITE_SSL_CERT_PATH` | — | Path to PEM certificate for TLS |

## Usage

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "prosuite": {
      "command": "prosuite-mcp",
      "env": {
        "PROSUITE_HOST": "localhost",
        "PROSUITE_PORT": "5151"
      }
    }
  }
}
```

### CLI

```bash
prosuite-mcp   # starts the server on stdio
```

## Tools

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

> Check that all features in the Roads layer in `C:/data/my.gdb` are at least 0.5 m long.

Claude uses `list_conditions` and `describe_condition` to find the right condition and its parameters, then calls `run_verification` and returns a summary of errors per condition.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check src
uv run pyright src
```

## License

MIT — see [LICENSE](LICENSE).
