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

Windows users: see [docs/windows-setup.md](docs/windows-setup.md) for a step-by-step guide including uv and Claude Code installation.

- CLI coding agents (Claude Code, Copilot CLI, opencode): see [docs/cli-clients.md](docs/cli-clients.md)
- Any other MCP client (Claude Desktop, other GUI apps, or anything else): see [docs/gui-clients.md](docs/gui-clients.md)

## Tools

Every tool is defined in [`src/prosuite_mcp/tools.py`](src/prosuite_mcp/tools.py): its docstring is the authoritative description, the same one your MCP client shows the LLM.

### Example

Once connected, you talk to Claude in plain language:

> Check road connectivity in `C:/data/tlm.sde`.

With a spec loaded, Claude calls `search_spec` to find the relevant pre-configured conditions from the `.qa.xml` file, then calls `run_verification` with the pre-filled parameters and returns a summary of errors per condition.

Without a spec, Claude uses `list_conditions` and `describe_condition` to find and configure conditions from scratch.

## Development

See [docs/development.md](docs/development.md).

