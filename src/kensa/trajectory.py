"""Deterministic trajectory matching over canonical tool-call traces."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from kensa.models import (
    CheckResult,
    Span,
    SpanKind,
    ToolInfo,
    TrajectoryArgsMode,
    TrajectoryOrderingMode,
    TrajectoryParams,
    TrajectoryStep,
)
from kensa.trace_semantics import collect_tool_calls


def _tool_to_step(tool: ToolInfo) -> TrajectoryStep:
    return TrajectoryStep(tool=tool.name, args=tool.args)


def _step_payload(index: int, step: TrajectoryStep) -> dict[str, Any]:
    return {"index": index, "tool": step.tool, "args": step.args}


def _steps_match(
    expected: TrajectoryStep,
    actual: TrajectoryStep,
    args_mode: TrajectoryArgsMode,
) -> bool:
    if expected.tool != actual.tool:
        return False
    if args_mode == TrajectoryArgsMode.IGNORE:
        return True
    return expected.args == actual.args


def _same_tool(expected: TrajectoryStep, actual: TrajectoryStep) -> bool:
    return expected.tool == actual.tool


def _collect_actual_steps(spans: list[Span]) -> list[TrajectoryStep]:
    return [_tool_to_step(tool) for tool in collect_tool_calls(spans, ordered=True)]


def _total_tokens(spans: list[Span]) -> tuple[int, bool]:
    llm_spans = [span for span in spans if span.kind == SpanKind.LLM]
    if not llm_spans:
        return 0, False
    if any(span.tokens is None for span in llm_spans):
        return 0, False
    total = sum(span.tokens.total for span in llm_spans if span.tokens is not None)
    return total, True


def _duration_seconds(spans: list[Span]) -> float:
    if not spans:
        return 0.0
    earliest = min(span.start_time for span in spans)
    latest = max(span.end_time for span in spans)
    return (latest - earliest).total_seconds()


def _trajectory_accuracy(matched_steps: int, expected_steps: int, actual_steps: int) -> float:
    if expected_steps == 0 and actual_steps == 0:
        return 1.0

    precision = 0.0 if actual_steps == 0 else matched_steps / actual_steps
    recall = 1.0 if expected_steps == 0 else matched_steps / expected_steps
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _step_efficiency(matched_steps: int, actual_steps: int, expected_steps: int) -> float:
    if actual_steps == 0 and expected_steps == 0:
        return 1.0
    if actual_steps == 0:
        return 0.0
    return matched_steps / actual_steps


def _match_exact(
    expected_steps: list[TrajectoryStep],
    actual_steps: list[TrajectoryStep],
    args_mode: TrajectoryArgsMode,
) -> tuple[int, dict[str, Any]]:
    matched_steps = 0
    missing_steps: list[dict[str, Any]] = []
    unexpected_steps: list[dict[str, Any]] = []
    argument_mismatches: list[dict[str, Any]] = []

    for index in range(max(len(expected_steps), len(actual_steps))):
        expected = expected_steps[index] if index < len(expected_steps) else None
        actual = actual_steps[index] if index < len(actual_steps) else None

        if expected is None and actual is not None:
            unexpected_steps.append(_step_payload(index, actual))
            continue
        if expected is not None and actual is None:
            missing_steps.append(_step_payload(index, expected))
            continue
        if expected is None or actual is None:
            continue

        if _steps_match(expected, actual, args_mode):
            matched_steps += 1
            continue

        if args_mode == TrajectoryArgsMode.EXACT and _same_tool(expected, actual):
            argument_mismatches.append(
                {
                    "expected_index": index,
                    "actual_index": index,
                    "tool": expected.tool,
                    "expected_args": expected.args,
                    "actual_args": actual.args,
                }
            )
            continue

        missing_steps.append(_step_payload(index, expected))
        unexpected_steps.append(_step_payload(index, actual))

    diagnostics = {
        "missing_steps": missing_steps,
        "unexpected_steps": unexpected_steps,
        "argument_mismatches": argument_mismatches,
        "budget_violations": [],
        "budget_warnings": [],
    }
    return matched_steps, diagnostics


def _pop_first_match(
    expected: TrajectoryStep,
    actual_steps: list[TrajectoryStep],
    actual_indexes: list[int],
    args_mode: TrajectoryArgsMode,
) -> tuple[int, TrajectoryStep] | None:
    for position, actual_index in enumerate(actual_indexes):
        actual = actual_steps[actual_index]
        if _steps_match(expected, actual, args_mode):
            actual_indexes.pop(position)
            return actual_index, actual
    return None


def _pop_first_same_tool(
    expected: TrajectoryStep,
    actual_steps: list[TrajectoryStep],
    actual_indexes: list[int],
) -> tuple[int, TrajectoryStep] | None:
    for position, actual_index in enumerate(actual_indexes):
        actual = actual_steps[actual_index]
        if _same_tool(expected, actual):
            actual_indexes.pop(position)
            return actual_index, actual
    return None


def _match_any_order(
    expected_steps: list[TrajectoryStep],
    actual_steps: list[TrajectoryStep],
    args_mode: TrajectoryArgsMode,
) -> tuple[int, dict[str, Any]]:
    matched_steps = 0
    missing_steps: list[dict[str, Any]] = []
    unexpected_steps: list[dict[str, Any]] = []
    argument_mismatches: list[dict[str, Any]] = []
    actual_indexes = list(range(len(actual_steps)))

    # Pass 1: exact matches only — prevents greedy same-tool consumption
    # from stealing actuals that would exactly match a later expected step.
    unmatched_expected: list[tuple[int, TrajectoryStep]] = []
    for expected_index, expected in enumerate(expected_steps):
        matched = _pop_first_match(expected, actual_steps, actual_indexes, args_mode)
        if matched is not None:
            matched_steps += 1
        else:
            unmatched_expected.append((expected_index, expected))

    # Pass 2: same-tool fallback for remaining unmatched expected steps.
    for expected_index, expected in unmatched_expected:
        if args_mode == TrajectoryArgsMode.EXACT:
            mismatched = _pop_first_same_tool(expected, actual_steps, actual_indexes)
            if mismatched is not None:
                actual_index, actual = mismatched
                argument_mismatches.append(
                    {
                        "expected_index": expected_index,
                        "actual_index": actual_index,
                        "tool": expected.tool,
                        "expected_args": expected.args,
                        "actual_args": actual.args,
                    }
                )
                continue

        missing_steps.append(_step_payload(expected_index, expected))

    unexpected_steps = [
        _step_payload(actual_index, actual_steps[actual_index]) for actual_index in actual_indexes
    ]

    diagnostics = {
        "missing_steps": missing_steps,
        "unexpected_steps": unexpected_steps,
        "argument_mismatches": argument_mismatches,
        "budget_violations": [],
        "budget_warnings": [],
    }
    return matched_steps, diagnostics


def _format_budget_violation(budget: str, actual: float, limit: float) -> str:
    if budget == "max_duration_seconds":
        return f"{budget} {actual:.1f}s > {limit:.1f}s"
    if budget == "max_tokens":
        return f"{budget} {int(actual)} > {int(limit)}"
    return f"{budget} {int(actual)} > {int(limit)}"


def _budget_violations(
    params: TrajectoryParams,
    spans: list[Span],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actual_steps = len(_collect_actual_steps(spans))
    total_tokens, has_tokens = _total_tokens(spans)
    duration_seconds = _duration_seconds(spans)
    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if params.max_steps is not None and actual_steps > params.max_steps:
        violations.append(
            {"budget": "max_steps", "limit": params.max_steps, "actual": actual_steps}
        )

    if params.max_tokens is not None:
        if not has_tokens:
            has_llm_spans = any(span.kind == SpanKind.LLM for span in spans)
            msg = (
                "Incomplete token data — budget not enforced."
                if has_llm_spans
                else "No LLM spans — token budget not applicable."
            )
            warnings.append({"budget": "max_tokens", "message": msg})
        elif total_tokens > params.max_tokens:
            violations.append(
                {"budget": "max_tokens", "limit": params.max_tokens, "actual": total_tokens}
            )

    if params.max_duration_seconds is not None and duration_seconds > params.max_duration_seconds:
        violations.append(
            {
                "budget": "max_duration_seconds",
                "limit": params.max_duration_seconds,
                "actual": round(duration_seconds, 3),
            }
        )

    return violations, warnings


def _format_counts(label: str, items: Iterable[object]) -> str | None:
    count = sum(1 for _ in items)
    if count == 0:
        return None
    return f"{count} {label}"


def _build_detail(
    matched_steps: int,
    expected_steps: int,
    actual_steps: int,
    diagnostics: dict[str, Any],
    scores: dict[str, float],
) -> str:
    parts = [
        f"matched {matched_steps}/{expected_steps} expected steps",
        f"actual {actual_steps} steps",
        f"accuracy {scores['trajectory_accuracy']:.3f}",
        f"efficiency {scores['step_efficiency']:.3f}",
    ]
    for label, key in (
        ("missing", "missing_steps"),
        ("unexpected", "unexpected_steps"),
        ("arg mismatches", "argument_mismatches"),
    ):
        summary = _format_counts(label, diagnostics[key])
        if summary:
            parts.append(summary)
    if diagnostics["budget_violations"]:
        budgets = ", ".join(
            _format_budget_violation(item["budget"], item["actual"], item["limit"])
            for item in diagnostics["budget_violations"]
        )
        parts.append(f"budget violations: {budgets}")
    if diagnostics["budget_warnings"]:
        warnings = ", ".join(item["message"] for item in diagnostics["budget_warnings"])
        parts.append(f"warnings: {warnings}")
    return "; ".join(parts)


def check_trajectory(spans: list[Span], params: dict[str, Any]) -> CheckResult:
    """Check canonical tool-call trajectories against expected steps."""
    trajectory = TrajectoryParams.model_validate(params)
    expected_steps = trajectory.steps
    actual_steps = _collect_actual_steps(spans)

    if trajectory.ordering == TrajectoryOrderingMode.EXACT:
        matched_steps, diagnostics = _match_exact(expected_steps, actual_steps, trajectory.args)
    else:
        matched_steps, diagnostics = _match_any_order(expected_steps, actual_steps, trajectory.args)

    budget_violations, budget_warnings = _budget_violations(trajectory, spans)
    diagnostics["budget_violations"] = budget_violations
    diagnostics["budget_warnings"] = budget_warnings

    trajectory_accuracy = _trajectory_accuracy(
        matched_steps, len(expected_steps), len(actual_steps)
    )
    step_efficiency = _step_efficiency(matched_steps, len(actual_steps), len(expected_steps))
    scores = {
        "trajectory_accuracy": round(trajectory_accuracy, 6),
        "step_efficiency": round(step_efficiency, 6),
    }

    passed = trajectory_accuracy >= trajectory.min_accuracy and not budget_violations
    detail = _build_detail(
        matched_steps,
        len(expected_steps),
        len(actual_steps),
        diagnostics,
        scores,
    )
    return CheckResult(
        check="trajectory",
        passed=passed,
        detail=detail,
        scores=scores,
        diagnostics=diagnostics,
    )
