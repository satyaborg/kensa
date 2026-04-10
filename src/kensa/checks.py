"""Deterministic checks for kensa trace evaluation.

Each check: (spans: list[Span], params: dict) → CheckResult
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from kensa.models import CheckResult, CheckType, Span, SpanKind
from kensa.trace_semantics import repeated_tool_names
from kensa.utils import get_agent_output, get_tool_names, get_tool_names_ordered


def check_output_contains(spans: list[Span], params: dict[str, Any]) -> CheckResult:
    """Check that agent output contains a string. Case-insensitive by default."""
    value = params.get("value", "")
    case_sensitive = params.get("case_sensitive", False)
    output = get_agent_output(spans)
    found = value in output if case_sensitive else value.lower() in output.lower()
    return CheckResult(
        check="output_contains",
        passed=found,
        detail=f"{'Found' if found else 'Not found'}: {value!r}",
    )


def check_output_matches(spans: list[Span], params: dict[str, Any]) -> CheckResult:
    """Check that agent output matches a regex pattern."""
    pattern = params.get("pattern", "")
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return CheckResult(
            check="output_matches",
            passed=False,
            detail=f"Invalid regex {pattern!r}: {e}",
        )
    output = get_agent_output(spans)
    matched = bool(regex.search(output))
    return CheckResult(
        check="output_matches",
        passed=matched,
        detail=f"{'Matched' if matched else 'No match'}: /{pattern}/",
    )


def check_tools_called(spans: list[Span], params: dict[str, Any]) -> CheckResult:
    """Check that all named tools were called."""
    expected: list[str] = params["tools"]
    actual = get_tool_names(spans)
    missing = [name for name in expected if name not in actual]
    passed = not missing
    if passed:
        detail = f"All expected tools called: {expected}"
    else:
        detail = f"Missing tools: {missing} (called: {actual})"
    return CheckResult(check="tools_called", passed=passed, detail=detail)


def check_tools_not_called(spans: list[Span], params: dict[str, Any]) -> CheckResult:
    """Check that none of the named tools were called."""
    forbidden: list[str] = params["tools"]
    actual = get_tool_names(spans)
    present = [name for name in forbidden if name in actual]
    passed = not present
    if passed:
        detail = f"None of the forbidden tools called: {forbidden}"
    else:
        detail = f"Forbidden tools called: {present}"
    return CheckResult(check="tools_not_called", passed=passed, detail=detail)


def check_tool_order(spans: list[Span], params: dict[str, Any]) -> CheckResult:
    """Check that tools were called in the expected order (as subsequence)."""
    expected = params.get("order", [])
    actual_names = get_tool_names_ordered(spans)

    it = iter(actual_names)
    matched = all(name in it for name in expected)
    return CheckResult(
        check="tool_order",
        passed=matched,
        detail=f"Expected order {expected}, actual: {actual_names}",
    )


def check_max_cost(spans: list[Span], params: dict[str, Any]) -> CheckResult:
    """Check that total cost is under threshold.

    Returns a warning (passed=True with explicit detail) when no cost data is
    present, so users know the check is vacuous rather than silently green.
    """
    max_cost = params.get("max_usd", params.get("max", float("inf")))
    total = sum(s.cost.total for s in spans if s.cost)
    if total == 0.0 and not any(s.cost for s in spans):
        return CheckResult(
            check="max_cost",
            passed=True,
            detail="WARNING: No cost data — check is vacuous. "
            "Ensure the SDK instrumentor populates cost info.",
        )
    passed = total <= max_cost
    return CheckResult(
        check="max_cost",
        passed=passed,
        detail=f"Total cost ${total:.4f} {'<=' if passed else '>'} ${max_cost:.4f}",
    )


def check_max_turns(spans: list[Span], params: dict[str, Any]) -> CheckResult:
    """Check that LLM call count is under threshold."""
    max_turns = params.get("max", float("inf"))
    llm_count = sum(1 for s in spans if s.kind == SpanKind.LLM)
    passed = llm_count <= max_turns
    return CheckResult(
        check="max_turns",
        passed=passed,
        detail=f"{llm_count} LLM calls {'<=' if passed else '>'} {max_turns}",
    )


def check_max_duration(spans: list[Span], params: dict[str, Any]) -> CheckResult:
    """Check that total elapsed time is under threshold."""
    max_seconds = params.get("max_seconds", float("inf"))
    if not spans:
        return CheckResult(check="max_duration", passed=True, detail="No spans")

    earliest = min(s.start_time for s in spans)
    latest = max(s.end_time for s in spans)
    duration = (latest - earliest).total_seconds()
    passed = duration <= max_seconds
    return CheckResult(
        check="max_duration",
        passed=passed,
        detail=f"{duration:.1f}s {'<=' if passed else '>'} {max_seconds}s",
    )


def check_no_repeat_calls(spans: list[Span], params: dict[str, Any]) -> CheckResult:
    """Check for duplicate tool calls (same name + args).

    Deduplicates across span types so the same call recorded on both
    a TOOL span and an LLM span isn't flagged as a repeat.
    """
    del params
    duplicates = repeated_tool_names(spans, ordered=True)
    passed = len(duplicates) == 0
    return CheckResult(
        check="no_repeat_calls",
        passed=passed,
        detail=f"{'No duplicates' if passed else f'Duplicates: {duplicates}'}",
    )


CheckFn = Callable[[list[Span], dict[str, Any]], CheckResult]

CHECK_REGISTRY: dict[CheckType, CheckFn] = {
    CheckType.OUTPUT_CONTAINS: check_output_contains,
    CheckType.OUTPUT_MATCHES: check_output_matches,
    CheckType.TOOLS_CALLED: check_tools_called,
    CheckType.TOOLS_NOT_CALLED: check_tools_not_called,
    CheckType.TOOL_ORDER: check_tool_order,
    CheckType.MAX_COST: check_max_cost,
    CheckType.MAX_TURNS: check_max_turns,
    CheckType.MAX_DURATION: check_max_duration,
    CheckType.NO_REPEAT_CALLS: check_no_repeat_calls,
}


def run_checks(spans: list[Span], checks: list[dict[str, Any]]) -> list[CheckResult]:
    """Run a list of checks against spans. Each check dict has 'type' and 'params'."""
    results: list[CheckResult] = []
    for check_def in checks:
        check_type = CheckType(check_def["type"])
        check_fn = CHECK_REGISTRY[check_type]
        params = check_def.get("params", {})
        results.append(check_fn(spans, params))
    return results
