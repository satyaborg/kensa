"""Tests for deterministic trajectory matching."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kensa.models import Span, SpanKind, TokenCounts, ToolInfo
from kensa.trajectory import check_trajectory

T0 = datetime(2026, 3, 17, 14, 30, 0, tzinfo=timezone.utc)


def _llm(
    *,
    start: float = 0,
    dur: float = 1,
    tools: list[ToolInfo] | None = None,
    tokens: TokenCounts | None = None,
) -> Span:
    return Span(
        trace_id="t",
        span_id=f"llm-{start}",
        name="llm",
        kind=SpanKind.LLM,
        start_time=T0 + timedelta(seconds=start),
        end_time=T0 + timedelta(seconds=start + dur),
        tools=tools or [],
        tokens=tokens,
    )


def _tool(name: str, args: dict | None = None, *, start: float = 0, dur: float = 0.5) -> Span:
    return Span(
        trace_id="t",
        span_id=f"tool-{name}-{start}",
        name=name,
        kind=SpanKind.TOOL,
        start_time=T0 + timedelta(seconds=start),
        end_time=T0 + timedelta(seconds=start + dur),
        tools=[ToolInfo(name=name, args=args or {})],
    )


class TestTrajectoryExact:
    def test_exact_match_passes_with_perfect_scores(self) -> None:
        spans = [
            _tool("search_docs", {"query": "refund"}, start=0),
            _tool("open_url", {"url": "/refunds"}, start=1),
        ]
        result = check_trajectory(
            spans,
            {
                "steps": [
                    {"tool": "search_docs", "args": {"query": "refund"}},
                    {"tool": "open_url", "args": {"url": "/refunds"}},
                ],
                "ordering": "exact",
                "args": "exact",
            },
        )
        assert result.passed is True
        assert result.scores["trajectory_accuracy"] == 1.0
        assert result.scores["step_efficiency"] == 1.0
        assert result.diagnostics["missing_steps"] == []
        assert result.diagnostics["unexpected_steps"] == []

    def test_exact_arg_mismatch_fails_with_diagnostic(self) -> None:
        spans = [_tool("search_docs", {"query": "returns"}, start=0)]
        result = check_trajectory(
            spans,
            {
                "steps": [{"tool": "search_docs", "args": {"query": "refund"}}],
                "ordering": "exact",
                "args": "exact",
            },
        )
        assert result.passed is False
        assert result.scores["trajectory_accuracy"] == 0.0
        assert result.diagnostics["argument_mismatches"] == [
            {
                "expected_index": 0,
                "actual_index": 0,
                "tool": "search_docs",
                "expected_args": {"query": "refund"},
                "actual_args": {"query": "returns"},
            }
        ]

    def test_exact_extra_step_reduces_scores(self) -> None:
        spans = [
            _tool("search_docs", {"query": "refund"}, start=0),
            _tool("open_url", {"url": "/refunds"}, start=1),
            _tool("summarize", {}, start=2),
        ]
        result = check_trajectory(
            spans,
            {
                "steps": [
                    {"tool": "search_docs", "args": {"query": "refund"}},
                    {"tool": "open_url", "args": {"url": "/refunds"}},
                ],
                "ordering": "exact",
                "args": "exact",
                "min_accuracy": 0.9,
            },
        )
        assert result.passed is False
        assert result.scores["trajectory_accuracy"] == 0.8
        assert round(result.scores["step_efficiency"], 6) == round(2 / 3, 6)
        assert result.diagnostics["unexpected_steps"][0]["tool"] == "summarize"


class TestTrajectoryAnyOrder:
    def test_any_order_accepts_reordered_steps(self) -> None:
        spans = [
            _tool("open_url", {"url": "/refunds"}, start=0),
            _tool("search_docs", {"query": "refund"}, start=1),
        ]
        result = check_trajectory(
            spans,
            {
                "steps": [
                    {"tool": "search_docs", "args": {"query": "refund"}},
                    {"tool": "open_url", "args": {"url": "/refunds"}},
                ],
                "ordering": "any_order",
                "args": "exact",
            },
        )
        assert result.passed is True
        assert result.scores["trajectory_accuracy"] == 1.0

    def test_any_order_with_ignore_args_matches_name_only(self) -> None:
        spans = [_tool("search_docs", {"query": "returns"}, start=0)]
        result = check_trajectory(
            spans,
            {
                "steps": [{"tool": "search_docs", "args": {"query": "refund"}}],
                "ordering": "any_order",
                "args": "ignore",
            },
        )
        assert result.passed is True
        assert result.diagnostics["argument_mismatches"] == []

    def test_any_order_pairs_same_tool_arg_mismatch(self) -> None:
        spans = [_tool("search_docs", {"query": "returns"}, start=0)]
        result = check_trajectory(
            spans,
            {
                "steps": [{"tool": "search_docs", "args": {"query": "refund"}}],
                "ordering": "any_order",
                "args": "exact",
            },
        )
        assert result.passed is False
        assert result.diagnostics["missing_steps"] == []
        assert result.diagnostics["unexpected_steps"] == []
        assert result.diagnostics["argument_mismatches"][0]["tool"] == "search_docs"

    def test_any_order_handles_duplicate_tools(self) -> None:
        spans = [
            _tool("search_docs", {"query": "a"}, start=0),
            _tool("search_docs", {"query": "b"}, start=1),
        ]
        result = check_trajectory(
            spans,
            {
                "steps": [
                    {"tool": "search_docs", "args": {"query": "a"}},
                    {"tool": "search_docs", "args": {"query": "c"}},
                ],
                "ordering": "any_order",
                "args": "exact",
            },
        )
        assert result.passed is False
        assert result.diagnostics["argument_mismatches"][0]["actual_args"] == {"query": "b"}

    def test_any_order_exact_prefers_exact_match_over_greedy_same_tool(self) -> None:
        """Regression: greedy same-tool fallback must not steal an actual step
        that would exactly match a later expected step."""
        spans = [
            _tool("search_docs", {"query": "y"}, start=0),
            _tool("search_docs", {"query": "z"}, start=1),
        ]
        result = check_trajectory(
            spans,
            {
                "steps": [
                    {"tool": "search_docs", "args": {"query": "x"}},
                    {"tool": "search_docs", "args": {"query": "y"}},
                ],
                "ordering": "any_order",
                "args": "exact",
            },
        )
        # expected[1]=y should exact-match actual[0]=y; expected[0]=x falls back to actual[1]=z
        assert result.scores["trajectory_accuracy"] > 0.0
        assert len(result.diagnostics["argument_mismatches"]) == 1
        assert result.diagnostics["argument_mismatches"][0]["expected_args"] == {"query": "x"}
        assert result.diagnostics["argument_mismatches"][0]["actual_args"] == {"query": "z"}
        assert result.diagnostics["missing_steps"] == []


class TestTrajectoryBudgets:
    def test_max_steps_budget_violation_fails(self) -> None:
        spans = [_tool("search_docs", start=0), _tool("open_url", start=1)]
        result = check_trajectory(
            spans,
            {
                "steps": [{"tool": "search_docs"}, {"tool": "open_url"}],
                "max_steps": 1,
            },
        )
        assert result.passed is False
        assert result.diagnostics["budget_violations"] == [
            {"budget": "max_steps", "limit": 1, "actual": 2}
        ]

    def test_max_tokens_budget_violation_fails(self) -> None:
        spans = [
            _llm(
                start=0,
                tokens=TokenCounts(prompt=40, completion=30, total=70),
                tools=[ToolInfo(name="search_docs", args={})],
            )
        ]
        result = check_trajectory(
            spans,
            {
                "steps": [{"tool": "search_docs"}],
                "max_tokens": 50,
            },
        )
        assert result.passed is False
        assert result.diagnostics["budget_violations"] == [
            {"budget": "max_tokens", "limit": 50, "actual": 70}
        ]

    def test_missing_token_data_emits_warning_not_failure(self) -> None:
        spans = [_tool("search_docs", start=0)]
        result = check_trajectory(
            spans,
            {
                "steps": [{"tool": "search_docs"}],
                "max_tokens": 50,
            },
        )
        assert result.passed is True
        assert result.diagnostics["budget_violations"] == []
        assert result.diagnostics["budget_warnings"] == [
            {
                "budget": "max_tokens",
                "message": "No LLM spans — token budget not applicable.",
            }
        ]

    def test_partial_token_data_emits_warning_not_partial_enforcement(self) -> None:
        spans = [
            _llm(
                start=0,
                tokens=TokenCounts(prompt=40, completion=30, total=70),
                tools=[ToolInfo(name="search_docs", args={})],
            ),
            _llm(
                start=1,
                tools=[ToolInfo(name="open_url", args={})],
            ),
        ]
        result = check_trajectory(
            spans,
            {
                "steps": [{"tool": "search_docs"}, {"tool": "open_url"}],
                "max_tokens": 50,
            },
        )
        assert result.passed is True
        assert result.diagnostics["budget_violations"] == []
        assert result.diagnostics["budget_warnings"] == [
            {
                "budget": "max_tokens",
                "message": "Incomplete token data — budget not enforced.",
            }
        ]

    def test_max_duration_budget_violation_fails(self) -> None:
        spans = [_tool("search_docs", start=0, dur=3), _tool("open_url", start=5, dur=1)]
        result = check_trajectory(
            spans,
            {
                "steps": [{"tool": "search_docs"}, {"tool": "open_url"}],
                "max_duration_seconds": 5.0,
            },
        )
        assert result.passed is False
        assert result.diagnostics["budget_violations"] == [
            {"budget": "max_duration_seconds", "limit": 5.0, "actual": 6.0}
        ]


class TestTrajectoryEdgeCases:
    def test_no_actual_steps_scores_zero(self) -> None:
        result = check_trajectory([], {"steps": [{"tool": "search_docs"}], "min_accuracy": 0.0})
        assert result.scores["trajectory_accuracy"] == 0.0
        assert result.scores["step_efficiency"] == 0.0

    def test_detail_summarizes_mismatch_and_warning(self) -> None:
        spans = [_tool("search_docs", {"query": "returns"}, start=0)]
        result = check_trajectory(
            spans,
            {
                "steps": [{"tool": "search_docs", "args": {"query": "refund"}}],
                "max_tokens": 10,
            },
        )
        assert "arg mismatches" in result.detail
        assert "warnings" in result.detail
