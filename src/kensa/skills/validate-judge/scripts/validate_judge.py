"""Validate an kensa judge against human-labeled examples.

Runs the judge on each labeled example, compares verdicts to human labels,
and reports TPR (true positive rate) and TNR (true negative rate).

Usage:
    uv run python validate_judge.py <judge_name> <labels_path> \
        [--threshold 0.9] [--json] [--bootstrap]

Labels format (.yaml):
    examples:
      - output: "agent output text"
        label: pass
      - output: "another output"
        label: fail

Exit codes:
    0 — both TPR and TNR meet threshold
    1 — one or both metrics below threshold
    2 — usage error
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import TypedDict

import yaml

from kensa.judge import get_judge, load_judge_prompt_spec
from kensa.models import JudgePromptSpec, ResultStatus


class ValidationRow(TypedDict):
    output: str
    human: str
    judge: str
    correct: bool
    reasoning: str


def build_validation_prompt(spec: JudgePromptSpec, output: str) -> str:
    """Build a judge prompt for a single labeled output.

    Mirrors the structure of kensa' production judge prompt but without
    trace/scenario metadata — we're testing criterion evaluation, not
    trace parsing.
    """
    parts = [
        f"Criterion: {spec.criterion}",
        f"\nPASS: {spec.pass_definition}",
        f"\nFAIL: {spec.fail_definition}",
    ]
    if spec.examples:
        parts.append("\nExamples:")
        for i, ex in enumerate(spec.examples, 1):
            parts.append(f"\n  Example {i} [{ex.label.upper()}]:")
            parts.append(f"  Output: {ex.output}")
            parts.append(f"  Critique: {ex.critique}")
    criteria_text = "\n".join(parts)

    return f"""You are evaluating an AI agent's output against a specific criterion.

## Evaluation Criteria
{criteria_text}

## Agent Output
{output}

## Task
Evaluate whether this output meets the criterion above.

