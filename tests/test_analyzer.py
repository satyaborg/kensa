"""Tests for trace analyzer: percentiles, tool usage, flagging."""

from __future__ import annotations

from pathlib import Path

import pytest

from kensa.analyzer import _percentiles, analyze_traces
from kensa.models import FlagType, Span


class TestPercentiles:
    def test_basic(self) -> None:
        values = list(range(1, 101))  # 1 to 100
        dist = _percentiles(values)
        assert dist.p50 == 50.5
        assert dist.p90 == pytest.approx(90.1)
        assert dist.max == 100

    def test_empty(self) -> None:
        dist = _percentiles([])
        assert dist.p50 == 0.0
        assert dist.max == 0.0

    def test_single(self) -> None:
        dist = _percentiles([42.0])
        assert dist.p50 == 42.0
        assert dist.max == 42.0


class TestAnalyzeTraces:
    def test_basic_analysis(self, multi_trace_spans: list[Span]) -> None:
        analysis = analyze_traces(spans=multi_trace_spans)
        assert analysis.trace_count == 5
        assert analysis.success_rate < 1.0  # trace_002 has error

    def test_error_flagging(self, multi_trace_spans: list[Span]) -> None:
        analysis = analyze_traces(spans=multi_trace_spans)
        error_flags = [f for f in analysis.flagged_traces if f.flag == FlagType.ERROR]
        assert len(error_flags) >= 1

    def test_cost_outlier_flagging(self, multi_trace_spans: list[Span]) -> None:
        analysis = analyze_traces(spans=multi_trace_spans)
        cost_flags = [f for f in analysis.flagged_traces if f.flag == FlagType.COST_OUTLIER]
        # trace_004 has 10x cost multiplier
        assert len(cost_flags) >= 1

    def test_tool_usage_stats(self, multi_trace_spans: list[Span]) -> None:
        analysis = analyze_traces(spans=multi_trace_spans)
        assert len(analysis.tool_usage) > 0
        search_usage = [t for t in analysis.tool_usage if t.tool == "search"]
        assert len(search_usage) == 1
        assert search_usage[0].call_count == 5

    def test_empty_analysis(self) -> None:
        analysis = analyze_traces(spans=[])
        assert analysis.trace_count == 0
        assert analysis.success_rate == 0.0

    def test_cost_distribution(self, multi_trace_spans: list[Span]) -> None:
        analysis = analyze_traces(spans=multi_trace_spans)
        assert analysis.cost_distribution.max > 0

    def test_latency_distribution(self, multi_trace_spans: list[Span]) -> None:
        analysis = analyze_traces(spans=multi_trace_spans)
        assert analysis.latency_distribution.max > 0

    def test_analyze_from_trace_dir(self, tmp_path: Path, sample_spans: list[Span]) -> None:
        """Test loading traces from a directory."""
        trace_file = tmp_path / "test.jsonl"
        with open(trace_file, "w") as f:
            for span in sample_spans:
                f.write(span.model_dump_json() + "\n")
        analysis = analyze_traces(trace_dir=str(tmp_path))
        assert analysis.trace_count == 1  # both spans share trace_001

    def test_analyze_nonexistent_dir(self) -> None:
        analysis = analyze_traces(trace_dir="/nonexistent/path")
        assert analysis.trace_count == 0

    def test_high_turn_count_flagging(self) -> None:
        """Trace with >20 LLM calls should be flagged."""
        from datetime import datetime, timedelta, timezone

        from kensa.models import FlagType, SpanKind

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        spans = [
            Span(
                trace_id="high_turn",
                span_id=f"s{i}",
                name="ChatCompletion",
                kind=SpanKind.LLM,
                start_time=base + timedelta(seconds=i),
                end_time=base + timedelta(seconds=i + 0.5),
            )
            for i in range(25)
        ]
        analysis = analyze_traces(spans=spans)
        flags = [f for f in analysis.flagged_traces if f.flag == FlagType.HIGH_TURN_COUNT]
        assert len(flags) == 1

    def test_repeated_tool_call_flagging(self) -> None:
        """Two calls to the same tool in sequence should be flagged."""
        from datetime import datetime, timedelta, timezone

        from kensa.models import FlagType, SpanKind, ToolInfo

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        spans = [
            Span(
                trace_id="repeated",
                span_id="s1",
                name="search",
                kind=SpanKind.TOOL,
                start_time=base,
                end_time=base + timedelta(seconds=0.5),
                tools=[ToolInfo(name="search", args={"q": "a"})],
            ),
            Span(
                trace_id="repeated",
                span_id="s2",
                name="search",
                kind=SpanKind.TOOL,
                start_time=base + timedelta(seconds=1),
                end_time=base + timedelta(seconds=1.5),
                tools=[ToolInfo(name="search", args={"q": "a"})],
            ),
        ]
        analysis = analyze_traces(spans=spans)
        flags = [f for f in analysis.flagged_traces if f.flag == FlagType.REPEATED_TOOL_CALL]
        assert len(flags) == 1

    def test_latency_outlier_flagging(self) -> None:
        """A trace with >3x median duration should be flagged."""
        from datetime import datetime, timedelta, timezone

        from kensa.models import FlagType, SpanKind

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        spans = []
        # 4 normal traces (1s each) + 1 slow trace (10s)
        for i in range(5):
            duration = 10.0 if i == 4 else 1.0
            spans.append(
                Span(
                    trace_id=f"t{i}",
                    span_id=f"s{i}",
                    name="test",
                    kind=SpanKind.LLM,
                    start_time=base,
                    end_time=base + timedelta(seconds=duration),
                )
            )
        analysis = analyze_traces(spans=spans)
        flags = [f for f in analysis.flagged_traces if f.flag == FlagType.LATENCY_OUTLIER]
        assert len(flags) >= 1

    def test_tool_error_rate(self) -> None:
        """Tool with errors should have non-zero error rate."""
        from datetime import datetime, timedelta, timezone

        from kensa.models import SpanKind, ToolInfo

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Analyzer groups tools by s.name (span name), so use same name
        spans = [
            Span(
                trace_id="t1",
                span_id="s1",
                name="api_call",
                kind=SpanKind.TOOL,
                start_time=base,
                end_time=base + timedelta(seconds=0.5),
                status="ok",
                tools=[ToolInfo(name="api_call")],
            ),
            Span(
                trace_id="t1",
                span_id="s2",
                name="api_call",
                kind=SpanKind.TOOL,
                start_time=base + timedelta(seconds=1),
                end_time=base + timedelta(seconds=1.5),
                status="error",
                tools=[ToolInfo(name="api_call")],
            ),
        ]
        analysis = analyze_traces(spans=spans)
        tool = [t for t in analysis.tool_usage if t.tool == "api_call"]
        assert len(tool) == 1
        assert tool[0].error_rate > 0

    def test_same_tool_different_args_not_flagged(self) -> None:
        """Same tool name with different args should NOT be flagged as repeated."""
        from datetime import datetime, timedelta, timezone

        from kensa.models import FlagType, SpanKind, ToolInfo

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        spans = [
            Span(
                trace_id="t1",
                span_id="s1",
                name="search",
                kind=SpanKind.TOOL,
                start_time=base,
                end_time=base + timedelta(seconds=0.5),
                tools=[ToolInfo(name="search", args={"q": "weather"})],
            ),
            Span(
                trace_id="t1",
                span_id="s2",
                name="search",
                kind=SpanKind.TOOL,
                start_time=base + timedelta(seconds=1),
                end_time=base + timedelta(seconds=1.5),
                tools=[ToolInfo(name="search", args={"q": "stocks"})],
            ),
        ]
        analysis = analyze_traces(spans=spans)
        flags = [f for f in analysis.flagged_traces if f.flag == FlagType.REPEATED_TOOL_CALL]
        assert len(flags) == 0

    def test_same_tool_same_args_is_flagged(self) -> None:
        """Same tool name with identical args should be flagged."""
        from datetime import datetime, timedelta, timezone

        from kensa.models import FlagType, SpanKind, ToolInfo

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        spans = [
            Span(
                trace_id="t1",
                span_id="s1",
                name="search",
                kind=SpanKind.TOOL,
                start_time=base,
                end_time=base + timedelta(seconds=0.5),
                tools=[ToolInfo(name="search", args={"q": "same"})],
            ),
            Span(
                trace_id="t1",
                span_id="s2",
                name="search",
                kind=SpanKind.TOOL,
                start_time=base + timedelta(seconds=1),
                end_time=base + timedelta(seconds=1.5),
                tools=[ToolInfo(name="search", args={"q": "same"})],
            ),
        ]
        analysis = analyze_traces(spans=spans)
        flags = [f for f in analysis.flagged_traces if f.flag == FlagType.REPEATED_TOOL_CALL]
        assert len(flags) == 1

    def test_dual_instrumentor_does_not_double_flag(self) -> None:
        """A single tool call observed by two instrumentors must not be flagged."""
        from datetime import datetime, timedelta, timezone

        from kensa.models import FlagType, SpanKind, ToolInfo

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        spans = [
            # LLM span with embedded tool call (e.g. OpenAI instrumentor)
            Span(
                trace_id="t1",
                span_id="llm1",
                name="chat",
                kind=SpanKind.LLM,
                start_time=base,
                end_time=base + timedelta(seconds=0.3),
                tools=[ToolInfo(name="search", args={"q": "weather"})],
            ),
            # TOOL span for the same call (e.g. LangChain instrumentor)
            Span(
                trace_id="t1",
                span_id="tool1",
                name="search",
                kind=SpanKind.TOOL,
                start_time=base + timedelta(seconds=0.4),
                end_time=base + timedelta(seconds=0.6),
                tools=[ToolInfo(name="search", args={"q": "weather"})],
            ),
        ]
        analysis = analyze_traces(spans=spans)
        flags = [f for f in analysis.flagged_traces if f.flag == FlagType.REPEATED_TOOL_CALL]
        assert len(flags) == 0

    def test_tool_span_with_only_name_is_considered(self) -> None:
        """TOOL spans without populated `.tools` should fall back to span.name."""
        from datetime import datetime, timedelta, timezone

        from kensa.models import FlagType, SpanKind

        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        spans = [
            Span(
                trace_id="t1",
                span_id="s1",
                name="lookup",
                kind=SpanKind.TOOL,
                start_time=base,
                end_time=base + timedelta(seconds=0.5),
            ),
            Span(
                trace_id="t1",
                span_id="s2",
                name="lookup",
                kind=SpanKind.TOOL,
                start_time=base + timedelta(seconds=1),
                end_time=base + timedelta(seconds=1.5),
            ),
        ]
        analysis = analyze_traces(spans=spans)
        flags = [f for f in analysis.flagged_traces if f.flag == FlagType.REPEATED_TOOL_CALL]
        assert len(flags) == 1
