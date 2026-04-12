"""Tests for the deterministic checks."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from kensa.checks import (
    CHECK_REGISTRY,
    check_max_cost,
    check_max_duration,
    check_max_turns,
    check_no_repeat_calls,
    check_output_contains,
    check_output_matches,
    check_tool_order,
    check_tools_called,
    check_tools_not_called,
    run_checks,
)
from kensa.models import CheckType, CostInfo, Span, SpanKind, TokenCounts, ToolInfo

T0 = datetime(2026, 3, 17, 14, 30, 0, tzinfo=timezone.utc)


def _llm(
    output: dict | None = None,
    *,
    start: float = 0,
    dur: float = 1,
    tools: list[ToolInfo] | None = None,
    cost: CostInfo | None = None,
    tokens: TokenCounts | None = None,
) -> Span:
    return Span(
        trace_id="t",
        span_id=f"s-{start}",
        name="llm",
        kind=SpanKind.LLM,
        start_time=T0 + timedelta(seconds=start),
        end_time=T0 + timedelta(seconds=start + dur),
        output=output,
        tools=tools or [],
        cost=cost,
        tokens=tokens,
    )


def _tool(
    name: str,
    args: dict | None = None,
    *,
    start: float = 0,
    dur: float = 0.5,
) -> Span:
    return Span(
        trace_id="t",
        span_id=f"ts-{name}-{start}",
        name=name,
        kind=SpanKind.TOOL,
        start_time=T0 + timedelta(seconds=start),
        end_time=T0 + timedelta(seconds=start + dur),
        tools=[ToolInfo(name=name, args=args or {})],
    )


class TestOutputContains:
    """Check: output_contains — substring match on final LLM output."""

    def test_found(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "Hello world"}]})]
        assert check_output_contains(spans, {"value": "world"}).passed is True

    def test_not_found(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "Hello world"}]})]
        assert check_output_contains(spans, {"value": "mars"}).passed is False

    def test_case_insensitive_by_default(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "Hello World"}]})]
        assert check_output_contains(spans, {"value": "hello world"}).passed is True

    def test_case_sensitive_opt_in(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "Hello World"}]})]
        r = check_output_contains(spans, {"value": "hello world", "case_sensitive": True})
        assert r.passed is False

    def test_case_sensitive_exact(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "Hello World"}]})]
        r = check_output_contains(spans, {"value": "Hello World", "case_sensitive": True})
        assert r.passed is True

    def test_empty_spans(self) -> None:
        assert check_output_contains([], {"value": "anything"}).passed is False

    def test_uses_last_llm_span_not_first(self) -> None:
        """Multiple LLM spans — check should use the one with latest end_time."""
        early = _llm(
            {"messages": [{"role": "assistant", "content": "early answer"}]},
            start=0,
        )
        late = _llm(
            {"messages": [{"role": "assistant", "content": "final answer"}]},
            start=5,
        )
        assert check_output_contains([early, late], {"value": "final"}).passed is True
        assert check_output_contains([early, late], {"value": "early"}).passed is False

    def test_ignores_tool_spans(self) -> None:
        """TOOL spans should not contribute to output."""
        tool = _tool("search")
        llm = _llm({"messages": [{"role": "assistant", "content": "result"}]}, start=1)
        assert check_output_contains([tool, llm], {"value": "result"}).passed is True
        assert check_output_contains([tool, llm], {"value": "search"}).passed is False

    def test_unicode(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "Tōkyō 東京 🗼"}]})]
        assert check_output_contains(spans, {"value": "東京"}).passed is True
        assert check_output_contains(spans, {"value": "🗼"}).passed is True

    def test_newlines_in_output(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "line1\nline2\nline3"}]})]
        assert check_output_contains(spans, {"value": "line2"}).passed is True
        assert check_output_contains(spans, {"value": "line1\nline2"}).passed is True

    def test_value_format_anthropic(self) -> None:
        """Output stored as JSON-serialized Anthropic response in output.value."""
        response = json.dumps({"content": [{"type": "text", "text": "P1"}]})
        spans = [_llm({"value": response})]
        assert check_output_contains(spans, {"value": "P1"}).passed is True
        assert check_output_contains(spans, {"value": "P4"}).passed is False

    def test_value_format_openai(self) -> None:
        """Output stored as JSON-serialized OpenAI response in output.value."""
        response = json.dumps({"choices": [{"message": {"content": "POSITIVE"}}]})
        spans = [_llm({"value": response})]
        assert check_output_contains(spans, {"value": "POSITIVE"}).passed is True

    def test_empty_value_param_matches_anything(self) -> None:
        """Empty string is always a substring of any string — vacuous pass."""
        spans = [_llm({"messages": [{"role": "assistant", "content": "anything"}]})]
        assert check_output_contains(spans, {"value": ""}).passed is True
        assert check_output_contains(spans, {}).passed is True

    def test_missing_value_param_on_empty_output(self) -> None:
        """Empty value on empty output: '' in '' is True."""
        spans = [_llm({"messages": [{"role": "assistant", "content": ""}]})]
        assert check_output_contains(spans, {}).passed is True

    def test_special_regex_chars_treated_literally(self) -> None:
        """Not a regex — special chars should be literal."""
        spans = [_llm({"messages": [{"role": "assistant", "content": "price is $10.00"}]})]
        assert check_output_contains(spans, {"value": "$10.00"}).passed is True
        assert check_output_contains(spans, {"value": "$10.0+"}).passed is False

    def test_detail_message(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "hello"}]})]
        r = check_output_contains(spans, {"value": "xyz"})
        assert r.check == "output_contains"
        assert "Not found" in r.detail


class TestOutputMatches:
    """Check: output_matches — regex search on final LLM output."""

    def test_simple_match(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "temp is 72°F"}]})]
        assert check_output_matches(spans, {"pattern": r"\d+°F"}).passed is True

    def test_no_match(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "hello"}]})]
        assert check_output_matches(spans, {"pattern": r"\d+"}).passed is False

    def test_invalid_regex(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "hello"}]})]
        r = check_output_matches(spans, {"pattern": "[invalid"})
        assert r.passed is False
        assert "Invalid regex" in r.detail

    def test_anchored_regex_exact_output(self) -> None:
        """Anchored regex on clean extracted text — the triage scenario case."""
        response = json.dumps({"content": [{"type": "text", "text": "P1"}]})
        spans = [_llm({"value": response})]
        assert check_output_matches(spans, {"pattern": r"^P[123]$"}).passed is True

    def test_anchored_regex_no_match(self) -> None:
        response = json.dumps({"content": [{"type": "text", "text": "P4"}]})
        spans = [_llm({"value": response})]
        assert check_output_matches(spans, {"pattern": r"^P[123]$"}).passed is False

    def test_search_not_match(self) -> None:
        """regex.search — pattern doesn't need to match from start."""
        spans = [_llm({"messages": [{"role": "assistant", "content": "the answer is 42."}]})]
        assert check_output_matches(spans, {"pattern": r"\d+"}).passed is True

    def test_multiline_output_anchors(self) -> None:
        """Without re.MULTILINE, ^ and $ anchor to start/end of entire string."""
        spans = [_llm({"messages": [{"role": "assistant", "content": "line1\nP2\nline3"}]})]
        assert check_output_matches(spans, {"pattern": r"^P2$"}).passed is False
        assert check_output_matches(spans, {"pattern": r"P2"}).passed is True

    def test_complex_pattern_with_groups(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "Error code: E-404"}]})]
        assert check_output_matches(spans, {"pattern": r"E-(\d{3})"}).passed is True

    def test_empty_pattern_matches_anything(self) -> None:
        """Empty regex matches at every position — vacuous pass."""
        spans = [_llm({"messages": [{"role": "assistant", "content": "anything"}]})]
        assert check_output_matches(spans, {"pattern": ""}).passed is True
        assert check_output_matches(spans, {}).passed is True

    def test_empty_pattern_on_empty_output(self) -> None:
        """Empty pattern on empty output still matches (zero-width match at position 0)."""
        assert check_output_matches([], {"pattern": ""}).passed is True

    def test_dot_doesnt_match_newline_by_default(self) -> None:
        spans = [_llm({"messages": [{"role": "assistant", "content": "a\nb"}]})]
        assert check_output_matches(spans, {"pattern": r"a.b"}).passed is False
        assert check_output_matches(spans, {"pattern": r"a\nb"}).passed is True

    def test_openai_value_format(self) -> None:
        response = json.dumps({"choices": [{"message": {"content": "NEGATIVE"}}]})
        spans = [_llm({"value": response})]
        assert check_output_matches(spans, {"pattern": r"^(POSITIVE|NEGATIVE|NEUTRAL)$"}).passed

    def test_uses_last_llm_span(self) -> None:
        early = _llm({"messages": [{"role": "assistant", "content": "FAIL"}]}, start=0)
        late = _llm({"messages": [{"role": "assistant", "content": "PASS"}]}, start=2)
        assert check_output_matches([early, late], {"pattern": r"^PASS$"}).passed is True
        assert check_output_matches([early, late], {"pattern": r"^FAIL$"}).passed is False

