"""Tests for kensa.utils: span extraction, counting, output helpers, CLI helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kensa.models import Span, SpanKind, ToolInfo
from kensa.utils import (
    count_tool_calls,
    extract_output_text,
    get_agent_output,
    get_tool_names,
    get_tool_names_ordered,
    latest_manifest,
    validate_run_id,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _llm_span(
    span_id: str = "s1",
    output: dict | None = None,
    end_time: datetime | None = None,
    tools: list[ToolInfo] | None = None,
) -> Span:
    return Span(
        trace_id="t1",
        span_id=span_id,
        name="ChatCompletion",
        kind=SpanKind.LLM,
        start_time=BASE_TIME,
        end_time=end_time or BASE_TIME + timedelta(seconds=1),
        output=output,
        tools=tools or [],
    )


def _tool_span(
    span_id: str = "s2",
    name: str = "search",
    tools: list[ToolInfo] | None = None,
) -> Span:
    return Span(
        trace_id="t1",
        span_id=span_id,
        name=name,
        kind=SpanKind.TOOL,
        start_time=BASE_TIME,
        end_time=BASE_TIME + timedelta(seconds=0.5),
        tools=tools or [],
    )


class TestExtractOutputText:
    def test_none_output(self) -> None:
        assert extract_output_text(_llm_span()) == ""

    def test_string_message(self) -> None:
        span = _llm_span(output={"messages": ["hello", "world"]})
        assert extract_output_text(span) == "hello\nworld"

    def test_dict_message_string_content(self) -> None:
        span = _llm_span(output={"messages": [{"role": "assistant", "content": "hi"}]})
        assert extract_output_text(span) == "hi"

    def test_dict_message_structured_content(self) -> None:
        span = _llm_span(
            output={
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "part1"},
                            {"type": "image", "url": "x"},
                            {"type": "text", "text": "part2"},
                        ],
                    }
                ]
            }
        )
        text = extract_output_text(span)
        assert "part1" in text
        assert "part2" in text
        assert "image" not in text

    def test_fallback_json_dump(self) -> None:
        span = _llm_span(output={"result": 42})
        assert extract_output_text(span) == '{"result": 42}'

    def test_mixed_message_types(self) -> None:
        span = _llm_span(output={"messages": ["raw", {"role": "assistant", "content": "dict"}]})
        assert extract_output_text(span) == "raw\ndict"

    def test_value_anthropic_response(self) -> None:
        import json

        response = json.dumps({"content": [{"type": "text", "text": "P1"}], "role": "assistant"})
        span = _llm_span(output={"value": response})
        assert extract_output_text(span) == "P1"

    def test_value_openai_response(self) -> None:
        import json

        response = json.dumps({"choices": [{"message": {"content": "POSITIVE"}}]})
        span = _llm_span(output={"value": response})
        assert extract_output_text(span) == "POSITIVE"

    def test_value_plain_string(self) -> None:
        span = _llm_span(output={"value": "just text"})
        assert extract_output_text(span) == "just text"

    def test_value_invalid_json(self) -> None:
        span = _llm_span(output={"value": "not {json"})
        assert extract_output_text(span) == "not {json"

    def test_non_text_content_blocks_no_spurious_newline(self) -> None:
        """Image-only content block followed by text message — no leading newline."""
        span = _llm_span(
            output={
                "messages": [
                    {"role": "assistant", "content": [{"type": "image", "url": "x"}]},
                    {"role": "assistant", "content": "hello"},
                ]
            }
        )
        assert extract_output_text(span) == "hello"


class TestCountToolCalls:
    def test_empty(self) -> None:
        assert count_tool_calls([]) == 0

    def test_tool_spans_only(self) -> None:
        spans = [_tool_span("s1"), _tool_span("s2")]
        assert count_tool_calls(spans) == 2

    def test_llm_with_tools_field(self) -> None:
        llm = _llm_span(tools=[ToolInfo(name="calculator", args={"expr": "1+1"})])
        assert count_tool_calls([llm]) == 1

    def test_mixed(self) -> None:
        tool = _tool_span()
        llm_with_tools = _llm_span(span_id="s3", tools=[ToolInfo(name="calc", args={})])
        llm_without = _llm_span(span_id="s4")
        assert count_tool_calls([tool, llm_with_tools, llm_without]) == 2

    def test_no_double_count_tool_span_with_tools_field(self) -> None:
        tool = _tool_span(tools=[ToolInfo(name="search", args={})])
        assert count_tool_calls([tool]) == 1

    def test_parallel_tool_calls_on_single_llm_span(self) -> None:
        llm = _llm_span(
            tools=[
                ToolInfo(name="search", args={"q": "weather"}),
                ToolInfo(name="calculate", args={"expr": "2+2"}),
            ]
        )
        assert count_tool_calls([llm]) == 2

    def test_parallel_tool_calls_mixed_with_tool_spans(self) -> None:
        """TOOL span for 'search' + LLM span with [search, calculate].
        Dedup: 'search' on LLM is a duplicate of the TOOL span → count = 2."""
        llm = _llm_span(
            tools=[
                ToolInfo(name="search", args={}),
                ToolInfo(name="calculate", args={}),
            ]
        )
        tool = _tool_span(span_id="s3")
        assert count_tool_calls([llm, tool]) == 2


class TestGetToolNames:
    def test_empty(self) -> None:
        assert get_tool_names([]) == []

    def test_tool_span_with_tools_field(self) -> None:
        span = _tool_span(tools=[ToolInfo(name="search", args={})])
        assert get_tool_names([span]) == ["search"]

    def test_tool_span_without_tools_field_uses_span_name(self) -> None:
        span = _tool_span(name="fallback_tool")
        assert get_tool_names([span]) == ["fallback_tool"]

    def test_llm_span_with_tools_field(self) -> None:
        llm = _llm_span(tools=[ToolInfo(name="calc", args={})])
        assert get_tool_names([llm]) == ["calc"]

    def test_llm_span_without_tools_excluded(self) -> None:
        assert get_tool_names([_llm_span()]) == []

    def test_order_preserved(self) -> None:
        spans = [
            _tool_span("s1", tools=[ToolInfo(name="alpha", args={})]),
            _tool_span("s2", tools=[ToolInfo(name="beta", args={})]),
            _llm_span(span_id="s3", tools=[ToolInfo(name="gamma", args={})]),
        ]
        assert get_tool_names(spans) == ["alpha", "beta", "gamma"]

    def test_parallel_tools_on_single_span(self) -> None:
        llm = _llm_span(
            tools=[
                ToolInfo(name="search", args={}),
                ToolInfo(name="calculate", args={}),
                ToolInfo(name="fetch", args={}),
            ]
        )
        assert get_tool_names([llm]) == ["search", "calculate", "fetch"]


class TestToolDeduplication:
    """When both framework (TOOL spans) and SDK (LLM span tools) instrumentors
    are active, the same tool call appears in both. Verify deduplication."""

    def test_count_dedup_same_tool(self) -> None:
        """TOOL span + LLM span both reference 'search' → count once."""
        tool = _tool_span(name="search", tools=[ToolInfo(name="search", args={})])
        llm = _llm_span(span_id="s3", tools=[ToolInfo(name="search", args={})])
        assert count_tool_calls([tool, llm]) == 1

    def test_count_dedup_different_tools(self) -> None:
        """TOOL span for 'search', LLM span for 'search' + 'calculate'.
        'calculate' has no TOOL span → counted."""
        tool = _tool_span(name="search", tools=[ToolInfo(name="search", args={})])
        llm = _llm_span(
            span_id="s3",
            tools=[
                ToolInfo(name="search", args={}),
                ToolInfo(name="calculate", args={}),
            ],
        )
        assert count_tool_calls([tool, llm]) == 2

    def test_names_dedup(self) -> None:
        """get_tool_names should deduplicate the same way."""
        tool = _tool_span(name="search", tools=[ToolInfo(name="search", args={})])
        llm = _llm_span(span_id="s3", tools=[ToolInfo(name="search", args={})])
        assert get_tool_names([tool, llm]) == ["search"]

    def test_no_dedup_without_tool_spans(self) -> None:
        """SDK-only traces (no TOOL spans): all tools on LLM spans are counted."""
        llm1 = _llm_span(tools=[ToolInfo(name="search", args={"q": "a"})])
        llm2 = _llm_span(span_id="s3", tools=[ToolInfo(name="search", args={"q": "b"})])
        assert count_tool_calls([llm1, llm2]) == 2
        assert get_tool_names([llm1, llm2]) == ["search", "search"]

    def test_multiple_calls_same_tool_dedup(self) -> None:
        """Two TOOL spans for 'search', two LLM refs for 'search' → count = 2."""
        t1 = _tool_span(span_id="t1", name="search", tools=[ToolInfo(name="search", args={})])
        t2 = _tool_span(span_id="t2", name="search", tools=[ToolInfo(name="search", args={})])
        llm = _llm_span(
            span_id="s3",
            tools=[
                ToolInfo(name="search", args={}),
                ToolInfo(name="search", args={}),
            ],
        )
        assert count_tool_calls([t1, t2, llm]) == 2


class TestGetToolNamesOrdered:
    def test_empty(self) -> None:
        assert get_tool_names_ordered([]) == []

    def test_sorted_by_start_time(self) -> None:
        """Spans passed out of order should still produce time-ordered names."""
        late = _llm_span(
            span_id="s2",
            tools=[ToolInfo(name="beta", args={})],
            end_time=BASE_TIME + timedelta(seconds=3),
        )
        # Manually set start_time for ordering
        late.start_time = BASE_TIME + timedelta(seconds=2)
        early = _llm_span(
            span_id="s1",
            tools=[ToolInfo(name="alpha", args={})],
        )
        # Pass in reverse order — function should still sort by start_time
        assert get_tool_names_ordered([late, early]) == ["alpha", "beta"]

    def test_tools_on_llm_spans(self) -> None:
        """Should find tools on LLM spans, not just TOOL spans."""
        span = _llm_span(
            tools=[
                ToolInfo(name="lookup", args={}),
                ToolInfo(name="search", args={}),
            ],
        )
        assert get_tool_names_ordered([span]) == ["lookup", "search"]

    def test_tool_span_without_tools_field_uses_name(self) -> None:
        span = _tool_span(name="fallback_tool")
        assert get_tool_names_ordered([span]) == ["fallback_tool"]


class TestGetAgentOutput:
    def test_empty(self) -> None:
        assert get_agent_output([]) == ""

    def test_returns_last_llm_by_end_time(self) -> None:
        early = _llm_span(
            span_id="s1",
            output={"messages": [{"role": "assistant", "content": "early"}]},
            end_time=BASE_TIME + timedelta(seconds=1),
        )
        late = _llm_span(
            span_id="s2",
            output={"messages": [{"role": "assistant", "content": "late"}]},
            end_time=BASE_TIME + timedelta(seconds=5),
        )
        assert get_agent_output([early, late]) == "late"

    def test_ignores_non_llm_spans(self) -> None:
        tool = _tool_span()
        llm = _llm_span(output={"messages": [{"role": "assistant", "content": "answer"}]})
        assert get_agent_output([tool, llm]) == "answer"


class TestValidateRunId:
    def test_valid_ids(self) -> None:
        assert validate_run_id("20260317T143000") == "20260317T143000"
        assert validate_run_id("my_run.1") == "my_run.1"
        assert validate_run_id("run-2") == "run-2"

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="Invalid run ID"):
            validate_run_id("../evil")

    def test_rejects_slashes(self) -> None:
        with pytest.raises(ValueError, match="Invalid run ID"):
            validate_run_id("foo/bar")


class TestLatestManifest:
    def test_no_runs_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError, match="No runs found"):
            latest_manifest()

    def test_empty_runs_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".kensa" / "runs").mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="No run manifests found"):
            latest_manifest()
