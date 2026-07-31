# Windows Setup

Verified on Windows 11 with uv 0.9.7 and Claude Desktop.

Install uv first, then follow the route for the kind of client you use.

## 1. Install uv

Follow the Windows instructions at <https://docs.astral.sh/uv/getting-started/installation> -- use the standalone installer.

Open a new PowerShell window after installing and confirm:

```powershell
uv --version
```

## 2a. CLI coding agents

Install Claude Code from <https://code.claude.com/docs/en/setup> -- use the **Native Install > Windows PowerShell** tab.

Confirm it works and log in before continuing:

```powershell
claude --version
```

Then follow [docs/cli-clients.md](cli-clients.md), applying the PowerShell note below.

## 2b. GUI clients

GUI clients need the server as a standalone executable, since they don't run from a project directory. Install it and ask uv where it landed:

```powershell
uv tool install prosuite-mcp
uv tool dir --bin
```

The second command prints the directory holding `prosuite-mcp.exe`, typically `C:\Users\<you>\.local\bin`. That full path is what you register in the client -- see [docs/gui-clients.md](gui-clients.md).

Smoke-test the executable by running it directly. It prints nothing and waits, which is what a stdio server is supposed to do; Ctrl+C to stop.

```powershell
& "C:\Users\<you>\.local\bin\prosuite-mcp.exe"
```

Two things to expect here:

- uv does not add its bin directory to `PATH` for you. GUI clients don't care, because they get the absolute path. If you also want the bare `prosuite-mcp` command in a shell and a fresh PowerShell window says "not recognized", run `uv tool update-shell` and open another window.
- On startup the upstream `prosuite` package prints several `SyntaxWarning: invalid escape sequence '\/'` lines from its own docstrings. They go to stderr, not to the protocol channel, and nothing is wrong.

## PowerShell note

[docs/cli-clients.md](cli-clients.md) uses bash syntax. In PowerShell, replace the backslash line-continuation character (`\`) with a backtick (`` ` ``). For example:

```powershell
claude mcp add prosuite `
  -e PROSUITE_HOST=localhost `
  -e PROSUITE_PORT=5151 `
  -- uv run prosuite-mcp
```
