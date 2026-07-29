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

- CLI coding agents (Claude Code, Copilot CLI, opencode): see [docs/cli-clients.md](docs/cli-clients.md)
- Any other MCP client (Claude Desktop, other GUI apps, or anything else): see [docs/gui-clients.md](docs/gui-clients.md)

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

See [docs/development.md](docs/development.md).

## License

MIT — see [LICENSE](LICENSE).
