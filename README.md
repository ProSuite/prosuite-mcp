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

Windows users: see [docs/windows-setup.md](docs/windows-setup.md) for a step-by-step guide including uv and Claude Code installation.

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

### Any other MCP client

`prosuite-mcp` is a standard stdio MCP server with no dependency on any particular client: it doesn't know or care which model or coding agent is driving it. Any client that can launch a local process and speak MCP over stdio (Claude Desktop, Cursor, Cline, Continue, Windsurf, etc.) can register it the same way: run `uv run prosuite-mcp` as the command, with `PROSUITE_HOST`/`PROSUITE_PORT`/`PROSUITE_SSL_CERT_PATH` as environment variables, per your client's own MCP configuration format.

## Tools

**`load_spec <path>`** — Loads a `.qa.xml` spec file. Call this at the start of a session with the path to your spec (local drive, OneDrive, network share). Replaces any previously loaded spec.

**`describe_spec`** — Describes the loaded spec: available `specification_name` values, `workspace_id` keys that need path substitutions, and per-specification dataset lists. Call this before `run_xml_verification`.

**`search_spec <query> [max_results]`** — Searches the loaded `.qa.xml` spec for conditions matching a natural-language query (English, German, French, Italian). Returns up to `max_results` (default 20) matching conditions with pre-filled `condition_request` blocks ready to pass directly to `run_verification`, plus the `required_datasets` list. Requires a spec to be loaded first via `load_spec`.

**`run_xml_verification`** — Runs a named `QualitySpecification` directly from the loaded XML spec, with workspace path substitutions. Unlike `run_verification`, the XML is sent to ProSuite as-is, preserving per-condition dataset filters and all other spec details exactly as configured. Preferred whenever a `.qa.xml` spec exists.

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

**`condition_to_xml`** — Previews the `<QualityCondition>` XML for a single condition, without touching any spec. Requires an existing `test_descriptor` alias (e.g. `MinLength(1)`); does not look one up for you. Mainly useful for inspecting parameter serialization in isolation — `add_condition_to_spec` is the tool for actually adding a condition to a spec.

**`add_condition_to_spec`** — Previews adding a new `QualityCondition` to a spec: resolves an existing `<TestDescriptor>` whose test class matches (never synthesizes one), and returns the full updated spec XML with the condition appended and wired into the named `QualitySpecification`. Pure preview — it never writes to a file, so review the result before persisting it. Reads the currently loaded/configured spec unless `spec_xml` is passed explicitly.

**`preview_condition_run`** — Runs a single proposed condition ad-hoc and returns the actual flagged features (same summary shape as `run_verification`), so a condition from `add_condition_to_spec` can be judged by what it flags before being merged into a spec, not just by whether it builds.

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

### Live integration test

`tests/test_live_verification.py` runs a real verification against an actual ProSuite gRPC service instead of a mocked one. It exists to catch drift between the real service's response shape and what `prosuite_mcp.verification` assumes — something a fully mocked suite can't do by construction.

It's skipped by default (`pytest` / CI never runs it) because it depends on network access to a real ProSuite service, which not every contributor or CI environment has, and this repo never hardcodes an address for one. Opt in explicitly, pointing at your own reachable service:

```bash
PROSUITE_LIVE_TESTS=1 PROSUITE_HOST=<host> PROSUITE_PORT=<port> uv run pytest tests/test_live_verification.py -v
```

It expects a plain file geodatabase named `gdb1.gdb` (feature classes `lines`/`points`/`polygons`) to exist on that server; adjust `_GDB1_PATH` in the test if yours keeps it elsewhere.

If you want it to run every time you run the full suite on your own machine, export `PROSUITE_LIVE_TESTS`, `PROSUITE_HOST`, and `PROSUITE_PORT` in your shell profile (or a local, uncommitted `.env`) rather than changing the default — that keeps `main`'s default test run hermetic for everyone else while making it effectively always-on for you.

## License

MIT — see [LICENSE](LICENSE).
