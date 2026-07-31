# Any other MCP client

`prosuite-mcp` is a standard stdio MCP server with no dependency on any particular client: it doesn't know or care which model or coding agent is driving it.

The clients in [docs/cli-clients.md](cli-clients.md) all invoke `uv run prosuite-mcp` from inside a project directory (`mytest` there), which is how `uv run` finds the right `.venv`. Many other clients (Claude Desktop and other GUI apps in particular) launch the server's command from their own working directory instead, where that would fail to find the project at all. For those, install `prosuite-mcp` as a standalone executable and register its absolute path, not just the bare command: GUI apps started from a dock or desktop launcher often run with a narrower environment than a terminal and may not have it on `PATH` even after installing it.

| Method | Install                           | Find the absolute path                                                                   |
| ------ | --------------------------------- | ---------------------------------------------------------------------------------------- |
| `uv`   | `uv tool install prosuite-mcp`    | `uv tool dir --bin`                                                                      |
| `pip`  | `pip install --user prosuite-mcp` | `pip show -f prosuite-mcp` (path is `Location` + the listed `../../../bin/prosuite-mcp`) |

Register the resulting full path (e.g. `/home/you/.local/bin/prosuite-mcp`) as the command in your client's configuration, with `PROSUITE_HOST`/`PROSUITE_PORT`/`PROSUITE_SSL_CERT_PATH` as environment variables.

## Finding the config file

Use whatever "Edit Config" button the client offers rather than typing a documented path from memory. Claude Desktop on Windows is the cautionary case: installed from the Microsoft Store it reads

```text
C:\Users\<you>\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json
```

while the path most guides name,

```text
C:\Users\<you>\AppData\Roaming\Claude\claude_desktop_config.json
```

also exists and is already populated, so editing it looks right and changes nothing. **Settings > Developer > Edit Config** opens the file the app actually reads.

## Windows paths in JSON

Both forms work, so pick either -- just don't use single backslashes, which JSON reads as escape sequences:

```json
{
  "mcpServers": {
    "prosuite": {
      "command": "C:\\Users\\<you>\\.local\\bin\\prosuite-mcp.exe",
      "env": {
        "PROSUITE_HOST": "localhost",
        "PROSUITE_PORT": "5151"
      }
    }
  }
}
```

Saving a valid entry is enough: the server shows up in the client without a reinstall, though some clients need a restart to re-read the file.
