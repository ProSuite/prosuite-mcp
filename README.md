# prosuite-mcp

MCP server that exposes [Dira ProSuite](https://www.dirageosystems.ch/prosuite?lang=en) quality verification to AI assistants (Claude, etc.).

## Prerequisites

A running ProSuite Quality Verification Server reachable from the host where this server runs.

## Configuration

| Environment variable     | Default     | Description                     |
| ------------------------ | ----------- | ------------------------------- |
| `PROSUITE_HOST`          | `localhost` | ProSuite service host           |
| `PROSUITE_PORT`          | `5151`      | ProSuite service port           |
| `PROSUITE_SSL_CERT_PATH` | (none)      | Path to PEM certificate for TLS |

## Usage

Windows users: start at [docs/windows-setup.md](docs/windows-setup.md).

- CLI coding agents (Claude Code, Copilot CLI, opencode): see [docs/cli-clients.md](docs/cli-clients.md)
- Any other MCP client (Claude Desktop, other GUI apps, or anything else): see [docs/gui-clients.md](docs/gui-clients.md)

## Tools

Every tool is defined in [`src/prosuite_mcp/tools.py`](src/prosuite_mcp/tools.py): its docstring is the authoritative description, the same one your MCP client shows the LLM.

### Example

Once connected, you talk to the assistant in plain language:

> Check road connectivity in `C:/data/tlm.sde`.

With a spec loaded, the assistant calls `describe_spec` to see which specifications and workspaces it defines, then `run_xml_verification` to run one against your data, and returns a summary of errors per condition. The spec goes to the ProSuite service as-is, so per-condition filters and defaults are applied exactly as your domain experts configured them. `search_spec` browses the conditions in a spec by keyword.

Without a spec, the assistant uses `list_conditions` and `describe_condition` to find and configure conditions from scratch, then `run_verification` to run them ad-hoc. Datasets take a `filter_expression` here too, but only one per dataset per run, so a spec that filters the same feature class differently in two conditions cannot be reproduced this way.

## Development

See [docs/development.md](docs/development.md).
