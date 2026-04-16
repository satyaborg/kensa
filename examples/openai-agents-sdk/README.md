# OpenAI Agents SDK — Customer Service Eval

End-to-end evaluation of the [customer_service](https://github.com/openai/openai-agents-python/tree/main/examples/customer_service) multi-agent example from OpenAI's Agents SDK using kensa.

Three agents, two tools, one triage router. Kensa evaluates routing accuracy, tool selection, hallucination resistance, and multi-turn context threading — without modifying a single line of the agent code.

## The agent

| Agent | Role | Tools |
|-------|------|-------|
| `triage_agent` | Routes incoming requests | Handoffs only |
| `faq_agent` | Answers common questions | `faq_lookup_tool` (keyword match) |
| `seat_booking_agent` | Changes seat assignments | `update_seat` (context mutation) |

## Setup

```bash
./setup.sh
```

This clones the openai-agents-python repo, installs `openai-agents` + `kensa[openai]`, and verifies imports.

Requires `OPENAI_API_KEY` (export it or add to `.env`).

## Run the eval

```bash
kensa eval
```

Or step by step:

```bash
kensa run                 # execute scenarios
kensa judge               # run deterministic checks + LLM judge
kensa report              # terminal report
kensa report --format html  # standalone HTML
```

## Scenarios

| ID | Input | Tests |
|----|-------|-------|
| `faq-routing` | "What's the baggage policy?" | Correct tool (`faq_lookup_tool`), factual output |
| `seat-routing` | "Change my seat to 14A" | Correct tool (`update_seat`), confirmation |
| `ambiguous-request` | "I have a question about my flight" | Graceful handling, no premature tool calls |
| `faq-no-hallucinate` | "Policy on emotional support animals?" | LLM judge: no fabrication when FAQ has no answer |
| `seat-missing-info` | "Change my seat" (no details) | Must NOT call `update_seat` without info |
| `wrong-routing-trap` | "How much to change seats?" | FAQ question, not a booking — routing judgment |
| `multi-turn-context` | Two turns: seat request + seat number | Context threading across conversation turns |

## How tracing works

Kensa uses OTel auto-instrumentation (Path A from the spec). The wrapper script calls `instrument()` before any OpenAI imports, which hooks into the `openai` Python SDK via `openinference-instrumentation-openai`. Every `chat.completions.create()` call becomes an OTel span with tool calls, token counts, and timing.

The agent code is untouched. No adapter, no shim, no SDK-specific integration.

What you get: tool call names/args, token usage, latency, cost.
What you don't get: agent-level handoff names (those are SDK-internal events above the API layer).

## Wrapper

`run_agent.py` accepts a single string or a JSON list of strings for multi-turn:

```bash
python run_agent.py "What's the baggage policy?"
python run_agent.py '["I need to change my seat", "14A please"]'
```
