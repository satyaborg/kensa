---
name: validate-judge
description: >
  Validate a judge prompt against human-labeled examples. Measures TPR and TNR,
  identifies miscalibration, and iterates until both metrics meet threshold.
  Use after writing a structured judge with generate-judges, when verdicts seem
  wrong, or when you want confidence that the judge works before running evals
  at scale. Also triggered by "validate judge", "calibrate judge", "test the
  judge", "is my judge accurate", "judge quality", "judge metrics", "TPR",
  "TNR", "false positives", "false negatives", "the judge is wrong",
  "my judge sucks", "how good is my judge".
compatibility: Requires Python 3.10+, an LLM API key (Anthropic or OpenAI).
---

# Validate Judge

Measure whether a judge prompt agrees with human labels.

## Lifecycle

```
Setup → Design → Calibrate → ► Validate → Execute → Diagnose → Iterate
```

This skill follows `generate-judges`. It produces confidence that the judge works before you run `kensa eval` at scale. Skip this if your judge uses only deterministic checks.

## Why TPR and TNR, not accuracy

Accuracy is misleading when classes are imbalanced. If 90% of outputs pass, a judge that always says "pass" gets 90% accuracy but catches zero failures.

- **TPR** (true positive rate): of outputs humans labeled pass, what fraction does the judge pass?
- **TNR** (true negative rate): of outputs humans labeled fail, what fraction does the judge fail?

Both must be high. Low TPR means the judge is too strict (false negatives). Low TNR means the judge is too lenient (false positives).

Target: TPR ≥ 90% and TNR ≥ 90%. Adjust threshold if the domain demands it.

## Step 1: Create labeled data

You need 8-20 labeled examples. More is better but diminishing returns after ~20.

**Source 1: Extract from traces.** If you have prior eval runs:

```bash
kensa report --format json
```

Read the agent outputs from results. Present each output to the user and ask: "pass or fail?" Record their verdict.

**Source 2: Manually authored.** Write realistic outputs that cover:
- Clear passes (2-3)
- Clear fails (2-3)
- Borderline cases (2-4): these are the most valuable

**Source 3: From judge prompt examples.** The few-shot examples in the judge YAML are training data. Do NOT reuse them as validation data, that measures memorization, not generalization. Write fresh examples.

Save to `.kensa/labels/{judge-name}.yaml`:

```yaml
examples:
  - output: "Agent output text here..."
    label: pass
  - output: "Another output..."
    label: fail
```

Balance the dataset: roughly equal pass and fail examples. Skewed datasets make TPR or TNR unreliable.

## Step 2: Run validation

```bash
uv run python ${CLAUDE_SKILL_DIR}/scripts/validate_judge.py <judge-name> .kensa/labels/<judge-name>.yaml
```

Options:
- `--threshold 0.9`: minimum TPR and TNR (default: 0.9)
- `--json`: machine-readable output

The script loads the judge prompt, runs it against each labeled example, and reports the confusion matrix.

## Step 3: Interpret results

Read the output. Focus on disagreements:

**Low TPR (too strict):**
The judge rejects outputs that humans accept. Read the false negatives, what did the judge cite as the failure reason?
- Pass definition too narrow → broaden it
- Missing edge case in examples → add a borderline pass example
- Criterion demands perfection → relax to "sufficient"

**Low TNR (too lenient):**
The judge accepts outputs that humans reject. Read the false positives, what did the judge miss?
- Fail definition too vague → add concrete failure indicators
- Missing failure mode in examples → add a fail example for that mode
- Criterion too broad → narrow the scope

**Both low:**
The criterion is ambiguous. The judge is guessing. Rewrite the criterion to be more specific, or split into multiple judges.

## Step 4: Iterate

After adjusting the judge prompt (pass/fail definitions, examples):

1. Re-run validation: `uv run python ${CLAUDE_SKILL_DIR}/scripts/validate_judge.py <judge-name> .kensa/labels/<judge-name>.yaml`
2. Check if TPR and TNR improved
3. Repeat until both meet threshold

Do NOT add the validation examples to the judge prompt's few-shot examples. That contaminates the validation set. If you want to add examples to the prompt, write new ones.

Typical iteration count: 2-4 rounds. If you're past 5 rounds, the criterion may be too subjective for automated judging, consider splitting it or using deterministic checks instead.

## Step 5: Estimate true pass rate (optional)

Once the judge is validated, use bootstrap resampling to estimate the true pass rate on unlabeled data with confidence intervals:

```bash
uv run python ${CLAUDE_SKILL_DIR}/scripts/validate_judge.py <judge-name> .kensa/labels/<judge-name>.yaml --bootstrap
```

This resamples the validation set 1000 times, computes the bias-corrected pass rate each time (using Rogan-Gladen: `(apparent + TNR - 1) / (TPR + TNR - 1)`), and reports the median with a 95% CI. The CI tells you how much to trust the number given your validation set size.

If the CI is wide (e.g., 40%–80%), you need more labeled examples. If it's tight (e.g., 62%–68%), you have enough.

## Anti-patterns

- Validating with the same examples used in the judge prompt. That's testing memorization.
- Using accuracy instead of TPR/TNR. Misleading with imbalanced data.
- Skipping borderline cases. Easy examples don't reveal calibration issues.
- Iterating forever. If 5 rounds don't get you there, the problem is the criterion, not the examples.
- Validating judges for objective criteria. If it can be a deterministic check, make it one.

## Gotchas

- Each validation run costs LLM API calls (one per labeled example). Budget accordingly.
- Judge behavior may vary across models. Validate against the model you'll use in production.
- The validation script uses the same judge resolution as `kensa judge` (env var → API key → error).
- Labels are YAML, not JSON. One file per judge.
