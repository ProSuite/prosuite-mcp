# Windows Setup

Install the two prerequisites below, then follow [docs/cli-clients.md](cli-clients.md).

## 1. Install uv

Follow the Windows instructions at <https://docs.astral.sh/uv/getting-started/installation> -- use the standalone installer.

Open a new PowerShell window after installing and confirm:

```powershell
uv --version
```

## 2. Install Claude Code

Follow the setup page at <https://code.claude.com/docs/en/setup> -- use the **Native Install > Windows PowerShell** tab.

Confirm it works and log in before continuing:

```powershell
claude --version
```

## PowerShell note

The README uses bash syntax. In PowerShell, replace the backslash line-continuation character (`\`) with a backtick (`` ` ``). For example:

```powershell
claude mcp add prosuite `
  -e PROSUITE_HOST=localhost `
  -e PROSUITE_PORT=5151 `
  -- uv run prosuite-mcp
```