Respond with ONLY a JSON object (no markdown, no backticks):
{{"verdict": "pass" or "fail", "reasoning": "your reasoning"}}"""


def load_labels(path: Path) -> list[dict[str, str]]:
    """Load labeled examples from YAML."""
    with open(path) as f:
        data = yaml.safe_load(f)
    examples = data.get("examples", [])
    for i, ex in enumerate(examples):
        if "output" not in ex or "label" not in ex:
            raise ValueError(f"Label example {i} missing 'output' or 'label' field")
        if ex["label"] not in ("pass", "fail"):
            raise ValueError(
                f"Label example {i} has invalid label: {ex['label']!r} (must be 'pass' or 'fail')"
            )
    return examples


def bootstrap_corrected_pass_rate(
    results: list[ValidationRow],
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> dict[str, float | None]:
    """Bootstrap the bias-corrected true pass rate with 95% CI.

    Resamples the validation results, computes TPR/TNR each time, applies
    Rogan-Gladen correction, and returns the median + 95% CI.
    """
    rng = random.Random(seed)
    corrected_rates: list[float] = []

    for _ in range(n_bootstrap):
        sample = rng.choices(results, k=len(results))

        tp = sum(1 for r in sample if r["human"] == "pass" and r["judge"] == "pass")
        fn = sum(1 for r in sample if r["human"] == "pass" and r["judge"] == "fail")
        fp = sum(1 for r in sample if r["human"] == "fail" and r["judge"] == "pass")
        tn = sum(1 for r in sample if r["human"] == "fail" and r["judge"] == "fail")

        tpr = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        tnr = tn / (tn + fp) if (tn + fp) > 0 else 0.0

        # Rogan-Gladen correction
        denominator = tpr + tnr - 1
        if abs(denominator) < 1e-9:
            continue  # judge no better than random, skip this sample

        apparent = (tp + fp) / len(sample) if sample else 0.0
        corrected = (apparent + tnr - 1) / denominator
        corrected = max(0.0, min(1.0, corrected))  # clamp to [0, 1]
        corrected_rates.append(corrected)

    if not corrected_rates:
        return {"median": None, "ci_lower": None, "ci_upper": None}

    corrected_rates.sort()
    n = len(corrected_rates)
    return {
        "median": round(corrected_rates[n // 2], 3),
        "ci_lower": round(corrected_rates[int(n * 0.025)], 3),
        "ci_upper": round(corrected_rates[int(n * 0.975)], 3),
    }


def main() -> None:
    args = sys.argv[1:]

    if len(args) < 2 or "--help" in args or "-h" in args:
        print("Usage: validate_judge.py <judge_name> <labels_path>")
        print("       [--threshold 0.9] [--json] [--bootstrap]")
        print("\nValidates a judge prompt against human-labeled examples.")
        print("Reports TPR (true positive rate) and TNR (true negative rate).")
        sys.exit(2)

    judge_name = args[0]
    labels_path = Path(args[1])
    threshold = 0.9
    json_output = "--json" in args
    do_bootstrap = "--bootstrap" in args

    if "--threshold" in args:
        idx = args.index("--threshold")
        if idx + 1 >= len(args):
            print("Error: --threshold requires a value", file=sys.stderr)
            sys.exit(2)
        threshold = float(args[idx + 1])

    # Load judge spec
    try:
        spec = load_judge_prompt_spec(judge_name)
    except FileNotFoundError:
        print(f"Error: judge prompt not found at .kensa/judges/{judge_name}.yaml", file=sys.stderr)
        sys.exit(2)

    # Load labels
    try:
        labels = load_labels(labels_path)
    except FileNotFoundError:
        print(f"Error: labels file not found: {labels_path}", file=sys.stderr)
        sys.exit(2)

    if not labels:
        print("Error: no labeled examples found", file=sys.stderr)
        sys.exit(2)

    # Resolve judge provider
    try:
        judge = get_judge()
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    # Run validation
    tp = fp = tn = fn = 0
    results: list[ValidationRow] = []

    for i, example in enumerate(labels):
        output = example["output"]
        human_label = example["label"]

        if not json_output:
            print(f"  [{i + 1}/{len(labels)}] Judging ({human_label})...", end=" ", flush=True)

        prompt = build_validation_prompt(spec, output)
        result = judge.judge(prompt)

        judge_label = "pass" if result.verdict == ResultStatus.PASS else "fail"
        correct = human_label == judge_label

        if human_label == "pass" and judge_label == "pass":
            tp += 1
        elif human_label == "pass" and judge_label == "fail":
            fn += 1
        elif human_label == "fail" and judge_label == "pass":
            fp += 1
        else:
            tn += 1

        if not json_output:
            marker = "✓" if correct else "✗"
            print(f"{marker} judge={judge_label}")

        truncated = output[:100] + "..." if len(output) > 100 else output
        results.append(
            {
                "output": truncated,
                "human": human_label,
                "judge": judge_label,
                "correct": correct,
                "reasoning": result.reasoning,
            }
        )

    # Compute metrics
    tpr = tp / (tp + fn) if (tp + fn) > 0 else float("nan")
    tnr = tn / (tn + fp) if (tn + fp) > 0 else float("nan")
    total = len(labels)
    accuracy = (tp + tn) / total if total > 0 else 0.0

    # Bootstrap corrected pass rate if requested
    bootstrap_result = None
    if do_bootstrap:
        bootstrap_result = bootstrap_corrected_pass_rate(results)

    report: dict[str, object] = {
        "judge": judge_name,
        "total_examples": total,
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "tpr": round(tpr, 3) if tpr == tpr else None,  # NaN check
        "tnr": round(tnr, 3) if tnr == tnr else None,
        "accuracy": round(accuracy, 3),
        "threshold": threshold,
        "tpr_pass": tpr >= threshold if tpr == tpr else False,
        "tnr_pass": tnr >= threshold if tnr == tnr else False,
        "results": results,
    }
    if bootstrap_result:
        report["corrected_pass_rate"] = bootstrap_result

    if json_output:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n{'=' * 50}")
        print(f"Judge: {judge_name}")
        print(f"Examples: {total} ({tp + fn} pass, {tn + fp} fail)")
        print("\nConfusion matrix:")
        print("               Predicted")
        print("            pass    fail")
        print(f"  Actual pass  {tp:<7} {fn}")
        print(f"        fail   {fp:<7} {tn}")
        tpr_str = f"{tpr:.1%}" if tpr == tpr else "N/A (no pass labels)"
        tnr_str = f"{tnr:.1%}" if tnr == tnr else "N/A (no fail labels)"
        print(f"\nTPR: {tpr_str}")
        print(f"TNR: {tnr_str}")
        print(f"Accuracy: {accuracy:.1%}")
        print(f"Threshold: {threshold:.0%}")

        if report["tpr_pass"] and report["tnr_pass"]:
            print("\n✓ PASS — judge meets threshold on both metrics")
        else:
            issues = []
            if not report["tpr_pass"]:
                issues.append(f"TPR {tpr_str} < {threshold:.0%} (too strict — false negatives)")
            if not report["tnr_pass"]:
                issues.append(f"TNR {tnr_str} < {threshold:.0%} (too lenient — false positives)")
            print("\n✗ FAIL")
            for issue in issues:
                print(f"  - {issue}")

        # Bootstrap results
        if bootstrap_result and bootstrap_result["median"] is not None:
            median = bootstrap_result["median"]
            ci_lo = bootstrap_result["ci_lower"]
            ci_hi = bootstrap_result["ci_upper"]
            print(f"\nCorrected pass rate: {median:.1%} (95% CI: {ci_lo:.1%}-{ci_hi:.1%})")

        # Show disagreements
        disagreements = [r for r in results if not r["correct"]]
        if disagreements:
            print(f"\nDisagreements ({len(disagreements)}):")
            for d in disagreements:
                print(f"\n  Human={d['human']}, Judge={d['judge']}")
                print(f"  Output: {d['output']}")
                print(f"  Reasoning: {d['reasoning'][:200]}")

    sys.exit(0 if (report["tpr_pass"] and report["tnr_pass"]) else 1)


if __name__ == "__main__":
    main()
