"""Canonical tool-call semantics across traces."""

from __future__ import annotations

import json

from kensa.models import Span, SpanKind, ToolInfo


def tool_call_key(tool: ToolInfo) -> str:
    """Stable identity for a tool call based on name and args."""
    return f"{tool.name}:{json.dumps(tool.args, sort_keys=True)}"


def span_tool_calls(span: Span) -> list[ToolInfo]:
    """Return the tool calls represented by a single span.

    TOOL spans may omit ``tools``; in that case the span name is the tool name.
    """
    if span.kind == SpanKind.TOOL:
        return span.tools if span.tools else [ToolInfo(name=span.name, args={})]
    return span.tools


def collect_tool_calls(spans: list[Span], *, ordered: bool = False) -> list[ToolInfo]:
    """Collect deduplicated tool calls across a trace.

    TOOL spans are authoritative. LLM-embedded tool calls count only when no
    matching TOOL span covers the same name+args pair.
    """
    trace_spans = sorted(spans, key=lambda s: s.start_time) if ordered else spans

    covered_by_tool_spans: dict[str, int] = {}
    for span in trace_spans:
        if span.kind != SpanKind.TOOL:
            continue
        for tool in span_tool_calls(span):
            key = tool_call_key(tool)
            covered_by_tool_spans[key] = covered_by_tool_spans.get(key, 0) + 1

    tool_calls: list[ToolInfo] = []
    for span in trace_spans:
        if span.kind == SpanKind.TOOL:
            tool_calls.extend(span_tool_calls(span))
            continue
        for tool in span.tools:
            key = tool_call_key(tool)
            if covered_by_tool_spans.get(key, 0) > 0:
                covered_by_tool_spans[key] -= 1
            else:
                tool_calls.append(tool)

    return tool_calls


def repeated_tool_names(spans: list[Span], *, ordered: bool = False) -> list[str]:
    """Return duplicate tool-call names after canonical deduplication."""
    duplicates: list[str] = []
    seen: set[str] = set()
    for tool in collect_tool_calls(spans, ordered=ordered):
        key = tool_call_key(tool)
        if key in seen:
            duplicates.append(tool.name)
        else:
            seen.add(key)
    return duplicates
