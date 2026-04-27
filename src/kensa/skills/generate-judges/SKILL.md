---
name: generate-judges
description: >
  Create structured judge prompt files for subjective evaluation criteria that
  inline strings cannot handle. Produces .kensa/judges/*.yaml with binary pass/fail
  definitions and few-shot examples. Use when inline criteria produce inconsistent
  verdicts, when pass/fail boundaries need explicit definitions, or when multiple
  scenarios share an evaluation standard. Also triggered by "improve the judge",
  "write judge prompt", "judge is wrong", "calibrate judge", "generate judges",
  "judge keeps failing", "false positive", "false negative", "judge is too strict",
  "judge is too lenient", "inconsistent verdicts". Use this even when the user
  doesn't mention "judge" explicitly but describes evaluation inconsistency.
compatibility: Requires Python 3.10+.
---

# Write Judge Prompt

Create `.kensa/judges/*.yaml` files with structured evaluation criteria.

## Lifecycle

```
Setup → Design → ► Calibrate → Execute → Diagnose → Iterate
```

This skill is conditional, only needed when inline `criteria:` in scenario YAML produce inconsistent or wrong judge verdicts. You should already have scenarios in `.kensa/scenarios/`. After writing judge prompts, wire them into scenario files and run `kensa eval` to test.

## When to use this

Use inline `criteria:` in scenario YAML for objective, simple evaluations:
- "The response mentions Tokyo"
- "The agent called the correct tool"

Use structured judge prompts (this skill) when:
- The pass/fail boundary is subjective and needs explicit definitions
- The judge is producing inconsistent or wrong verdicts
- You need few-shot examples to calibrate the judge
- Multiple scenarios share the same evaluation standard

If you don't have scenarios yet, use `generate-scenarios` first.

## The four components

Every structured judge prompt has four parts. When writing the YAML, load `${CLAUDE_SKILL_DIR}/references/judge-prompt-schema.md` for the full schema.

### 1. Criterion

One thing being evaluated. Not two, not three. One.

```yaml
criterion: Email tone matches the client persona
```

Not: "Email is good and well-formatted and uses correct tone"

### 2. Pass definition

Explicitly define what constitutes a pass. Be concrete, no wiggle room.

```yaml
pass: |
  Language appropriate for the client type:
  - Luxury: formal, exclusive features, premium positioning
  - First-time: warm, educational, avoids jargon
  - Investor: data-driven, ROI-focused, concise
```

### 3. Fail definition

Explicitly define what constitutes a fail. Mirror the pass definition.

```yaml
fail: |
  Tone mismatched to persona:
  - Casual slang for luxury clients
  - Heavy financial jargon for first-time buyers
  - Overly emotional language for investors
```

### 4. Few-shot examples

2-4 examples with output, label, and critique. Include at least one clear pass, one clear fail, and one borderline case. Borderline examples teach the judge nuance.

```yaml
examples:
  - output: "Dear Mr. Harrington, I am pleased to present an exclusive listing..."
    label: pass
    critique: "Formal salutation, luxury positioning language throughout."
  - output: "Hey! Check out this awesome place, it's got a pool and stuff!"
    label: fail
    critique: "Casual slang inappropriate for luxury buyer."
  - output: "Hi Sarah, I found a property that might be a great fit..."
    label: pass
    critique: "Warm greeting, relatable terms. Matches first-time buyer tone."
```

Critiques must be detailed, not terse. They set the bar for the judge's own reasoning.

## Write the file

Save to `.kensa/judges/{name}.yaml`:

```bash
mkdir -p .kensa/judges
```

Use a descriptive slug: `tone-match`, `sql-accuracy`, `safety-check`.

## Wire it to the scenario

In the scenario YAML, replace `criteria:` with `judge:`:

```yaml
# Before
criteria: The email should match the client persona tone

# After
judge: tone-match   # loads .kensa/judges/tone-match.yaml
```

`criteria` and `judge` are mutually exclusive. Setting both raises a validation error.

## Test the judge

Run the scenario and inspect the judge's reasoning:

```bash
kensa eval -s <scenario_id>
kensa report --format json
```

Read the `judge_result.reasoning` field. If the judge misses failures or flags passing outputs, adjust:
- Sharpen pass/fail definitions: make boundaries more explicit
- Add or swap examples: especially borderline cases
- Split the criterion: if one judge evaluates multiple things, decompose

Repeat until the judge's verdicts match your expectations. Then run `kensa eval` for the full suite.

## Code-based checks first

Exhaust deterministic checks before reaching for an LLM judge. Many criteria that seem subjective reduce to code:
- Format validation → `output_matches` with regex
- Required content → `output_contains`
- Tool usage → `tools_called`, `tools_not_called`, `tool_order`
- Resource limits → `max_cost`, `max_turns`, `max_duration`

Reserve judge prompts for criteria that genuinely require interpretation: tone, faithfulness, relevance, completeness.

## Anti-patterns

- Multi-criterion judges. One file, one criterion. If you need to check tone AND accuracy, write two judge files.
- Vague definitions like "the response is helpful." Define what helpful means for this specific application.
- No examples. Without examples, the judge doesn't know what counts as failure in your application.
- Likert scales. Binary pass/fail only. Likert scales can't be calibrated and produce unactionable scores.
- Judging without fixing obvious problems first. If the prompt never asked for the behavior, add the instruction before building a judge for it.
