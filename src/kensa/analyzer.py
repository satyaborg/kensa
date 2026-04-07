"""Trace analysis: percentiles, tool frequencies, flagging heuristics."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from math import ceil, floor
from pathlib import Path

from kensa.models import (
    Analysis,
    Distribution,
    FlaggedTrace,
    FlagType,
    Span,
    SpanKind,
    ToolUsage,
)
from kensa.runner import read_trace
from kensa.trace_semantics import repeated_tool_names
from kensa.utils import get_tool_names


def _percentiles(values: Sequence[int | float]) -> Distribution:
    """Compute p50, p90, p99, max from a list of values."""
    if not values:
        return Distribution()
    sorted_vals = sorted(values)

    def _percentile(p: float) -> float:
        if len(sorted_vals) == 1:
            return float(sorted_vals[0])
        position = (len(sorted_vals) - 1) * p
        lower = floor(position)
        upper = ceil(position)
        if lower == upper:
            return float(sorted_vals[lower])
        weight = position - lower
        lower_value = float(sorted_vals[lower])
        upper_value = float(sorted_vals[upper])
        return lower_value + (upper_value - lower_value) * weight

    return Distribution(
        p50=_percentile(0.50),
        p90=_percentile(0.90),
        p99=_percentile(0.99),
        max=float(sorted_vals[-1]),
    )


def _group_by_trace(spans: list[Span]) -> dict[str, list[Span]]:
    """Group spans by trace_id."""
    traces: dict[str, list[Span]] = defaultdict(list)
    for span in spans:
        traces[span.trace_id].append(span)
    return dict(traces)


def analyze_traces(
    trace_dir: str = ".kensa/traces",
    spans: list[Span] | None = None,
) -> Analysis:
    """Compute statistics and flag outlier traces.

    Either provide spans directly or a trace_dir to read from.
    """
    if spans is None:
        spans = []
        trace_path = Path(trace_dir)
        try:
            contents = list(trace_path.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            contents = []
        for f in sorted(p for p in contents if p.suffix == ".jsonl"):
            spans.extend(read_trace(str(f)))

    if not spans:
        return Analysis()

    traces = _group_by_trace(spans)
    trace_count = len(traces)

    trace_costs: list[float] = []
    trace_durations: list[float] = []
    error_traces = 0
    tool_call_counter: Counter[str] = Counter()
    tool_latencies: defaultdict[str, list[float]] = defaultdict(list)
    tool_errors: defaultdict[str, int] = defaultdict(int)
    tool_counts: defaultdict[str, int] = defaultdict(int)

    flagged: list[FlaggedTrace] = []

    for trace_id, trace_spans in traces.items():
        cost = sum(s.cost.total for s in trace_spans if s.cost)
        trace_costs.append(cost)

        if trace_spans:
            earliest = min(s.start_time for s in trace_spans)
            latest = max(s.end_time for s in trace_spans)
            duration = (latest - earliest).total_seconds()
            trace_durations.append(duration)
        else:
            trace_durations.append(0.0)

        has_error = any(s.status == "error" for s in trace_spans)
        if has_error:
            error_traces += 1
            flagged.append(
                FlaggedTrace(
                    trace_id=trace_id,
                    flag=FlagType.ERROR,
                    detail="Trace contains error spans",
                )
            )

        # Latency and error tracking from TOOL spans (only source of per-tool timing).
        for s in trace_spans:
            if s.kind == SpanKind.TOOL:
                tool_name = s.tools[0].name if s.tools else s.name
                latency_ms = (s.end_time - s.start_time).total_seconds() * 1000
                tool_latencies[tool_name].append(latency_ms)
                if s.status == "error":
                    tool_errors[tool_name] += 1

        # Deduplicated tool counts (handles dual-instrumentor traces).
        for name in get_tool_names(trace_spans):
            tool_call_counter[name] += 1
            tool_counts[name] += 1

        llm_count = sum(1 for s in trace_spans if s.kind == SpanKind.LLM)
        if llm_count > 20:
            flagged.append(
                FlaggedTrace(
                    trace_id=trace_id,
                    flag=FlagType.HIGH_TURN_COUNT,
                    detail=f"{llm_count} LLM calls",
                )
            )

        duplicates = repeated_tool_names(trace_spans, ordered=True)
        if duplicates:
            flagged.append(
                FlaggedTrace(
                    trace_id=trace_id,
                    flag=FlagType.REPEATED_TOOL_CALL,
                    detail=f"Repeated call to {duplicates[0]}",
                )
            )

    if trace_costs:
        median_cost = statistics.median(trace_costs)
        if median_cost > 0:
            for trace_id, cost in zip(traces.keys(), trace_costs, strict=True):
                ratio = cost / median_cost
                if ratio > 3.0:
                    flagged.append(
                        FlaggedTrace(
                            trace_id=trace_id,
                            flag=FlagType.COST_OUTLIER,
                            detail=f"cost ${cost:.4f}, {ratio:.1f}x median",
                        )
                    )

    if trace_durations:
        median_duration = statistics.median(trace_durations)
        if median_duration > 0:
            for trace_id, duration in zip(traces.keys(), trace_durations, strict=True):
                ratio = duration / median_duration
                if ratio > 3.0:
                    flagged.append(
                        FlaggedTrace(
                            trace_id=trace_id,
                            flag=FlagType.LATENCY_OUTLIER,
                            detail=f"{duration:.1f}s, {ratio:.1f}x median",
                        )
                    )

    tool_usage: list[ToolUsage] = []
    for tool_name in sorted(tool_call_counter.keys()):
        count = tool_counts[tool_name]
        has_latency = bool(tool_latencies[tool_name])
        avg_latency = statistics.mean(tool_latencies[tool_name]) if has_latency else 0.0
        error_rate = tool_errors[tool_name] / count if count > 0 else 0.0
        tool_usage.append(
            ToolUsage(
                tool=tool_name,
                call_count=count,
                avg_latency_ms=round(avg_latency, 2),
                error_rate=round(error_rate, 4),
                metrics_available=has_latency,
            )
        )

    success_rate = 1.0 - (error_traces / trace_count) if trace_count > 0 else 0.0

    return Analysis(
        trace_count=trace_count,
        success_rate=round(success_rate, 4),
        cost_distribution=_percentiles(trace_costs),
        latency_distribution=_percentiles(trace_durations),
        tool_usage=tool_usage,
        flagged_traces=flagged,
    )
