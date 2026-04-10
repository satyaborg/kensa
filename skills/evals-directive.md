# Evals Directive

How to write kensa skills. Read this before creating or modifying a skill.

## What a skill is

A skill is a set of directives that tells a coding agent how to perform a specific eval task. It's not a tutorial, not documentation, not a blog post. It's an instruction manual for an agent that is smart but lacks domain-specific knowledge.

## File structure

```
skills/{skill-name}/
  SKILL.md              # The skill itself
  scripts/              # Helper scripts (optional)
  references/           # Schema docs, examples (optional)
  evals/
    evals.json          # 2-3 test cases for the skill
    files/              # Fixture files referenced by test cases
```

## SKILL.md format

### Frontmatter

```yaml
---
name: skill-name
description: >
  One paragraph. What it does, when to use it. Include trigger phrases
  the user might say. Be generous, include synonyms and indirect
  phrasings. This is what the agent uses to decide whether to invoke
  the skill.
compatibility: Runtime requirements (Python version, API keys, etc.)
---
```

The description is the most important field. If it doesn't match the user's intent, the skill never fires.

### Body structure

1. **Title**: imperative verb phrase matching the skill name
2. **Lifecycle**: where this skill sits in the eval flow, using arrow notation:
   ```
   Setup → Design → Calibrate → Validate → Execute → Diagnose → Iterate
   ```
   Mark the current position with `►`. Not every skill touches every step.
3. **When to use**: preconditions and the decision logic for choosing this skill vs another
4. **Steps**: numbered or headed sections, each a concrete action
5. **Anti-patterns**: what NOT to do (more valuable than what to do)
6. **Gotchas**: non-obvious failure modes specific to this domain

### What to include

- Directives the agent wouldn't know from general training
- Exact CLI commands with flags
- File paths and YAML schemas
- Decision trees: "if X, do Y; if Z, do W"
- Concrete examples of good and bad output

### What to cut

- General programming knowledge (the agent knows Python)
- Motivational text ("it's important to...", "best practice is...")
- Background theory (why evals matter, what LLMs are)
- Citations and references to papers
- Hedging ("you might want to consider...")
- Redundant explanations of the same concept

The agent is smart. It doesn't need to be convinced or motivated. It needs domain-specific directives it wouldn't otherwise know.

## Skill design principles

**One clear recommendation.** Each skill ends with ONE next action. Not a numbered menu, not "you could do A or B or C." Pick the best action and state it. Mention alternatives only if the diagnosis is genuinely ambiguous.

**Hand-off context.** Each skill produces a summary that the next skill consumes. Include: what was done, what was found, what to do next. The next skill shouldn't start from scratch.

**Deterministic before LLM.** Exhaust code-based checks (regex, schema, tools_called) before reaching for an LLM judge. Many "subjective" criteria reduce to keyword checks when you understand the domain.

**Binary pass/fail.** No Likert scales, no letter grades, no partial credit. Binary forces clear decision boundaries and makes calibration measurable.

**Fix before evaluate.** If the agent prompt never asked for a behavior, add the instruction. Don't build an evaluator for something the agent was never told to do.

**One criterion per judge.** If you're evaluating tone AND accuracy, that's two judges. Multi-criterion judges produce ambiguous verdicts.

**Read before generating.** Always read the codebase, traces, or prior results before generating scenarios, judges, or diagnoses. Grounding matters more than coverage.

**Never read `.env`.** It contains secrets. The runner auto-loads it; skills must not open, print, or grep it. To check whether an env var is set, run `kensa doctor` or inspect the running shell, never the file.

## Prerequisite: audit-evals is always first

Before invoking any other skill, verify kensa is installed:

```bash
python skills/audit-evals/scripts/check_library.py
```

If exit code is 1, invoke `audit-evals`, it handles installation and environment setup. Never skip this. The other skills assume the CLI exists and will fail with "command not found" if it doesn't.

## CLI commands available

```bash
kensa eval                    # run + judge + report
kensa eval -s <id>            # single scenario
kensa judge                   # judge latest run
kensa report --format json    # machine-readable results
kensa analyze --format json   # cost/latency stats + anomalies
kensa doctor                  # pre-flight checks
kensa init                    # scaffold .kensa/ with example scenario
```

## Labels and validation data

Labels live at `.kensa/labels/{judge-name}.yaml`. They are NOT the same as judge prompt examples. Labels are held-out validation data, never reuse them as few-shot examples in the judge prompt.

## Line budget

Keep SKILL.md under 200 lines. If you need more, extract schemas into `references/` and scripts into `scripts/`. The agent's context window is finite, every line competes with the user's codebase for attention.

## The lifecycle

```
audit-evals → generate-scenarios → generate-judges → validate-judge → kensa eval → diagnose-errors → (iterate)
```

Each skill owns one step. Skills don't overlap. If you're writing a skill that partially duplicates another, you're drawing the boundary wrong.
