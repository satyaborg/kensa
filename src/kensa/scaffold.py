"""Scaffolding logic for ``.kensa/`` — shared between the CLI and MCP server.

The public surface is :func:`init_kensa`, which performs idempotent filesystem
work and returns a structured :class:`InitResult` describing what happened.
Callers wrap it with their own UI (rich output for the CLI, JSON for MCP).
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from kensa.paths import AGENT_DIR, JUDGE_DIR, SCENARIO_DIR, TRACE_DIR


class InitResult(BaseModel):
    """What :func:`init_kensa` did on this invocation."""

    directories_created: list[str] = Field(default_factory=list)
    files_written: list[str] = Field(default_factory=list)
    provider: str | None = None
    example_already_existed: bool = False


def pick_templates() -> tuple[str, str, str, str]:
    """Return ``(agent, scenario, dataset, provider)`` based on available API keys.

    ``provider`` is ``"anthropic"`` / ``"openai"`` for live templates, or ``""``
    for the stub fallback.
    """
    from kensa.runner import ensure_dotenv_loaded

    ensure_dotenv_loaded()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _ANTHROPIC_AGENT, _LIVE_SCENARIO, _LIVE_DATASET, "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return _OPENAI_AGENT, _LIVE_SCENARIO, _LIVE_DATASET, "openai"
    return _STUB_AGENT, _STUB_SCENARIO, "", ""


def init_kensa(blank: bool = False, force: bool = False) -> InitResult:
    """Create the ``.kensa/`` scaffold. Idempotent.

    ``blank=True`` skips the example agent and scenario.
    ``force=True`` overwrites an existing ``example.yaml``.
    """
    dirs = [SCENARIO_DIR, TRACE_DIR, JUDGE_DIR, AGENT_DIR]
    directories_created: list[str] = []
    for d in dirs:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            directories_created.append(str(d))

    files_written: list[str] = []
    provider: str | None = None
    example_already_existed = False

    if not blank:
        agent_file = AGENT_DIR / "example.py"
        dataset_file = SCENARIO_DIR / "example.jsonl"
        example = SCENARIO_DIR / "example.yaml"

        if example.exists() and not force:
            example_already_existed = True
        else:
            agent_tpl, scenario_tpl, dataset_tpl, picked = pick_templates()
            provider = picked or None
            agent_file.write_text(agent_tpl)
            files_written.append(str(agent_file))
            if dataset_tpl:
                dataset_file.write_text(dataset_tpl)
                files_written.append(str(dataset_file))
            example.write_text(scenario_tpl)
            files_written.append(str(example))

    return InitResult(
        directories_created=directories_created,
        files_written=files_written,
        provider=provider,
        example_already_existed=example_already_existed,
    )


_ANTHROPIC_AGENT = """\
from kensa import instrument

instrument()

import sys

import anthropic

SYSTEM = (
    "You are a support ticket triage agent. Given a customer message, classify "
    "its priority as exactly one of: P1, P2, or P3.\\n\\n"
    "P1 = service outage or data loss affecting multiple users\\n"
    "P2 = degraded functionality or bug blocking a single user's workflow\\n"
    "P3 = cosmetic issue, feature request, or general question\\n\\n"
    "Classify based on actual business impact, not the customer's tone or "
    "self-declared urgency. Output only the label (P1, P2, or P3), nothing else."
)

client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=16,
    system=SYSTEM,
    messages=[{"role": "user", "content": sys.argv[1]}],
)
print(response.content[0].text)
"""

_OPENAI_AGENT = """\
from kensa import instrument

instrument()

import sys

import openai

SYSTEM = (
    "You are a support ticket triage agent. Given a customer message, classify "
    "its priority as exactly one of: P1, P2, or P3.\\n\\n"
    "P1 = service outage or data loss affecting multiple users\\n"
    "P2 = degraded functionality or bug blocking a single user's workflow\\n"
    "P3 = cosmetic issue, feature request, or general question\\n\\n"
    "Classify based on actual business impact, not the customer's tone or "
    "self-declared urgency. Output only the label (P1, P2, or P3), nothing else."
)

client = openai.OpenAI()
response = client.chat.completions.create(
    model="gpt-5.4-mini",
    max_tokens=16,
    messages=[
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": sys.argv[1]},
    ],
)
print(response.choices[0].message.content)
"""

_STUB_AGENT = """\
# Add these two lines to your real agent (before SDK imports):
# from kensa import instrument
# instrument()

import sys

message = sys.argv[1] if len(sys.argv) > 1 else ""
# Stub: always outputs P2. Replace with your real agent logic.
print("P2")
"""

_LIVE_DATASET = (
    '{"ticket": "Our entire team can\'t log in — SSO returns'
    " 502. We're completely blocked since 7am.\""
    ', "expected": "P1"}\n'
    '{"ticket": "Would be great if the dashboard had dark'
    ' mode. Not urgent, just a nice-to-have."'
    ', "expected": "P3"}\n'
    '{"ticket": "PDF exports render charts without axis'
    " labels since last Tuesday's update.\""
    ', "expected": "P2"}\n'
    '{"ticket": "URGENT!!! CRITICAL!!! Change my invoice'
    ' font to Arial. BLOCKING my entire business!!!"'
    ', "expected": "P3"}\n'
    '{"ticket": "A few users are seeing stale numbers on'
    " the dashboard — totals don't match the API."
    " Started after this morning's deploy.\""
    ', "expected": "P1"}\n'
    '{"ticket": "The export button is broken (just spins),'
    " the logo on reports looks pixelated, and we're"
    ' being billed for 50 seats but only have 30."'
    ', "expected": "P2"}\n'
)

_LIVE_SCENARIO = """\
# Example scenario — edit this for your agent.
# Full reference: https://github.com/satyaborg/kensa/blob/main/README.md

id: example
name: Support ticket triage
description: Classify support tickets by priority based on business impact.
source: user

dataset: example.jsonl
input_field: ticket

run_command: [python, .kensa/agents/example.py]

expected_outcome: Agent assigns the correct priority label for each ticket.

checks:
  - type: output_matches
    params: { pattern: "^P[123]$" }
    description: Output must be exactly P1, P2, or P3.
  - type: max_cost
    params: { max_usd: 0.05 }
    description: Each classification should cost less than $0.05.

criteria: |
  The agent must assign priority based on actual business impact:
  - P1 for outages or data loss affecting multiple users
  - P2 for bugs blocking a single user's workflow
  - P3 for cosmetic issues, feature requests, or general questions
  Ignore the customer's tone or self-declared urgency.
"""

_STUB_SCENARIO = """\
# Example scenario — edit this for your agent.
# Full reference: https://github.com/satyaborg/kensa/blob/main/README.md
#
# NOTE: No API key detected. This is a stub that won't produce traces.
# Set ANTHROPIC_API_KEY or OPENAI_API_KEY, then re-run: kensa init --force

id: example
name: Support ticket triage
description: Classify a support ticket by priority.
source: user

input: "When I export a report to PDF the charts render without axis labels."

run_command: [python, .kensa/agents/example.py]

expected_outcome: Agent outputs the correct priority label (P2).

checks:
  - type: output_matches
    params: { pattern: "^P[123]$" }
    description: Output must be exactly P1, P2, or P3.
"""
