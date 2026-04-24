"""Cross-layer tests for translation and canonical tool-call semantics."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kensa.analyzer import analyze_traces
from kensa.checks import check_no_repeat_calls
from kensa.models import FlagType
from kensa.runner import read_spans, read_trace, write_trace
from kensa.trace_semantics import collect_tool_calls, repeated_tool_names
from kensa.translate import oi_to_kensa
from kensa.utils import count_tool_calls, get_tool_names, get_tool_names_ordered

BASE = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)


def _oi_tool(
    span_id: str,
    name: str,
    *,
    args: dict[str, object] | None = None,
    start: datetime | None = None,
) -> dict[str, object]:
    started = start or BASE
    return {
        "trace_id": "trace_semantics",
        "span_id": span_id,
        "name": name,
        "start_time": started.isoformat(),
        "end_time": (started + timedelta(milliseconds=300)).isoformat(),
        "status": {"status_code": "OK"},
        "attributes": {
            "openinference.span.kind": "TOOL",
            "tool.name": name,
            "tool.parameters": args or {},
        },
    }


def _oi_llm(
    span_id: str,
    *,
    output_tools: list[tuple[str, dict[str, object]]] | None = None,
    input_tools: list[tuple[str, dict[str, object]]] | None = None,
    start: datetime | None = None,
) -> dict[str, object]:
    started = start or BASE
    attrs: dict[str, object] = {
        "openinference.span.kind": "LLM",
        "llm.model_name": "test-model",
    }
    for idx, (name, args) in enumerate(output_tools or []):
        prefix = f"llm.output_messages.0.message.tool_calls.{idx}.tool_call.function"
        attrs[f"{prefix}.name"] = name
        attrs[f"{prefix}.arguments"] = json.dumps(args)
    for idx, (name, args) in enumerate(input_tools or []):
        prefix = f"llm.input_messages.1.message.tool_calls.{idx}.tool_call.function"
        attrs[f"{prefix}.name"] = name
        attrs[f"{prefix}.arguments"] = json.dumps(args)
    return {
        "trace_id": "trace_semantics",
        "span_id": span_id,
        "name": "ChatCompletion",
        "start_time": started.isoformat(),
        "end_time": (started + timedelta(milliseconds=200)).isoformat(),
        "status": {"status_code": "OK"},
        "attributes": attrs,
    }


class TestTraceSemantics:
    def test_layers_agree_for_mixed_dual_instrumentor_trace(self) -> None:
        spans = [
            oi_to_kensa(
                _oi_llm(
                    "llm1",
                    output_tools=[("search", {"q": "weather"})],
                    start=BASE,
                )
            ),
            oi_to_kensa(
                _oi_tool(
                    "tool1",
                    "search",
                    args={"q": "weather"},
                    start=BASE + timedelta(milliseconds=250),
                )
            ),
            oi_to_kensa(
                _oi_llm(
                    "llm2",
                    output_tools=[("calculate", {"expr": "2+2"})],
                    start=BASE + timedelta(seconds=1),
                )
            ),
        ]

        canonical = collect_tool_calls(spans, ordered=True)
        assert [tool.name for tool in canonical] == ["search", "calculate"]
        assert count_tool_calls(spans) == 2
        assert get_tool_names(spans) == ["search", "calculate"]
        assert get_tool_names_ordered(spans) == ["search", "calculate"]
        assert repeated_tool_names(spans, ordered=True) == []
        assert check_no_repeat_calls(spans, {}).passed is True

        analysis = analyze_traces(spans=spans)
        repeat_flags = [f for f in analysis.flagged_traces if f.flag == FlagType.REPEATED_TOOL_CALL]
        assert repeat_flags == []

    def test_input_history_is_ignored_across_layers(self) -> None:
        spans = [
            oi_to_kensa(
                _oi_llm(
                    "llm1",
                    input_tools=[("lookup", {"id": "C-1001"})],
                    output_tools=[("refund", {"order_id": "ORD-4001"})],
                )
            )
        ]

        assert [tool.name for tool in spans[0].tools] == ["refund"]
        assert [tool.name for tool in collect_tool_calls(spans)] == ["refund"]
        assert count_tool_calls(spans) == 1
        assert repeated_tool_names(spans) == []
        assert check_no_repeat_calls(spans, {}).passed is True

    def test_true_repeat_is_detected_once_everywhere(self) -> None:
        spans = [
            oi_to_kensa(_oi_tool("tool1", "search", args={"q": "same"}, start=BASE)),
            oi_to_kensa(
                _oi_tool(
                    "tool2",
                    "search",
                    args={"q": "same"},
                    start=BASE + timedelta(seconds=1),
                )
            ),
        ]

        assert repeated_tool_names(spans, ordered=True) == ["search"]
        assert check_no_repeat_calls(spans, {}).passed is False

        analysis = analyze_traces(spans=spans)
        repeat_flags = [f for f in analysis.flagged_traces if f.flag == FlagType.REPEATED_TOOL_CALL]
        assert len(repeat_flags) == 1
        assert repeat_flags[0].detail == "Repeated call to search"

    def test_runner_persistence_preserves_canonical_tool_semantics(self, tmp_path: Path) -> None:
        spans_dir = tmp_path / "raw"
        spans_dir.mkdir()
        (spans_dir / "spans.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        _oi_llm(
                            "llm1",
                            input_tools=[("lookup", {"id": "C-1001"})],
                            output_tools=[("refund", {"order_id": "ORD-4001"})],
                            start=BASE,
                        )
                    ),
                    json.dumps(
                        _oi_tool(
                            "tool1",
                            "refund",
                            args={"order_id": "ORD-4001"},
                            start=BASE + timedelta(milliseconds=300),
                        )
                    ),
                ]
            )
            + "\n"
        )

        translated = read_spans(spans_dir)
        assert [tool.name for tool in collect_tool_calls(translated, ordered=True)] == ["refund"]

        persisted = tmp_path / "persisted" / "trace.jsonl"
        write_trace(translated, persisted)
        reloaded = read_trace(str(persisted))

        assert [tool.name for tool in collect_tool_calls(reloaded, ordered=True)] == ["refund"]
        assert count_tool_calls(reloaded) == 1
        assert repeated_tool_names(reloaded, ordered=True) == []

    def test_fallback_tool_name_without_tools_field_is_canonicalized(self) -> None:
        span = oi_to_kensa(
            {
                "trace_id": "trace_semantics",
                "span_id": "tool-fallback",
                "name": "lookup",
                "start_time": BASE.isoformat(),
                "end_time": (BASE + timedelta(milliseconds=300)).isoformat(),
                "status": {"status_code": "OK"},
                "attributes": {"openinference.span.kind": "TOOL"},
            }
        )
        assert [tool.name for tool in collect_tool_calls([span])] == ["lookup"]