class TestToolsCalled:
    """Check: tools_called — all named tools must appear in spans (set membership)."""

    def test_single_tool_found(self) -> None:
        assert check_tools_called([_tool("search")], {"tools": ["search"]}).passed is True

    def test_single_tool_missing(self) -> None:
        assert check_tools_called([_tool("search")], {"tools": ["delete"]}).passed is False

    def test_all_present(self) -> None:
        spans = [_tool("search", start=0), _tool("summarize", start=1)]
        assert check_tools_called(spans, {"tools": ["search", "summarize"]}).passed is True

    def test_one_missing(self) -> None:
        spans = [_tool("search", start=0), _tool("summarize", start=1)]
        r = check_tools_called(spans, {"tools": ["search", "delete"]})
        assert r.passed is False
        assert "delete" in r.detail

    def test_multiple_missing_all_named(self) -> None:
        spans = [_tool("search", start=0)]
        r = check_tools_called(spans, {"tools": ["search", "alpha", "beta"]})
        assert r.passed is False
        assert "alpha" in r.detail
        assert "beta" in r.detail

    def test_order_does_not_matter(self) -> None:
        """Set membership — any temporal order passes."""
        spans = [_tool("b", start=0), _tool("a", start=1)]
        assert check_tools_called(spans, {"tools": ["a", "b"]}).passed is True

    def test_extra_tools_ignored(self) -> None:
        """Tools beyond the expected set don't cause failure."""
        spans = [_tool("a", start=0), _tool("x", start=1), _tool("b", start=2)]
        assert check_tools_called(spans, {"tools": ["a", "b"]}).passed is True

    def test_empty_spans_with_nonempty_expected(self) -> None:
        assert check_tools_called([], {"tools": ["search"]}).passed is False

    def test_found_on_llm_span(self) -> None:
        llm = _llm(None, tools=[ToolInfo(name="calc", args={})])
        assert check_tools_called([llm], {"tools": ["calc"]}).passed is True

    def test_parallel_tools_on_single_span(self) -> None:
        llm = _llm(
            None,
            tools=[ToolInfo(name="a", args={}), ToolInfo(name="b", args={})],
        )
        assert check_tools_called([llm], {"tools": ["a", "b"]}).passed is True
        assert check_tools_called([llm], {"tools": ["a", "c"]}).passed is False

    def test_case_sensitive(self) -> None:
        assert check_tools_called([_tool("Search")], {"tools": ["search"]}).passed is False
        assert check_tools_called([_tool("Search")], {"tools": ["Search"]}).passed is True

    def test_substring_no_match(self) -> None:
        """'search' should not match 'search_v2'."""
        assert check_tools_called([_tool("search_v2")], {"tools": ["search"]}).passed is False

    def test_tool_on_both_tool_and_llm_span(self) -> None:
        """Dedup doesn't cause false negatives."""
        tool = _tool("search", start=0)
        llm = _llm(None, start=1, tools=[ToolInfo(name="search", args={})])
        assert check_tools_called([tool, llm], {"tools": ["search"]}).passed is True

    def test_tool_span_without_tools_list_uses_span_name(self) -> None:
        span = Span(
            trace_id="t",
            span_id="s",
            name="get_weather",
            kind=SpanKind.TOOL,
            start_time=T0,
            end_time=T0 + timedelta(seconds=1),
            tools=[],
        )
        assert check_tools_called([span], {"tools": ["get_weather"]}).passed is True

    def test_detail_lists_actual_tools_on_failure(self) -> None:
        spans = [_tool("a"), _tool("b", start=1)]
        r = check_tools_called(spans, {"tools": ["c"]})
        assert "a" in r.detail
        assert "b" in r.detail
        assert "c" in r.detail


