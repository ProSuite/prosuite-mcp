"""
Local LLM chat client for prosuite-mcp.

Starts prosuite-mcp as a stdio subprocess and drives it with any
OpenAI-compatible endpoint: f.e. llama.cpp on local machine, or a hosted one.

Usage:
    python examples/local_llm_chat.py              # interactive REPL
    python examples/local_llm_chat.py "question"   # single shot

Environment variables:
    LLAMA_SERVER_URL  Base URL of llama-server  (default: http://localhost:8080/v1)
    LLAMA_MODEL       Model name in requests     (default: local, llama-server ignores it)
    MAX_TOOL_CALLS    Tool calls per question    (default: 12)
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
# Gemma 4 E4B used 6 calls to author a condition, so this is headroom over a
# real task rather than a guess.
MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", "12"))

_SYSTEM_PROMPT = """\
You are a ProSuite quality specification assistant with access to tools.

Rules, follow these exactly:
- Call tools immediately and without asking for clarification.
- Never say "I can use X to..." or "you can provide a keyword", just call the tool.
- If you already have the answer from context below, answer directly without a tool call.

Which tool to call:
- What the spec contains (specification names, workspace ids, datasets): describe_spec.
- Finding conditions: search_spec with a keyword, or query="" for all of them.
- Parameters of a single test: describe_condition.
- Running checks: run_xml_verification, with the specification name and a workspace
  path for every workspace id describe_spec reported. It runs the spec as written,
  so prefer it whenever a spec is loaded.
- run_verification builds conditions from scratch and cannot express per-condition
  dataset filters. Use it only when no spec is loaded.
- A search_spec result marked "unsupported" has no condition_request: run_xml_verification
  runs it, run_verification cannot.\
"""


def _to_openai_tool(tool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    }


async def _build_spec_context(session: ClientSession) -> str:
    """Call search_spec("") once at startup and return a compact summary.

    Counts and categories only. Listing every condition reached ~9,000 tokens of
    system prompt on a real spec, and the old max_results=500 silently dropped
    the rest while still reporting the full total.
    """
    result = await session.call_tool(
        "search_spec", {"query": "", "max_results": 10_000}
    )
    raw = result.content[0].text if result.content else "{}"
    data = json.loads(raw)
    if "error" in data:
        return ""

    conditions = data.get("results", [])
    total = data.get("total_matches", len(conditions))
    hard = sum(1 for c in conditions if not c["allow_errors"])
    cats = Counter(c["category"] for c in conditions)

    lines = [
        (
            f"Loaded spec: {total} conditions "
            f"({hard} hard failures, {len(conditions) - hard} warnings)."
        ),
        "",
        "Categories:",
    ]
    for cat, count in sorted(cats.items()):
        lines.append(f"  {cat} ({count} conditions)")
    lines += ["", "Call search_spec with a keyword to see the conditions themselves."]

    return "\n".join(lines)


async def _turn(
    llm: OpenAI,
    session: ClientSession,
    tools: list[dict],
    messages: list[dict],
    question: str,
) -> None:
    """Append user question to shared history, run tool loop, append final reply.

    Budgeted in calls, not rounds: one response may carry any number of them,
    and each run_xml_verification is a real ProSuite verification. Refused calls
    still get a tool result, or the next turn sends a history the API rejects.
    """
    messages.append({"role": "user", "content": question})
    budget = MAX_TOOL_CALLS

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
            if budget:
                budget -= 1
                args = json.loads(tc.function.arguments)
                print(f"  [tool] {tc.function.name}({json.dumps(args)})", flush=True)
                result = await session.call_tool(tc.function.name, args)
                content = result.content[0].text if result.content else ""
            else:
                content = "Not executed: this question's tool budget is used up."
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})

        if not budget:
            print(
                f"\nStopped at the {MAX_TOOL_CALLS}-call tool budget without a "
                f"final answer. Raise MAX_TOOL_CALLS if the task needs more."
            )
            return


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

    async with (
        stdio_client(server) as (read, write),
        ClientSession(read, write) as session,
    ):
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
