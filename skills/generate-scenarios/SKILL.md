---
name: generate-scenarios
description: >
  Design test scenarios for an agent, covering happy paths, edge cases, tool usage,
  error handling, and cost bounds. Use when writing new scenarios, editing existing
  ones, expanding coverage after a failed run, or when the user says "add a scenario",
  "improve my evals", "write scenarios", "I need test cases", "what should I test",
  "write test cases", "expand coverage", "add edge cases", "more scenarios". Also use
  when the user has a diagnosis from a failed run and needs targeted scenarios for
  specific failure patterns.
compatibility: Requires Python 3.10+.
---

# Generate Scenarios

Design and write `.kensa/scenarios/*.yaml` files for agent evaluation.

## Lifecycle

```
Setup → ► Design → Execute → Diagnose → Iterate
```

This skill produces scenario files. It follows `audit-evals` (first time) or `diagnose-errors` (iteration). After writing scenarios, the next step is `kensa eval`.

## Gather context

Before writing scenarios, understand the agent. If coming from `audit-evals`, the codebase scan is already done. Otherwise, identify:

1. Entry point and run command
2. Tools the agent can call
3. Expected behaviors from system prompts or docs
4. Known failure modes (ask the user)

**If coming from `kensa init`**, an example scenario exists at `.kensa/scenarios/example.yaml` and an example agent at `.kensa/agents/example.py`. Read both to understand the scaffolded structure, then replace them with real scenarios targeting the user's agent. Don't build on the example, it's a template, not a starting point.

If traces exist from a previous run, read them first:

```bash
kensa analyze --format json
```

Use flagged traces to inform scenario design. Traces with errors, cost outliers, or repeated tool calls reveal real failure modes worth testing.

**If coming from `diagnose-errors`**, the diagnosis context (which scenarios failed, root causes, fix category) should guide what you write. Target the specific failure patterns identified, don't regenerate from scratch.

## Scenario categories

Generate 3-7 scenarios covering these categories:

1. **Happy path**: basic functionality works as expected
2. **Tool usage**: correct tools called for the task
3. **Edge case**: unusual, empty, or long input
4. **Error handling**: what happens when things go wrong
5. **Cost/latency bounds**: stays within reasonable limits

Not every category needs a scenario. Match coverage to the agent's complexity.

## Write scenarios

When writing the YAML, load `${CLAUDE_SKILL_DIR}/references/scenario-schema.md` for the full schema, available check types, and examples.

Save each scenario to `.kensa/scenarios/{id}.yaml`.

**Inline criteria** work for simple, objective evaluations:
```yaml
criteria: |
  The agent should provide a clear weather report for the requested city.
  It must use the weather tool, not hallucinate data.
```

**Structured judge prompts** work for subjective criteria that need explicit pass/fail boundaries. If your criteria are longer than 3 lines or require nuance, use the `generate-judges` skill to create `.kensa/judges/*.yaml` files, then reference them:
```yaml
judge: tone-match   # loads .kensa/judges/tone-match.yaml
```

`criteria` and `judge` are mutually exclusive.

## Summarize

After writing, present a summary table:

| # | Scenario ID | Tests | Checks | Priority |
|---|-------------|-------|--------|----------|
| 1 | weather_basic | Happy path weather query | tools_called, output_contains, max_turns | Correctness |
| 2 | weather_edge | Empty city input | output_contains | Edge case |

One line per scenario. Ask if the user wants adjustments before running.

## Validate

After writing scenarios, verify they execute correctly:

```bash
kensa eval -s <scenario_id>
```

If a scenario errors (exit code -1), fix the `run_command` or `env_overrides` before proceeding. Common issues: wrong entry point path, missing env var, `run_command` written as a string instead of a list.

Once scenarios run cleanly, the user is ready for the full eval loop: `kensa eval`.

## Modifying existing scenarios

When editing scenarios (not creating from scratch):

1. Read the existing scenario file first
2. Understand the current checks and criteria
3. Make targeted changes: don't rewrite what works
4. If changing criteria to a structured judge, switch `criteria:` to `judge:` and use `generate-judges`

## Anti-patterns

- Writing `run_command` as a string (e.g. `python agent.py {{input}}`). It must be a list of argv elements (e.g. `[python, agent.py]`); the scenario `input` is appended automatically.
- Vague criteria like "the agent should work correctly." Be specific about what to check.
- Exact output matching with `output_matches`. LLM output is non-deterministic. Use `output_contains` with key phrases.
- Missing cost/latency bounds. Every scenario should have at least `max_cost` or `max_turns`.
- Generating scenarios without reading the codebase or traces first. Grounding matters.