class TestToolsNotCalled:
    """Check: tools_not_called — none of the named tools may appear in spans."""

    def test_single_tool_absent(self) -> None:
        assert check_tools_not_called([_tool("search")], {"tools": ["delete"]}).passed is True

    def test_single_tool_present(self) -> None:
        assert check_tools_not_called([_tool("delete")], {"tools": ["delete"]}).passed is False

    def test_all_absent(self) -> None:
        spans = [_tool("search", start=0), _tool("summarize", start=1)]
        assert check_tools_not_called(spans, {"tools": ["delete", "drop"]}).passed is True

    def test_one_present(self) -> None:
        spans = [_tool("search", start=0), _tool("delete", start=1)]
        r = check_tools_not_called(spans, {"tools": ["delete", "drop"]})
        assert r.passed is False
        assert "delete" in r.detail
        assert "drop" not in r.detail

    def test_multiple_present(self) -> None:
        spans = [_tool("delete", start=0), _tool("drop", start=1)]
        r = check_tools_not_called(spans, {"tools": ["delete", "drop"]})
        assert r.passed is False
        assert "delete" in r.detail
        assert "drop" in r.detail

    def test_empty_spans(self) -> None:
        assert check_tools_not_called([], {"tools": ["anything"]}).passed is True

    def test_present_on_llm_span_only(self) -> None:
        llm = _llm(None, tools=[ToolInfo(name="danger", args={})])
        assert check_tools_not_called([llm], {"tools": ["danger"]}).passed is False

    def test_substring_no_false_positive(self) -> None:
        """'delete' should not match 'delete_draft'."""
        spans = [_tool("delete_draft")]
        assert check_tools_not_called(spans, {"tools": ["delete"]}).passed is True


