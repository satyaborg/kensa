"""Run OpenAI customer_service agent with kensa OTel instrumentation.

Wraps the multi-agent airline customer service example from
https://github.com/openai/openai-agents-python so kensa can capture
tool calls and evaluate agent behavior.

Usage:
    python run_agent.py "What's the baggage policy?"
    python run_agent.py '["I need to change my seat", "14A please"]'

Single string → one-turn conversation.
JSON list of strings → multi-turn conversation (turns executed sequentially).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Instrument BEFORE any OpenAI imports so OTel hooks into the SDK.
# ---------------------------------------------------------------------------
from kensa import instrument

instrument()

# Suppress the agents SDK's own tracing (posts to api.openai.com/v1/traces/ingest).
os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "true")

# ---------------------------------------------------------------------------
# 2. Put the cloned openai-agents-python repo on sys.path so we can import
#    the examples package.  Default: ./openai-agents-python relative to this
#    script.  Override with AGENTS_REPO env var.
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("AGENTS_REPO", SCRIPT_DIR / "openai-agents-python"))

if not (REPO_DIR / "examples" / "customer_service").is_dir():
    print(
        f"Error: openai-agents-python repo not found at {REPO_DIR}\n"
        "Run: ./setup.sh   (or set AGENTS_REPO to the repo root)",
        file=sys.stderr,
    )
    sys.exit(1)

sys.path.insert(0, str(REPO_DIR))

# ---------------------------------------------------------------------------
# 3. Import agent definitions from the example.
# ---------------------------------------------------------------------------
from agents import (  # noqa: E402
    HandoffOutputItem,
    ItemHelpers,
    MessageOutputItem,
    Runner,
    TResponseInputItem,
)
from examples.customer_service.main import (  # noqa: E402
    AirlineAgentContext,
    triage_agent,
)


async def run_conversation(turns: list[str]) -> None:
    """Execute one or more conversation turns against the triage agent."""
    context = AirlineAgentContext()
    current_agent = triage_agent
    input_items: list[TResponseInputItem] = []

    for turn in turns:
        input_items.append({"content": turn, "role": "user"})
        result = await Runner.run(current_agent, input_items, context=context)

        for item in result.new_items:
            if isinstance(item, MessageOutputItem):
                text = ItemHelpers.text_message_output(item)
                print(text)
            elif isinstance(item, HandoffOutputItem):
                print(f"[handoff] {item.source_agent.name} -> {item.target_agent.name}")

        input_items = result.to_input_list()
        current_agent = result.last_agent


def parse_input(raw: str) -> list[str]:
    """Parse CLI arg as either a plain string or a JSON list of strings."""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and all(isinstance(t, str) for t in parsed):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return [raw]


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python run_agent.py <input>", file=sys.stderr)
        sys.exit(1)

    turns = parse_input(sys.argv[1])
    asyncio.run(run_conversation(turns))


if __name__ == "__main__":
    main()
