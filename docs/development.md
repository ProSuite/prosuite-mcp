# Development

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