class TestToolOrder:
    """Check: tool_order — expected names appear as subsequence in temporal order."""

    def test_correct_order(self) -> None:
        spans = [_tool("a", start=0), _tool("b", start=1), _tool("c", start=2)]
        assert check_tool_order(spans, {"order": ["a", "b", "c"]}).passed is True

    def test_wrong_order(self) -> None:
        spans = [_tool("b", start=0), _tool("a", start=1)]
        assert check_tool_order(spans, {"order": ["a", "b"]}).passed is False

    def test_empty_spans(self) -> None:
        assert check_tool_order([], {"order": ["a"]}).passed is False

    def test_subsequence_with_extras(self) -> None:
        """Expected order is subsequence — interleaved tools OK."""
        spans = [_tool("a", start=0), _tool("x", start=1), _tool("b", start=2)]
        assert check_tool_order(spans, {"order": ["a", "b"]}).passed is True

    def test_tools_on_llm_spans(self) -> None:
        llm1 = _llm(None, start=0, tools=[ToolInfo(name="fetch", args={})])
        llm2 = _llm(None, start=2, tools=[ToolInfo(name="summarize", args={})])
        assert check_tool_order([llm1, llm2], {"order": ["fetch", "summarize"]}).passed is True

    def test_mixed_tool_and_llm_spans(self) -> None:
        tool = _tool("lookup", start=0)
        llm = _llm(None, start=2, tools=[ToolInfo(name="respond", args={})])
        assert check_tool_order([tool, llm], {"order": ["lookup", "respond"]}).passed is True

    def test_expected_longer_than_actual(self) -> None:
        """More expected than actual tools — must fail."""
        assert check_tool_order([_tool("a")], {"order": ["a", "b"]}).passed is False

    def test_repeated_tool_in_expected_order(self) -> None:
        """Expected: [search, process, search] — tool called twice with process between."""
        spans = [
            _tool("search", start=0),
            _tool("process", start=1),
            _tool("search", start=2),
        ]
        assert check_tool_order(spans, {"order": ["search", "process", "search"]}).passed is True

    def test_repeated_tool_insufficient_occurrences(self) -> None:
        """Expected [search, search] but search only called once."""
        spans = [_tool("search", start=0), _tool("other", start=1)]
        assert check_tool_order(spans, {"order": ["search", "search"]}).passed is False

    def test_parallel_tools_same_span_preserves_order(self) -> None:
        """Multiple tools on same LLM span — order within the tools list matters."""
        llm = _llm(
            None,
            start=0,
            tools=[ToolInfo(name="a", args={}), ToolInfo(name="b", args={})],
        )
        assert check_tool_order([llm], {"order": ["a", "b"]}).passed is True
        assert check_tool_order([llm], {"order": ["b", "a"]}).passed is False

    def test_spans_sorted_by_start_time_not_list_order(self) -> None:
        """Spans passed in wrong list order — should still sort by start_time."""
        late = _tool("b", start=5)
        early = _tool("a", start=0)
        assert check_tool_order([late, early], {"order": ["a", "b"]}).passed is True

    def test_detail_shows_actual_sequence(self) -> None:
        llm = _llm(None, tools=[ToolInfo(name="alpha", args={})])
        r = check_tool_order([llm], {"order": ["beta"]})
        assert "alpha" in r.detail
        assert "beta" in r.detail

    def test_single_tool_expected(self) -> None:
        spans = [_tool("a", start=0), _tool("b", start=1), _tool("c", start=2)]
        assert check_tool_order(spans, {"order": ["b"]}).passed is True

    def test_full_sequence_expected(self) -> None:
        spans = [_tool("a", start=0), _tool("b", start=1), _tool("c", start=2)]
        assert check_tool_order(spans, {"order": ["a", "b", "c"]}).passed is True


