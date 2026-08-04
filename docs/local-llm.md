# Driving the server without a coding agent

The clients in [cli-clients.md](cli-clients.md) and [gui-clients.md](gui-clients.md) are coding agents, which bring their own harness and prompting. If your organisation does not permit one, [`examples/local_llm_chat.py`](../examples/local_llm_chat.py) drives the same tools from any OpenAI-compatible endpoint instead.

**An example, not a supported entry point.** It is not installed by `prosuite-mcp`, has no tests, and may change or disappear.

## Running it

```sh
uv sync --extra local-llm
uv run python examples/local_llm_chat.py                  # interactive
uv run python examples/local_llm_chat.py "your question"  # single shot
```

It starts `prosuite-mcp` as a stdio child process, so the usual `PROSUITE_*` configuration applies and is passed through.

| Environment variable | Default                    | Description                           |
| -------------------- | -------------------------- | ------------------------------------- |
| `LLAMA_SERVER_URL`   | `http://localhost:8080/v1` | Any OpenAI-compatible base URL        |
| `LLAMA_MODEL`        | `local`                    | Model name; llama-server ignores it   |
| `MAX_TOOL_CALLS`     | `12`                       | Tool calls per question               |
| `PROSUITE_SPEC_PATH` | (none)                     | Spec loaded and summarised at startup |

## What to expect

Quality comes from the model you point it at, not from this example. Gemma 4 E4B (Q8_0) on a workstation GPU routed browsing and running correctly, including preferring `run_xml_verification` over the ad-hoc path, and authored a condition with correct parameters. That is a handful of observations, so test against your own specs.

## Limitations

- No streaming, and history is lost when the process exits.
- Stops after `MAX_TOOL_CALLS` rather than letting the model loop.
- The system prompt is tuned for a small local model. Directive phrasing that pushes a weak model to act makes a strong one overreact, so soften it for a frontier model.
