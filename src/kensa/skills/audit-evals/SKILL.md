---
name: audit-evals
description: >
  Set up evals for an agent codebase or check eval status after changes. Determines
  readiness, identifies what can be tested, and prepares the environment. Use when
  starting evals for the first time, returning after a code change, or figuring out
  what to do next. Also triggered by "set up evals", "is my agent ready?",
  "eval status", "what should I do next?", "init eval", "evaluate my agent",
  "test my agent", "help me eval this", "get started with evals", "where do I
  start", "how do I test this agent", "check my setup". This is the default entry
  point, use it whenever a user wants to evaluate an agent and you're unsure which
  skill to start with.
compatibility: Requires Python 3.10+, uv.
---

# Audit Eval

Set up and assess an agent codebase for kensa.

## Lifecycle

```
► Setup → Design → Execute → Diagnose → Iterate
```

This is the entry point, both for first-time setup and for returning users who need to figure out where they are. After this skill, the next step is `generate-scenarios` (if no scenarios exist) or `kensa eval` (if scenarios are ready).

## Check kensa is installed

```bash
python ${CLAUDE_SKILL_DIR}/scripts/check_library.py
```

If missing, ask the user before installing:
> "kensa is not installed. Add it as a project dependency?"

If approved:

```bash
python ${CLAUDE_SKILL_DIR}/scripts/check_library.py --install
```

## Determine current state

Use Glob to check what exists:
1. `Glob(".kensa/")`: does the directory exist at all?
2. `Glob(".kensa/scenarios/*.yaml")`: scenario files?
3. `Glob(".kensa/agents/*")`: agent entry points?
4. `Glob(".kensa/traces/*.jsonl")`: trace files?
5. `Glob(".kensa/results/*.json")`: result files from previous runs?
6. `Glob(".kensa/judges/*.yaml")`: judge prompt files?

Recommend a single next action based on findings:

- **No `.kensa/` directory**: Fresh start. Run `kensa init --blank` to scaffold the directory structure without example files. Then scan the codebase (below) and hand off to `generate-scenarios` to create real scenarios directly.
- **`.kensa/` exists but empty** (no scenarios, no traces): Blank scaffold from `kensa init --blank` or manual setup. Scan the codebase (below) and hand off to `generate-scenarios`.
- **Has `.kensa/` with only example scenario**: Init was run without `--blank`. Scan the codebase (below) to understand the real agent, then hand off to `generate-scenarios` to replace the example.
- **No scenarios, has traces**: Traces exist from manual runs. Run `kensa analyze` to surface patterns, then hand off to `generate-scenarios` with the analysis as context.
- **Has scenarios, no traces**: Scenarios are ready but never run. Recommend: `kensa eval`.
- **Has scenarios, has traces and results**: Full loop is active. Check both data stores:
  1. Run `kensa report --format json` and count scenarios with `status: "fail"`, `"error"`, or `"uncertain"`. This catches check failures and judge rejections that leave no trace anomaly.
  2. Run `kensa analyze` to check for trace-level anomalies (cost/latency outliers, errors, looping).
  If either surface has failures, recommend `diagnose-errors`. If both are clean, recommend `kensa eval`.
- **Has scenarios, has traces but no results**: Traces exist but judge hasn't run. Recommend: `kensa judge`.

State the recommendation clearly. Mention alternatives only if genuinely relevant.

## Scan codebase

Identify these five things. Use `Agent` with `subagent_type: "Explore"` for thorough scanning on larger codebases, or `Read` and `Grep` directly on small ones.

1. **Entry point**: how to run the agent
2. **LLM SDK**: anthropic, openai, langchain, etc.
3. **Tools**: functions the agent can call
4. **Behaviors**: from system prompts, docstrings, README
5. **Env vars**: required configuration

Record as working memory:
```
Entry point: src/agent.py
Run command: [uv, run, python, src/agent.py]
SDK: anthropic
Tools: [get_weather, search_web, calculate]
Behaviors: Answers user questions using web search and weather data
Env vars: ANTHROPIC_API_KEY
```

## Assess readiness

| Signal | Readiness | What you can deliver |
|--------|-----------|---------------------|
| Clear entry point, typed inputs, tool defs, docstrings | High | Strong baseline scenarios with targeted checks |
| Ambiguous entry point, no docstrings, implicit config | Medium | Generic smoke tests + recommendations |
| No clear agent boundary, hardcoded keys | Low | Minimal value from code. Push toward traces. |
| Custom HTTP clients, vendored SDKs, non-Python | Instrumentation risk | Flag early. May need manual instrumentation. |

Tell the user what you found honestly:
> "I scanned your codebase. Here's what I can infer and what I can't. Scenario quality will be limited by [X]. To improve: [concrete action]."

## Verify instrumentation

Instrumentation is automatic. The runner injects `sitecustomize.py` via `PYTHONPATH` before the agent runs. No code changes needed in the agent.

Ensure the matching SDK extra is installed:

| SDK | Install |
|-----|---------|
| `anthropic` | `uv add kensa[anthropic]` |
| `openai` | `uv add kensa[openai]` |
| `langchain` | `uv add kensa[langchain]` |

Verify with:

```bash
kensa doctor
```

If `kensa doctor` passes, instrumentation is ready. If it flags issues, fix them before proceeding.

## Next step

Hand off to `generate-scenarios` with the codebase scan context (entry point, tools, behaviors, env vars). The user's next action is to design test scenarios.

## Gotchas

- `instrument()` is idempotent. Agents that still have `from kensa import instrument; instrument()` work fine (no duplicate spans).
- `instrument()` warns when no SDK instrumentors are installed. Install the matching extra.
- `.env` is auto-loaded by the runner (walks up from cwd). Never read or print `.env` yourself: it holds secrets. Use `kensa doctor` to verify which env vars are set.
- Judge model resolution: `KENSA_JUDGE_MODEL` env var > `ANTHROPIC_API_KEY` (claude-sonnet-4-6) > `OPENAI_API_KEY` (gpt-5.4-mini) > error.
- For non-Python commands or `python -S`/`-I`, the escape hatch is `from kensa import instrument; instrument()` in the agent code.