class TestMaxCost:
    """Check: max_cost — total cost across spans must not exceed threshold."""

    def test_under_budget(self) -> None:
        spans = [_llm(None, cost=CostInfo(prompt=0.001, completion=0.002, total=0.003))]
        assert check_max_cost(spans, {"max_usd": 0.05}).passed is True

    def test_over_budget(self) -> None:
        spans = [_llm(None, cost=CostInfo(prompt=0.05, completion=0.05, total=0.10))]
        assert check_max_cost(spans, {"max_usd": 0.05}).passed is False

    def test_no_cost_data_is_vacuous_warning(self) -> None:
        spans = [_llm(None)]
        r = check_max_cost(spans, {"max_usd": 0.05})
        assert r.passed is True
        assert "WARNING" in r.detail
        assert "vacuous" in r.detail

    def test_empty_spans(self) -> None:
        r = check_max_cost([], {"max_usd": 0.05})
        assert r.passed is True

    def test_exactly_at_boundary(self) -> None:
        """total == max should pass (<=)."""
        spans = [_llm(None, cost=CostInfo(prompt=0.025, completion=0.025, total=0.05))]
        assert check_max_cost(spans, {"max_usd": 0.05}).passed is True

    def test_accumulates_across_spans(self) -> None:
        """Cost should be summed across all spans."""
        s1 = _llm(None, start=0, cost=CostInfo(prompt=0.01, completion=0.01, total=0.02))
        s2 = _llm(None, start=2, cost=CostInfo(prompt=0.02, completion=0.02, total=0.04))
        assert check_max_cost([s1, s2], {"max_usd": 0.05}).passed is False
        assert check_max_cost([s1, s2], {"max_usd": 0.10}).passed is True

    def test_mixed_spans_some_without_cost(self) -> None:
        """Spans without cost should be skipped, not treated as vacuous."""
        with_cost = _llm(None, cost=CostInfo(prompt=0.01, completion=0.01, total=0.02))
        without_cost = _llm(None, start=2)
        r = check_max_cost([with_cost, without_cost], {"max_usd": 0.05})
        assert r.passed is True
        assert "WARNING" not in r.detail

    def test_zero_cost_not_vacuous(self) -> None:
        """Cost data present but zero — legitimate, not a warning."""
        spans = [_llm(None, cost=CostInfo(prompt=0.0, completion=0.0, total=0.0))]
        r = check_max_cost(spans, {"max_usd": 0.05})
        assert r.passed is True
        assert "WARNING" not in r.detail

    def test_legacy_max_param_still_works(self) -> None:
        """Backwards compat: 'max' param should still work."""
        spans = [_llm(None, cost=CostInfo(prompt=0.05, completion=0.05, total=0.10))]
        assert check_max_cost(spans, {"max": 0.05}).passed is False
        assert check_max_cost(spans, {"max": 0.20}).passed is True

    def test_max_usd_takes_precedence_over_max(self) -> None:
        """If both present, max_usd wins."""
        spans = [_llm(None, cost=CostInfo(prompt=0.03, completion=0.03, total=0.06))]
        assert check_max_cost(spans, {"max_usd": 0.05, "max": 0.10}).passed is False

    def test_missing_param_defaults_to_inf(self) -> None:
        """No threshold param — defaults to infinity, always passes."""
        spans = [_llm(None, cost=CostInfo(prompt=100, completion=100, total=200))]
        assert check_max_cost(spans, {}).passed is True

    def test_floating_point_boundary(self) -> None:
        """Tiny costs that sum to boundary — floating point sensitive."""
        spans = [
            _llm(None, start=i, cost=CostInfo(prompt=0.005, completion=0.005, total=0.01))
            for i in range(3)
        ]
        assert check_max_cost(spans, {"max_usd": 0.03}).passed is True

    def test_detail_shows_amounts(self) -> None:
        spans = [_llm(None, cost=CostInfo(prompt=0.01, completion=0.01, total=0.02))]
        r = check_max_cost(spans, {"max_usd": 0.01})
        assert "$0.0200" in r.detail
        assert "$0.0100" in r.detail


