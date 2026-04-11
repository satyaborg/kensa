# Scenario YAML Schema

## Contents
- YAML schema definition
- Available check types
- Good scenario example
- Anti-patterns (too vague, too strict)

## Schema

```yaml
id: string                         # unique, snake_case
name: string                       # human-readable
description: string                # what this tests and why
source: code | traces | user       # how it was generated

input: string | object             # what to send the agent (appended as the final argv element)
run_command: [string, ...]         # argv list passed verbatim to subprocess (no shell, no templating)
env_overrides:                     # optional env var overrides
  KEY: value

dataset: string                    # path to JSONL file for parameterized inputs (optional)
input_field: string                # field name within each JSONL row to use as input (optional)
                                   # dataset and input_field must both be set or both omitted

expected_outcome: string           # plain language expected behavior

checks:                            # deterministic checks (all must pass)
  - type: check_type
    params: { ... }
    description: string

criteria: string                   # natural language for LLM judge (optional)
judge: string                      # reference to .kensa/judges/{name}.yaml (mutually exclusive with criteria)

trace_refs: [string]               # trace IDs that informed this (Mode B)
failure_pattern: string            # failure pattern this targets (Mode B)
```

## Available Check Types

| Check | Params | Tests |
|-------|--------|-------|
| `output_contains` | `value: string`, `case_sensitive: bool` (default: false) | Output includes string (case-insensitive) |
| `output_matches` | `pattern: regex` | Output matches regex |
| `tools_called` | `tools: [string]` | All listed tools were called (set membership, order-free) |
| `tools_not_called` | `tools: [string]` | None of the listed tools were called |
| `tool_order` | `order: [string]` | Tools called in this temporal sequence (opt-in; use only when order is load-bearing, like setup -> migrate -> test. Default to `tools_called` for presence intent; prefer grading outcomes over constraining incidental execution paths.) |
| `trajectory` | `steps: [{tool: string, args?: object}]`, `ordering: exact \| any_order` (default: exact), `args: exact \| ignore` (default: exact), `min_accuracy: float` (default: 1.0), `max_steps: int?`, `max_tokens: int?`, `max_duration_seconds: float?` | Canonical tool-call trajectory match with numeric `trajectory_accuracy` and `step_efficiency` metrics plus machine-readable mismatch diagnostics |
| `max_cost` | `max: float` | Total cost under threshold (USD) |
| `max_turns` | `max: int` | LLM call count under N |
| `max_duration` | `max_seconds: float` | Elapsed time under threshold |
| `no_repeat_calls` | (none) | No duplicate tool calls (same name + args) |

Notes:

- `trajectory` is limited to one check per scenario in V1.
- `trajectory` currently evaluates tool-call paths only.
- `max_tokens` is warning-only when token data is unavailable in the trace.

## Good Scenario Example

```yaml
id: weather_basic
name: Basic weather query
description: Verify agent can answer a simple weather question using the weather tool
source: code
input: "What's the weather in Tokyo?"
run_command: [uv, run, python, agent.py]
expected_outcome: Agent calls get_weather tool and returns temperature for Tokyo
checks:
  - type: trajectory
    params:
      steps:
        - tool: get_weather
          args: { city: Tokyo }
      max_steps: 1
      max_tokens: 2000
    description: Must take the expected tool path efficiently
  - type: tools_called
    params: { tools: [get_weather] }
    description: Must use the weather tool
  - type: output_contains
    params: { value: Tokyo }
    description: Response mentions the queried city
  - type: max_turns
    params: { max: 5 }
    description: Should complete in under 5 LLM calls
  - type: max_cost
    params: { max: 0.10 }
    description: Single query should cost under 10 cents
criteria: |
  The agent should provide a clear, accurate weather report for Tokyo.
  The response should include temperature and conditions.
  The agent should not hallucinate weather data, it must use the tool.
```

## Anti-patterns

**String-form `run_command`:**
```yaml
run_command: uv run python agent.py {{input}}     # WRONG, legacy template form is no longer supported
run_command: [uv, run, python, agent.py]          # CORRECT, list of argv elements
```
Why it's bad: the string form historically required `{{input}}` interpolation and shell-style parsing, which made command injection possible if the template was quoted incorrectly. The list form is passed straight to `subprocess.run` (no shell, no parsing), and the scenario `input` is appended as the final argv element automatically.

**Too vague:**
```yaml
id: test1
name: Test
description: Test the agent
input: "Do something"
expected_outcome: It works
criteria: The agent should work correctly.
```
Why it's bad: generic input, no deterministic checks, untestable outcome, no cost/latency bounds.

**Too strict:**
```yaml
id: exact_output
name: Exact output match
input: "What is 2+2?"
checks:
  - type: output_matches
    params: { pattern: "^The answer is 4\\.$" }
```
Why it's bad: LLM output is non-deterministic. Use `output_contains` with key phrases instead of `output_matches` with full output.
