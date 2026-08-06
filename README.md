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

Windows users: start at [docs/windows-setup.md](https://github.com/ProSuite/prosuite-mcp/blob/main/docs/windows-setup.md).

- CLI coding agents (Claude Code, Copilot CLI, opencode): see [docs/cli-clients.md](https://github.com/ProSuite/prosuite-mcp/blob/main/docs/cli-clients.md)
- Any other MCP client (Claude Desktop, other GUI apps, or anything else): see [docs/gui-clients.md](https://github.com/ProSuite/prosuite-mcp/blob/main/docs/gui-clients.md)
- No coding agent permitted? An experimental example drives the same tools from any OpenAI-compatible LLM, local or hosted: see [docs/local-llm.md](https://github.com/ProSuite/prosuite-mcp/blob/main/docs/local-llm.md)

## Tools

Every tool is defined in [`src/prosuite_mcp/tools.py`](https://github.com/ProSuite/prosuite-mcp/blob/main/src/prosuite_mcp/tools.py): its docstring is the authoritative description, the same one your MCP client shows the LLM.

### Example

Once connected, you talk to the assistant in plain language:

> Check road connectivity in `C:/data/tlm.sde`.

With a spec loaded, the assistant calls `describe_spec` to see which specifications and workspaces it defines, then `run_xml_verification` to run one against your data, and returns a summary of errors per condition. The spec goes to the ProSuite service as-is, so per-condition filters and defaults are applied exactly as your domain experts configured them. `search_spec` browses the conditions in a spec by keyword.

`list_datasets` and `describe_dataset` read the geodatabase itself, so the assistant can pick conditions from the actual geometry type, feature count and field names rather than from a dataset's name. These read the path on the machine running this server, which need not be the one the ProSuite service resolves paths on.

`list_conditions` and `describe_condition` are enriched with the ProSuite QA Quick Reference: each test gains the readable description and the test family it belongs to, and searches match that wording rather than only the API docstring. The document is downloaded in the background when the server starts, and a lookup never waits for it, so calls made before it lands, or on a host that cannot reach it, return the same output as before without the enrichment.

Without a spec, the assistant uses `list_conditions` and `describe_condition` to find and configure conditions from scratch, then `run_verification` to run them ad-hoc. Datasets take a `filter_expression` here too, but only one per dataset per run, so a spec that filters the same feature class differently in two conditions cannot be reproduced this way.

## Development

See [docs/development.md](https://github.com/ProSuite/prosuite-mcp/blob/main/docs/development.md).