class TestMaxTurns:
    """Check: max_turns — count of LLM-kind spans must not exceed threshold."""

    def test_under_limit(self) -> None:
        spans = [_llm(None, start=0), _llm(None, start=1)]
        assert check_max_turns(spans, {"max": 5}).passed is True

    def test_over_limit(self) -> None:
        spans = [_llm(None, start=0), _llm(None, start=1)]
        assert check_max_turns(spans, {"max": 1}).passed is False

    def test_empty_spans(self) -> None:
        assert check_max_turns([], {"max": 0}).passed is True

    def test_exactly_at_boundary(self) -> None:
        spans = [_llm(None, start=0), _llm(None, start=1), _llm(None, start=2)]
        assert check_max_turns(spans, {"max": 3}).passed is True

    def test_one_over_boundary(self) -> None:
        spans = [_llm(None, start=i) for i in range(4)]
        assert check_max_turns(spans, {"max": 3}).passed is False

    def test_non_llm_spans_ignored(self) -> None:
        """TOOL, CHAIN, AGENT spans should not count as turns."""
        spans = [
            _llm(None, start=0),
            _tool("search", start=1),
            Span(
                trace_id="t",
                span_id="chain",
                name="chain",
                kind=SpanKind.CHAIN,
                start_time=T0 + timedelta(seconds=2),
                end_time=T0 + timedelta(seconds=3),
            ),
            Span(
                trace_id="t",
                span_id="agent",
                name="agent",
                kind=SpanKind.AGENT,
                start_time=T0 + timedelta(seconds=3),
                end_time=T0 + timedelta(seconds=4),
            ),
        ]
        assert check_max_turns(spans, {"max": 1}).passed is True

    def test_max_zero_with_any_llm(self) -> None:
        assert check_max_turns([_llm(None)], {"max": 0}).passed is False

    def test_missing_param_defaults_to_inf(self) -> None:
        spans = [_llm(None, start=i) for i in range(100)]
        assert check_max_turns(spans, {}).passed is True

    def test_detail_format(self) -> None:
        spans = [_llm(None, start=0), _llm(None, start=1)]
        r = check_max_turns(spans, {"max": 1})
        assert "2 LLM calls" in r.detail
        assert ">" in r.detail


class TestMaxDuration:
    """Check: max_duration — wall-clock time from earliest start to latest end."""

    def test_under_limit(self) -> None:
        spans = [_llm(None, start=0, dur=2)]
        assert check_max_duration(spans, {"max_seconds": 5}).passed is True

    def test_over_limit(self) -> None:
        spans = [_llm(None, start=0, dur=10)]
        assert check_max_duration(spans, {"max_seconds": 5}).passed is False

    def test_empty_spans(self) -> None:
        r = check_max_duration([], {"max_seconds": 10})
        assert r.passed is True
        assert "No spans" in r.detail

    def test_exactly_at_boundary(self) -> None:
        spans = [_llm(None, start=0, dur=5)]
        assert check_max_duration(spans, {"max_seconds": 5}).passed is True

    def test_wall_clock_not_sum(self) -> None:
        """Overlapping spans — duration is wall clock, not sum of individual durations."""
        s1 = _llm(None, start=0, dur=3)
        s2 = _llm(None, start=1, dur=3)
        assert check_max_duration([s1, s2], {"max_seconds": 4}).passed is True
        assert check_max_duration([s1, s2], {"max_seconds": 3}).passed is False

    def test_non_llm_spans_count(self) -> None:
        """Duration spans all span types, not just LLM."""
        tool = _tool("search", start=0, dur=1)
        llm = _llm(None, start=5, dur=1)
        assert check_max_duration([tool, llm], {"max_seconds": 6}).passed is True
        assert check_max_duration([tool, llm], {"max_seconds": 5}).passed is False

    def test_single_span(self) -> None:
        spans = [_llm(None, start=0, dur=1)]
        assert check_max_duration(spans, {"max_seconds": 1}).passed is True

    def test_spans_out_of_list_order(self) -> None:
        """Spans passed in arbitrary list order — min/max should still work."""
        late = _llm(None, start=10, dur=1)
        early = _llm(None, start=0, dur=1)
        r = check_max_duration([late, early], {"max_seconds": 11})
        assert r.passed is True
        assert check_max_duration([late, early], {"max_seconds": 10}).passed is False

    def test_zero_duration(self) -> None:
        """Span with same start and end — zero duration."""
        span = Span(
            trace_id="t",
            span_id="s",
            name="instant",
            kind=SpanKind.LLM,
            start_time=T0,
            end_time=T0,
        )
        assert check_max_duration([span], {"max_seconds": 0}).passed is True

    def test_missing_param_defaults_to_inf(self) -> None:
        spans = [_llm(None, start=0, dur=9999)]
        assert check_max_duration(spans, {}).passed is True

    def test_detail_format(self) -> None:
        spans = [_llm(None, start=0, dur=3.7)]
        r = check_max_duration(spans, {"max_seconds": 2})
        assert "3.7s" in r.detail
        assert ">" in r.detail


