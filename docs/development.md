# Development

```bash
uv sync --dev
uv run pytest
uv run ruff check src
uv run pyright src
uv run mdformat --check README.md CONTEXT.md docs
```

### Live integration test

`tests/test_live_verification.py` runs a real verification against an actual ProSuite gRPC service, to catch drift between its response shape and what `prosuite_mcp.verification` assumes, something a mocked suite can't do by construction. Skipped by default (`pytest`, CI) since it needs network access to a real service and this repo doesn't hardcode one. Opt in:

```bash
PROSUITE_LIVE_TESTS=1 PROSUITE_HOST=<host> PROSUITE_PORT=<port> uv run pytest tests/test_live_verification.py -v
```

Expects a file geodatabase named `gdb1.gdb` (feature classes `lines`/`points`/`polygons`); adjust `_GDB1_PATH` in the test if yours differs.

To make it run by default on your own machine without changing the shared default, export the three variables above in your shell profile or a local `.env` instead.

### Spec corpus harness

`tests/test_spec_corpus.py` runs a directory of real `.qa.xml` files through `load_spec`, `get_spec_metadata` and the `search_spec` to `run_verification` handoff. No server needed. The other test modules only prove the code does what we assumed, because we wrote every fixture in them; this one measures what real spec files actually contain. Skipped by default, and the files are not committed since they belong to customers and projects. Opt in:

```bash
PROSUITE_SPEC_CORPUS=<dir of .qa.xml files> uv run pytest tests/test_spec_corpus.py -v -s
```

`-s` shows the summary: how many conditions `search_spec` offers and how many of them `run_verification` would actually accept, broken down by failure reason. `MIN_CLEAN_HANDOFF_RATE` in the module is a floor to raise as those failures get fixed, not a target to lower.
