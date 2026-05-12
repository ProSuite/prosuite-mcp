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

## Usage

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

### Copilot CLI

Register the server from inside `mytest`, then start Copilot:

```bash
copilot mcp add prosuite \
  -e PROSUITE_HOST=localhost \
  -e PROSUITE_PORT=5151 \
  -- uv run prosuite-mcp
```

## Tools

**`load_spec <path>`** — Loads a `.qa.xml` spec file. Call this at the start of a session with the path to your spec (local drive, OneDrive, network share). Replaces any previously loaded spec.

**`search_spec <query> [max_results]`** — Searches the loaded `.qa.xml` spec for conditions matching a natural-language query (English, German, French, Italian). Returns up to `max_results` (default 20) matching conditions with pre-filled `condition_request` blocks ready to pass directly to `run_verification`, plus the `required_datasets` list. Requires a spec to be loaded first via `load_spec`.

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
