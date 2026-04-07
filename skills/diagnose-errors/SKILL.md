---
name: diagnose-errors
description: >
  Understand what failed and why after an eval run. Reads judge results and traces,
  categorizes failures, identifies patterns, and recommends the next action. Use
  after running kensa eval or kensa judge, when results have failures, or when
  traces show anomalies. Also triggered by "what went wrong", "why did X fail",
  "diagnose my eval", "analyze errors", "my evals are failing", "everything failed",
  "check the results", "look at the failures", "debug my evals", "why is this
  failing", "evals broke". Use this whenever the user has run evals and wants to
  understand the results, even if they don't explicitly say "diagnose".
compatibility: Requires Python 3.10+, a completed kensa run and judge.
---

# Diagnose Errors

Read eval results and traces to understand what failed and why.

## Lifecycle

```
Setup → Design → Execute → ► Diagnose → Iterate
```

This skill follows a completed eval run (`kensa eval` or `kensa judge`). It produces a diagnosis that feeds back into `generate-scenarios` or `generate-judges` for the next iteration.

## Load results

Read the latest results file:

```bash
kensa report --format json
```

Or read directly from `.kensa/results/`. Identify which scenarios failed and how:
- **Check failure**: a deterministic check (tool_called, output_contains, etc.) failed
- **Judge rejection**: all checks passed but the LLM judge said fail
- **Error**: scenario crashed, timed out, or judge errored
- **Uncertain**: judge couldn't determine pass/fail

## Diagnose by failure type

### Check failures

Read the `check_results` array. The failed check tells you exactly what went wrong.

Present as:
> `weather_no_tool`: FAIL, `tool_called` check failed
> The agent answered without calling `get_weather`, likely hallucinating data.
> **Fix:** Check the system prompt; ensure it instructs the agent to use tools.

### Judge rejections

Read `judge_result.reasoning` and `judge_result.evidence`. The judge explains its verdict.

Present as:
> `weather_accuracy`: FAIL, judge rejected
> "The agent returned weather for New York instead of Tokyo."
> **Fix:** Agent may not be passing the city parameter correctly to the tool.

If the judge reasoning seems wrong (false negative), the judge criteria or prompt may need refinement. Suggest using `generate-judges` for structured criteria.

### Errors

Read `error` field and scenario's `stderr`. Common causes:
- Missing API key → run `kensa doctor` to confirm which var is unset. Never open or print `.env`: it holds secrets.
- Timeout → agent may be stuck in a loop
- Import error → missing dependency
- No traces → instrumentation not set up (use `audit-evals`)

## Analyze traces

For deeper diagnosis, read trace files directly from `.kensa/traces/`:

```bash
kensa analyze --format json
```

Focus on `flagged_traces`:
- **error**: spans with error status
- **cost_outlier**: unusually expensive runs
- **latency_outlier**: unusually slow runs
- **repeated_tool_call**: same tool called with same args multiple times
- **high_turn_count**: too many LLM calls

Read the actual trace JSONL files to understand *why* each was flagged. Group related flags to find patterns:
> "3 of 5 error traces failed on the same tool call, the `search_web` tool is returning 404s."

## Recommend next action

Based on the failure distribution, recommend ONE primary action:

- **Mostly check failures** → The agent has a bug. Point to the specific behavior that's wrong and recommend fixing the agent code, then re-running: `kensa eval`.
- **Mostly judge rejections** → Either the agent's output quality needs work, or the judge is miscalibrated. If the judge reasoning looks wrong, recommend `generate-judges` to add structured criteria with examples. If the judge reasoning looks right, recommend fixing the agent.
- **Mostly errors** → Environment or instrumentation issue. Recommend `audit-evals` to re-verify setup.
- **Mixed failures** → Recommend `generate-scenarios` to add targeted scenarios for each distinct failure pattern.

State the recommendation. Mention alternatives briefly only if the diagnosis is ambiguous.

Be honest about signal quality:
> "These are baseline results. The judge is unvalidated against human labels. Traces from this run are saved, iterate to improve."

## Carry forward

Summarize findings as context for the next skill invocation:

```
Failed scenarios: weather_no_tool, weather_accuracy
Failure types: 1 check failure (tool_called), 1 judge rejection
Root cause: agent not using weather tool for direct queries
Fix category: agent bug, system prompt doesn't instruct tool use
Recommended action: fix agent, then re-run kensa eval
```

This summary should be passed to `generate-scenarios` (if adding/editing scenarios) or `generate-judges` (if calibrating the judge) as input context, so the next skill doesn't start from scratch.

## Gotchas

- Trace files are JSONL, not JSON. One span per line. Parse line-by-line.
- Run continues on failure. One crashed scenario doesn't block others. Check `exit_code=-1` entries in the manifest.
- Subprocess isolation means no shared state between scenarios. But filesystem side effects persist.
- Cost checks warn when no cost data exists. A passing `max_cost` check might be vacuous.
