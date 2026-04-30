"""
Local LLM chat client for prosuite-mcp.

Starts prosuite-mcp as a stdio subprocess and connects it to a local
llama.cpp server via its OpenAI-compatible API.

Usage:
    python examples/local_llm_chat.py              # interactive REPL
    python examples/local_llm_chat.py "question"   # single shot

Environment variables:
    LLAMA_SERVER_URL  Base URL of llama-server  (default: http://localhost:8080/v1)
    LLAMA_MODEL       Model name in requests     (default: local — llama-server ignores it)
    PROSUITE_SPEC_PATH  Path to .qa.xml spec file (optional, loaded at startup)
    PROSUITE_HOST     ProSuite service host      (default: localhost)
    PROSUITE_PORT     ProSuite service port      (default: 5151)
"""

import asyncio
import json
import os
import sys
from collections import Counter

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

LLAMA_URL = os.environ.get("LLAMA_SERVER_URL", "http://localhost:8080/v1")
MODEL = os.environ.get("LLAMA_MODEL", "local")

_SYSTEM_PROMPT = """\
You are a ProSuite quality specification assistant with access to tools.

Rules — follow these exactly:
- Call tools immediately and without asking for clarification.
- For broad questions ("what categories are there?", "which conditions fail hard?",
  "give me an overview"), call search_spec with query="" right away.
- For topic-specific questions, call search_spec with a relevant keyword.
- For parameter details on a single condition, call describe_condition.
- Never say "I can use X to…" or "you can provide a keyword" — just call the tool.
- If you already have the answer from context below, answer directly without a tool call.\
"""


def _to_openai_tool(tool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }


async def _build_spec_context(session: ClientSession) -> str:
    """Call search_spec("") once at startup and return a compact summary."""
    result = await session.call_tool("search_spec", {"query": "", "max_results": 500})
    raw = result.content[0].text if result.content else "{}"
    data = json.loads(raw)
    if "error" in data:
        return ""

    conditions = data.get("results", [])
    total = data.get("total_matches", len(conditions))
    hard = [c for c in conditions if not c["allow_errors"]]
    warn = [c for c in conditions if c["allow_errors"]]
    cats = Counter(c["category"] for c in conditions)

    lines = [
        f"Loaded spec: {total} conditions total "
        f"({len(hard)} hard failures, {len(warn)} warnings).",
        "",
        "Categories:",
    ]
    for cat, count in sorted(cats.items()):
        lines.append(f"  {cat} ({count} conditions)")

    lines += ["", "Hard-failure conditions:"]
    for c in hard:
        lines.append(f"  [{c['category']}] {c['name']}")

    lines += ["", "Warning conditions (allow_errors=true):"]
    for c in warn:
        lines.append(f"  [{c['category']}] {c['name']}")

    return "\n".join(lines)


async def _turn(
    llm: OpenAI,
    session: ClientSession,
    tools: list[dict],
    messages: list[dict],
    question: str,
) -> None:
    """Append user question to shared history, run tool loop, append final reply."""
    messages.append({"role": "user", "content": question})

    while True:
        resp = llm.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            print(f"\nAssistant: {msg.content}")
            return

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            print(f"  [tool] {tc.function.name}({json.dumps(args)})", flush=True)
            result = await session.call_tool(tc.function.name, args)
            content = result.content[0].text if result.content else ""
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})


_PROSUITE_ENV_VARS = [
    "PROSUITE_HOST",
    "PROSUITE_PORT",
    "PROSUITE_SSL_CERT_PATH",
    "PROSUITE_SPEC_PATH",
]


async def run(question: str | None = None) -> None:
    llm = OpenAI(base_url=LLAMA_URL, api_key="none")
    prosuite_env = {k: v for k in _PROSUITE_ENV_VARS if (v := os.environ.get(k))}
    server = StdioServerParameters(command="prosuite-mcp", args=[], env=prosuite_env)

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [_to_openai_tool(t) for t in (await session.list_tools()).tools]

            system = _SYSTEM_PROMPT
            spec_ctx = await _build_spec_context(session)
            if spec_ctx:
                system += f"\n\nSpec loaded at startup:\n{spec_ctx}"
            else:
                system += "\n\nNo spec file loaded (set PROSUITE_SPEC_PATH to enable search_spec)."

            messages: list[dict] = [{"role": "system", "content": system}]

            if question:
                await _turn(llm, session, tools, messages, question)
                return

            print("prosuite-mcp local chat  |  Ctrl-C or 'quit' to exit\n")
            while True:
                try:
                    q = input("You: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not q or q.lower() in {"quit", "exit"}:
                    break
                await _turn(llm, session, tools, messages, q)
                print()


if __name__ == "__main__":
    asyncio.run(run(" ".join(sys.argv[1:]) or None))
