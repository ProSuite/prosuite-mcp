# Windows Setup

## 1. Install uv

Follow the Windows instructions at <https://docs.astral.sh/uv/getting-started/installation> -- use the standalone installer.

Open a new PowerShell window and confirm:

```powershell
uv --version
```

## 2a. CLI coding agents

Install Claude Code from <https://code.claude.com/docs/en/setup> -- use the **Native Install > Windows PowerShell** tab. Log in, then confirm:

```powershell
claude --version
```

Continue with [docs/cli-clients.md](cli-clients.md) and the PowerShell note below.

## 2b. GUI clients

GUI clients don't run from a project directory, so they need the executable's absolute path:

```powershell
uv tool install prosuite-mcp
uv tool dir --bin
```

The second command prints where `prosuite-mcp.exe` landed. Register that path as described in [docs/gui-clients.md](gui-clients.md).

Running the executable directly checks the install on its own, before any client is involved. It waits for input instead of exiting, which is correct for a stdio server; warnings from dependencies on startup are harmless. Ctrl+C to stop.

```powershell
& "C:\Users\<you>\.local\bin\prosuite-mcp.exe"
```

If a new PowerShell window doesn't find the bare `prosuite-mcp` command, run `uv tool update-shell` and open another. GUI clients are unaffected, since they use the absolute path.

## PowerShell note

[docs/cli-clients.md](cli-clients.md) uses bash syntax. In PowerShell, replace the backslash line-continuation character (`\`) with a backtick (`` ` ``):

```powershell
claude mcp add prosuite `
  -e PROSUITE_HOST=localhost `
  -e PROSUITE_PORT=5151 `
  -- uv run prosuite-mcp
```