class TestNoRepeatCalls:
    """Check: no_repeat_calls — same tool+args should not appear twice."""

    def test_no_duplicates(self) -> None:
        spans = [_tool("a", {"x": 1}, start=0), _tool("b", {"x": 1}, start=1)]
        assert check_no_repeat_calls(spans, {}).passed is True

    def test_with_duplicate(self) -> None:
        spans = [_tool("a", {"x": 1}, start=0), _tool("a", {"x": 1}, start=1)]
        assert check_no_repeat_calls(spans, {}).passed is False

    def test_empty_spans(self) -> None:
        assert check_no_repeat_calls([], {}).passed is True

    def test_same_name_different_args(self) -> None:
        """Same tool name but different args — not a duplicate."""
        spans = [_tool("search", {"q": "a"}, start=0), _tool("search", {"q": "b"}, start=1)]
        assert check_no_repeat_calls(spans, {}).passed is True

    def test_parallel_tools_duplicate_on_single_llm_span(self) -> None:
        """Two identical tool calls on same LLM span."""
        llm = _llm(
            None,
            tools=[
                ToolInfo(name="search", args={"q": "a"}),
                ToolInfo(name="search", args={"q": "a"}),
            ],
        )
        assert check_no_repeat_calls([llm], {}).passed is False

    def test_parallel_tools_no_duplicate(self) -> None:
        """Two different tool calls on same LLM span."""
        llm = _llm(
            None,
            tools=[
                ToolInfo(name="search", args={"q": "a"}),
                ToolInfo(name="search", args={"q": "b"}),
            ],
        )
        assert check_no_repeat_calls([llm], {}).passed is True

    def test_tool_and_llm_span_dedup(self) -> None:
        """Same call on TOOL span and LLM span — should NOT be flagged as duplicate."""
        tool = _tool("search", {"q": "a"}, start=0)
        llm = _llm(None, start=1, tools=[ToolInfo(name="search", args={"q": "a"})])
        assert check_no_repeat_calls([tool, llm], {}).passed is True

    def test_args_key_order_irrelevant(self) -> None:
        """JSON serialization sorts keys — different dict order should still match."""
        spans = [
            _tool("api", {"b": 2, "a": 1}, start=0),
            _tool("api", {"a": 1, "b": 2}, start=1),
        ]
        assert check_no_repeat_calls(spans, {}).passed is False

    def test_nested_args(self) -> None:
        """Nested dict args — same structure is duplicate."""
        args = {"filter": {"status": "active", "role": "admin"}}
        spans = [_tool("query", args, start=0), _tool("query", args, start=1)]
        assert check_no_repeat_calls(spans, {}).passed is False

    def test_nested_args_different(self) -> None:
        spans = [
            _tool("query", {"filter": {"status": "active"}}, start=0),
            _tool("query", {"filter": {"status": "inactive"}}, start=1),
        ]
        assert check_no_repeat_calls(spans, {}).passed is True

    def test_three_identical_calls(self) -> None:
        """Three identical calls — should report duplicates."""
        spans = [_tool("x", {"a": 1}, start=i) for i in range(3)]
        r = check_no_repeat_calls(spans, {})
        assert r.passed is False
        assert "Duplicates" in r.detail

    def test_tool_span_dup_plus_llm_span_dedup(self) -> None:
        """Complex: TOOL span called twice (dup), same call also on LLM span (dedup'd)."""
        t1 = _tool("search", {"q": "x"}, start=0)
        t2 = _tool("search", {"q": "x"}, start=1)
        llm = _llm(None, start=2, tools=[ToolInfo(name="search", args={"q": "x"})])
        r = check_no_repeat_calls([t1, t2, llm], {})
        assert r.passed is False

    def test_only_llm_spans_with_duplicate(self) -> None:
        """Duplicate calls across two LLM spans (no TOOL spans at all)."""
        llm1 = _llm(None, start=0, tools=[ToolInfo(name="fetch", args={"url": "/api"})])
        llm2 = _llm(None, start=2, tools=[ToolInfo(name="fetch", args={"url": "/api"})])
        assert check_no_repeat_calls([llm1, llm2], {}).passed is False

    def test_only_llm_spans_no_duplicate(self) -> None:
        llm1 = _llm(None, start=0, tools=[ToolInfo(name="fetch", args={"url": "/a"})])
        llm2 = _llm(None, start=2, tools=[ToolInfo(name="fetch", args={"url": "/b"})])
        assert check_no_repeat_calls([llm1, llm2], {}).passed is True

    def test_empty_args(self) -> None:
        """Tools with empty args — two calls with no args is a duplicate."""
        spans = [_tool("ping", {}, start=0), _tool("ping", {}, start=1)]
        assert check_no_repeat_calls(spans, {}).passed is False

    def test_detail_lists_names(self) -> None:
        spans = [_tool("search", {"q": "x"}, start=0), _tool("search", {"q": "x"}, start=1)]
        r = check_no_repeat_calls(spans, {})
        assert "search" in r.detail


