# Development

```bash
uv sync --dev
uv run pytest
uv run ruff check src
uv run pyright src
```

### Live integration test

`tests/test_live_verification.py` runs a real verification against an actual ProSuite gRPC service, to catch drift between its response shape and what `prosuite_mcp.verification` assumes, something a mocked suite can't do by construction. Skipped by default (`pytest`, CI) since it needs network access to a real service and this repo doesn't hardcode one. Opt in:

```bash
PROSUITE_LIVE_TESTS=1 PROSUITE_HOST=<host> PROSUITE_PORT=<port> uv run pytest tests/test_live_verification.py -v
```

Expects a file geodatabase named `gdb1.gdb` (feature classes `lines`/`points`/`polygons`); adjust `_GDB1_PATH` in the test if yours differs.

To make it run by default on your own machine without changing the shared default, export the three variables above in your shell profile or a local `.env` instead.