class TestCheckRegistry:
    """Verify registry completeness and run_checks integration."""

    def test_all_check_types_registered(self) -> None:
        for check_type in CheckType:
            assert check_type in CHECK_REGISTRY, f"{check_type} not in registry"

    def test_registry_count(self) -> None:
        assert len(CHECK_REGISTRY) == 10

    def test_run_checks_multiple(self) -> None:
        response = json.dumps({"content": [{"type": "text", "text": "P2"}]})
        spans = [
            _llm(
                {"value": response},
                cost=CostInfo(prompt=0.001, completion=0.001, total=0.002),
                tools=[ToolInfo(name="classify", args={})],
            )
        ]
        checks = [
            {"type": "output_matches", "params": {"pattern": r"^P[123]$"}},
            {"type": "output_contains", "params": {"value": "P2"}},
            {"type": "tools_called", "params": {"tools": ["classify"]}},
            {"type": "tools_not_called", "params": {"tools": ["delete"]}},
            {
                "type": "trajectory",
                "params": {
                    "steps": [{"tool": "classify", "args": {}}],
                    "ordering": "exact",
                    "args": "exact",
                },
            },
            {"type": "max_cost", "params": {"max_usd": 0.01}},
            {"type": "max_turns", "params": {"max": 5}},
            {"type": "max_duration", "params": {"max_seconds": 10}},
            {"type": "no_repeat_calls", "params": {}},
        ]
        results = run_checks(spans, checks)
        assert len(results) == 9
        for r in results:
            assert r.passed is True, f"{r.check} failed: {r.detail}"

    def test_run_checks_empty_params(self) -> None:
        """Params can be omitted — should use defaults."""
        spans = [_llm(None)]
        results = run_checks(spans, [{"type": "max_turns"}])
        assert len(results) == 1
        assert results[0].passed is True

    def test_run_checks_all_failing(self) -> None:
        """Scenario where every check type fails."""
        spans = [
            _llm(
                {"messages": [{"role": "assistant", "content": "wrong"}]},
                cost=CostInfo(prompt=1, completion=1, total=2),
                dur=100,
            )
        ]
        checks = [
            {"type": "output_contains", "params": {"value": "correct"}},
            {"type": "output_matches", "params": {"pattern": r"^right$"}},
            {"type": "tools_called", "params": {"tools": ["expected_tool"]}},
            {"type": "max_cost", "params": {"max_usd": 0.01}},
            {"type": "max_turns", "params": {"max": 0}},
            {"type": "max_duration", "params": {"max_seconds": 1}},
        ]
        results = run_checks(spans, checks)
        assert all(not r.passed for r in results)
