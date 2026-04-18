"""Tests for OpenInference ↔ kensa span translation."""

from __future__ import annotations

from unittest.mock import patch
from urllib.error import URLError

import pytest

from kensa.models import Span, SpanKind, TokenCounts, ToolInfo
from kensa.pricing import candidate_slugs
from kensa.translate import (
    _compute_cost,
    _fetch_openrouter_prices,
    kensa_to_oi,
    oi_to_kensa,
)


class TestOiToKensa:
    def test_basic_conversion(self, sample_oi_span: dict) -> None:
        span = oi_to_kensa(sample_oi_span)
        assert span.trace_id == "abc123"
        assert span.span_id == "span_001"
        assert span.kind == SpanKind.LLM
        assert span.model == "claude-sonnet-4-6"
        assert span.provider == "anthropic"

    def test_token_mapping(self, sample_oi_span: dict) -> None:
        span = oi_to_kensa(sample_oi_span)
        assert span.tokens is not None
        assert span.tokens.prompt == 20
        assert span.tokens.completion == 15
        assert span.tokens.total == 35

    def test_input_output_mapping(self, sample_oi_span: dict) -> None:
        span = oi_to_kensa(sample_oi_span)
        assert span.input is not None
        assert "messages" in span.input
        assert span.output is not None
        assert "messages" in span.output

    def test_tool_span(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "search",
            "start_time": "2026-03-17T14:30:00+00:00",
            "end_time": "2026-03-17T14:30:01+00:00",
            "status": {"status_code": "OK"},
            "attributes": {
                "openinference.span.kind": "TOOL",
                "tool.name": "web_search",
                "tool.parameters": {"query": "weather"},
            },
        }
        span = oi_to_kensa(oi)
        assert span.kind == SpanKind.TOOL
        assert len(span.tools) == 1
        assert span.tools[0].name == "web_search"
        assert span.tools[0].args == {"query": "weather"}

    def test_tool_parameters_as_json_string(self) -> None:
        """OTel only allows primitive attribute types, so tool.parameters may arrive as a string."""
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "search",
            "start_time": "2026-03-17T14:30:00+00:00",
            "end_time": "2026-03-17T14:30:01+00:00",
            "status": {"status_code": "OK"},
            "attributes": {
                "openinference.span.kind": "TOOL",
                "tool.name": "web_search",
                "tool.parameters": '{"query": "weather"}',
            },
        }
        span = oi_to_kensa(oi)
        assert span.tools[0].name == "web_search"
        assert span.tools[0].args == {"query": "weather"}

    def test_tool_parameters_as_invalid_string(self) -> None:
        """Non-JSON string in tool.parameters should be wrapped, not crash."""
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "search",
            "start_time": "2026-03-17T14:30:00+00:00",
            "end_time": "2026-03-17T14:30:01+00:00",
            "status": {"status_code": "OK"},
            "attributes": {
                "openinference.span.kind": "TOOL",
                "tool.name": "web_search",
                "tool.parameters": "not json at all",
            },
        }
        span = oi_to_kensa(oi)
        assert span.tools[0].args == {"raw": "not json at all"}

    def test_parallel_tool_calls_from_flattened_oi_attrs(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "ChatCompletion",
            "start_time": "2026-03-17T14:30:00+00:00",
            "end_time": "2026-03-17T14:30:01+00:00",
            "status": {"status_code": "OK"},
            "attributes": {
                "openinference.span.kind": "LLM",
                "llm.output_messages.0.message.tool_calls.0.tool_call.function.name": "search",
                "llm.output_messages.0.message.tool_calls.0"
                ".tool_call.function.arguments": '{"q": "weather"}',
                "llm.output_messages.0.message.tool_calls.1.tool_call.function.name": "calculate",
                "llm.output_messages.0.message.tool_calls.1"
                ".tool_call.function.arguments": '{"expr": "2+2"}',
            },
        }
        span = oi_to_kensa(oi)
        assert len(span.tools) == 2
        names = {t.name for t in span.tools}
        assert names == {"search", "calculate"}

    def test_parallel_tool_calls_from_llm_tool_calls_attrs(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "ChatCompletion",
            "start_time": "2026-03-17T14:30:00+00:00",
            "end_time": "2026-03-17T14:30:01+00:00",
            "status": {"status_code": "OK"},
            "attributes": {
                "openinference.span.kind": "LLM",
                "llm.tool_calls.0.tool_call.function.name": "search",
                "llm.tool_calls.0.tool_call.function.arguments": '{"q": "weather"}',
                "llm.tool_calls.1.tool_call.function.name": "calculate",
                "llm.tool_calls.1.tool_call.function.arguments": '{"expr": "2+2"}',
            },
        }
        span = oi_to_kensa(oi)
        assert [t.name for t in span.tools] == ["search", "calculate"]

    def test_invalid_flattened_tool_args_are_wrapped(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "ChatCompletion",
            "start_time": "2026-03-17T14:30:00+00:00",
            "end_time": "2026-03-17T14:30:01+00:00",
            "status": {"status_code": "OK"},
            "attributes": {
                "openinference.span.kind": "LLM",
                "llm.output_messages.0.message.tool_calls.0.tool_call.function.name": "lookup",
                "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments": "oops",
            },
        }
        span = oi_to_kensa(oi)
        assert span.tools[0].args == {"raw": "oops"}

    def test_input_message_history_tool_calls_are_not_recounted(self) -> None:
        """Regression: prior-turn tool calls flattened into llm.input_messages.*
        must not be recorded as new tool calls. Otherwise no_repeat_calls fires
        on every multi-turn agent because each call is recounted in every later
        LLM span that sees it in history."""
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "ChatCompletion",
            "start_time": "2026-03-17T14:30:00+00:00",
            "end_time": "2026-03-17T14:30:01+00:00",
            "status": {"status_code": "OK"},
            "attributes": {
                "openinference.span.kind": "LLM",
                # Prior turn echoed back as input history — must be ignored.
                "llm.input_messages.2.message.tool_calls.0.tool_call.function.name": "lookup",
                "llm.input_messages.2.message.tool_calls.0"
                ".tool_call.function.arguments": '{"id": "C-1001"}',
                # The new output tool call — this is the only one we should count.
                "llm.output_messages.0.message.tool_calls.0.tool_call.function.name": "refund",
                "llm.output_messages.0.message.tool_calls.0"
                ".tool_call.function.arguments": '{"order_id": "ORD-4001"}',
            },
        }
        span = oi_to_kensa(oi)
        assert [t.name for t in span.tools] == ["refund"]

    def test_terminal_llm_span_with_only_input_history_records_no_tools(self) -> None:
        """A final-turn LLM span that emits no new tool_calls (finish_reason=stop)
        but still has prior tool_calls in its input history must record zero tools."""
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "ChatCompletion",
            "start_time": "2026-03-17T14:30:00+00:00",
            "end_time": "2026-03-17T14:30:01+00:00",
            "status": {"status_code": "OK"},
            "attributes": {
                "openinference.span.kind": "LLM",
                "llm.input_messages.1.message.tool_calls.0.tool_call.function.name": "lookup",
                "llm.input_messages.1.message.tool_calls.0"
                ".tool_call.function.arguments": '{"id": "C-1001"}',
            },
        }
        span = oi_to_kensa(oi)
        assert span.tools == []

    def test_error_status(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": "2026-03-17T14:30:00+00:00",
            "end_time": "2026-03-17T14:30:01+00:00",
            "status": {"status_code": "ERROR"},
            "attributes": {"openinference.span.kind": "LLM"},
        }
        span = oi_to_kensa(oi)
        assert span.status == "error"

    def test_all_span_kinds(self) -> None:
        for oi_kind, expected in [
            ("LLM", SpanKind.LLM),
            ("TOOL", SpanKind.TOOL),
            ("AGENT", SpanKind.AGENT),
            ("CHAIN", SpanKind.CHAIN),
            ("RETRIEVER", SpanKind.RETRIEVER),
            ("EVALUATOR", SpanKind.EVALUATOR),
        ]:
            oi = {
                "trace_id": "t1",
                "span_id": "s1",
                "name": "test",
                "start_time": "2026-03-17T14:30:00+00:00",
                "end_time": "2026-03-17T14:30:01+00:00",
                "attributes": {"openinference.span.kind": oi_kind},
            }
            span = oi_to_kensa(oi)
            assert span.kind == expected

    def test_nanosecond_timestamps(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": 1742223000000000000,  # nanoseconds
            "end_time": 1742223001000000000,
            "attributes": {"openinference.span.kind": "LLM"},
        }
        span = oi_to_kensa(oi)
        assert span.start_time.year == 2025

    def test_cost_mapping(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": "2026-03-17T14:30:00+00:00",
            "end_time": "2026-03-17T14:30:01+00:00",
            "attributes": {
                "openinference.span.kind": "LLM",
                "llm.cost.prompt": 0.001,
                "llm.cost.completion": 0.002,
                "llm.cost.total": 0.003,
            },
        }
        span = oi_to_kensa(oi)
        assert span.cost is not None
        assert span.cost.total == 0.003


class TestKensaToOi:
    def test_round_trip(self, sample_oi_span: dict) -> None:
        span = oi_to_kensa(sample_oi_span)
        oi_dict = kensa_to_oi(span)
        span2 = oi_to_kensa(oi_dict)

        assert span2.trace_id == span.trace_id
        assert span2.kind == span.kind
        assert span2.model == span.model
        assert span2.tokens is not None
        assert span2.tokens.total == span.tokens.total  # type: ignore

    def test_preserves_kind(self, sample_llm_span: Span) -> None:
        oi = kensa_to_oi(sample_llm_span)
        assert oi["attributes"]["openinference.span.kind"] == "LLM"

    def test_preserves_tokens(self, sample_llm_span: Span) -> None:
        oi = kensa_to_oi(sample_llm_span)
        assert oi["attributes"]["llm.token_count.prompt"] == 20
        assert oi["attributes"]["llm.token_count.total"] == 35

    def test_preserves_tool_info(self, sample_tool_span: Span) -> None:
        oi = kensa_to_oi(sample_tool_span)
        assert oi["attributes"]["tool.name"] == "get_weather"

    def test_preserves_cost(self, sample_llm_span: Span) -> None:
        oi = kensa_to_oi(sample_llm_span)
        assert oi["attributes"]["llm.cost.total"] == 0.003

    def test_preserves_status_error(self) -> None:
        from datetime import datetime, timezone

        span = Span(
            trace_id="t1",
            span_id="s1",
            name="test",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            status="error",
        )
        oi = kensa_to_oi(span)
        assert oi["status"]["status_code"] == "ERROR"

    def test_input_value_non_messages(self) -> None:
        from datetime import datetime, timezone

        span = Span(
            trace_id="t1",
            span_id="s1",
            name="test",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            input={"value": "plain text"},
            output={"value": "response text"},
        )
        oi = kensa_to_oi(span)
        assert oi["attributes"]["input.value"] == "plain text"
        assert oi["attributes"]["output.value"] == "response text"

    def test_metadata_preserved(self) -> None:
        from datetime import datetime, timezone

        span = Span(
            trace_id="t1",
            span_id="s1",
            name="test",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            metadata={"custom.key": "custom_value"},
        )
        oi = kensa_to_oi(span)
        assert oi["attributes"]["custom.key"] == "custom_value"


class TestOiToKensaEdgeCases:
    def test_unix_seconds_timestamp(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": 1742223000,  # seconds
            "end_time": 1742223001,
            "attributes": {"openinference.span.kind": "LLM"},
        }
        span = oi_to_kensa(oi)
        assert span.start_time.year >= 2025

    def test_unix_milliseconds_timestamp(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": 1742223000000,  # milliseconds
            "end_time": 1742223001000,
            "attributes": {"openinference.span.kind": "LLM"},
        }
        span = oi_to_kensa(oi)
        assert span.start_time.year >= 2025

    def test_unix_microseconds_timestamp(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": 1742223000000000,  # microseconds
            "end_time": 1742223001000000,
            "attributes": {"openinference.span.kind": "LLM"},
        }
        span = oi_to_kensa(oi)
        assert span.start_time.year >= 2025

    def test_datetime_timestamp(self) -> None:
        from datetime import datetime, timezone

        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "end_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "attributes": {"openinference.span.kind": "LLM"},
        }
        span = oi_to_kensa(oi)
        assert span.start_time.year == 2026

    def test_status_string(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-01T00:00:01+00:00",
            "status": "ERROR",
            "attributes": {"openinference.span.kind": "LLM"},
        }
        span = oi_to_kensa(oi)
        assert span.status == "error"

    def test_status_numeric(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-01T00:00:01+00:00",
            "status": {"status_code": "2"},
            "attributes": {"openinference.span.kind": "LLM"},
        }
        span = oi_to_kensa(oi)
        assert span.status == "error"

    def test_status_non_dict_non_str(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-01T00:00:01+00:00",
            "status": 123,
            "attributes": {"openinference.span.kind": "LLM"},
        }
        span = oi_to_kensa(oi)
        assert span.status == "ok"  # default

    def test_input_value_dict(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-01T00:00:01+00:00",
            "attributes": {
                "openinference.span.kind": "LLM",
                "input.value": {"key": "val"},
                "output.value": {"key": "val"},
            },
        }
        span = oi_to_kensa(oi)
        assert span.input == {"key": "val"}
        assert span.output == {"key": "val"}

    def test_input_value_string(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-01T00:00:01+00:00",
            "attributes": {
                "openinference.span.kind": "LLM",
                "input.value": "hello",
                "output.value": "world",
            },
        }
        span = oi_to_kensa(oi)
        assert span.input == {"value": "hello"}
        assert span.output == {"value": "world"}

    def test_empty_string_input_and_output_are_preserved(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-01T00:00:01+00:00",
            "attributes": {
                "openinference.span.kind": "LLM",
                "input.value": "",
                "output.value": "",
            },
        }
        span = oi_to_kensa(oi)
        assert span.input == {"value": ""}
        assert span.output == {"value": ""}

    def test_unknown_kind_defaults_chain(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-01T00:00:01+00:00",
            "attributes": {"openinference.span.kind": "UNKNOWN_KIND"},
        }
        span = oi_to_kensa(oi)
        assert span.kind == SpanKind.CHAIN

    def test_invalid_timestamp_raises(self) -> None:
        from kensa.translate import _parse_timestamp

        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_timestamp([1, 2, 3])

    def test_cache_read_tokens_extracted(self) -> None:
        oi = {
            "trace_id": "t1",
            "span_id": "s1",
            "name": "test",
            "start_time": "2026-01-01T00:00:00+00:00",
            "end_time": "2026-01-01T00:00:01+00:00",
            "attributes": {
                "openinference.span.kind": "LLM",
                "llm.token_count.prompt": 1000,
                "llm.token_count.completion": 200,
                "llm.token_count.total": 1200,
                "llm.token_count.cache_read": 800,
            },
        }
        span = oi_to_kensa(oi)
        assert span.tokens is not None
        assert span.tokens.cache_read == 800

    def test_cache_read_round_trip(self) -> None:
        from datetime import datetime, timezone

        span = Span(
            trace_id="t1",
            span_id="s1",
            name="test",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            tokens=TokenCounts(prompt=1000, completion=200, total=1200, cache_read=800),
        )
        oi = kensa_to_oi(span)
        assert oi["attributes"]["llm.token_count.cache_read"] == 800
        span2 = oi_to_kensa(oi)
        assert span2.tokens is not None
        assert span2.tokens.cache_read == 800

    def test_cache_read_zero_not_exported(self) -> None:
        from datetime import datetime, timezone

        span = Span(
            trace_id="t1",
            span_id="s1",
            name="test",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            tokens=TokenCounts(prompt=100, completion=50, total=150),
        )
        oi = kensa_to_oi(span)
        assert "llm.token_count.cache_read" not in oi["attributes"]


JUDGE_MODELS = ["claude-sonnet-4.6", "gpt-5.4-mini"]


@pytest.mark.integration
class TestOpenRouterPricing:
    def test_judge_models_have_pricing(self) -> None:
        """Judge model slugs must resolve to valid pricing from OpenRouter."""
        try:
            prices = _fetch_openrouter_prices()
        except URLError:
            pytest.skip("OpenRouter unavailable in current environment")
        for model in JUDGE_MODELS:
            assert model in prices, f"{model} not found in OpenRouter pricing"
            assert prices[model]["input_cost_per_token"] > 0
            assert prices[model]["output_cost_per_token"] > 0

    def test_judge_models_have_cache_read_pricing(self) -> None:
        try:
            prices = _fetch_openrouter_prices()
        except URLError:
            pytest.skip("OpenRouter unavailable in current environment")
        for model in JUDGE_MODELS:
            assert "cache_read_input_token_cost" in prices.get(model, {}), (
                f"{model} missing cache_read_input_token_cost"
            )

    def test_or_prices_keyed_by_dotted_slug(self) -> None:
        try:
            prices = _fetch_openrouter_prices()
        except URLError:
            pytest.skip("OpenRouter unavailable in current environment")
        assert "claude-sonnet-4.6" in prices
        assert "claude-sonnet-4-6" not in prices


MOCK_PRICES = {
    "claude-sonnet-4.6": {
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
        "cache_read_input_token_cost": 3e-07,
    },
    "claude-haiku-4.5": {
        "input_cost_per_token": 1e-06,
        "output_cost_per_token": 5e-06,
        "cache_read_input_token_cost": 1e-07,
    },
    "gpt-5.4-mini": {
        "input_cost_per_token": 7.5e-07,
        "output_cost_per_token": 4.5e-06,
        "cache_read_input_token_cost": 7.5e-08,
    },
}


_REAL_OR_SLUGS = [
    ("claude-sonnet-4-6", "claude-sonnet-4.6"),
    ("claude-sonnet-4-6-20260217", "claude-sonnet-4.6"),
    ("claude-opus-4-7", "claude-opus-4.7"),
    ("claude-haiku-4-5", "claude-haiku-4.5"),
    ("claude-haiku-4-5-20251001", "claude-haiku-4.5"),
    ("claude-3-5-haiku-20241022", "claude-3.5-haiku"),
    ("claude-3-7-sonnet", "claude-3.7-sonnet"),
    ("claude-opus-4-6-fast", "claude-opus-4.6-fast"),
    ("gpt-5.4-mini", "gpt-5.4-mini"),
    ("gpt-4o", "gpt-4o"),
    ("gpt-4o-mini", "gpt-4o-mini"),
    ("gpt-4o-2024-08-06", "gpt-4o-2024-08-06"),
    ("gpt-4o-mini-2024-07-18", "gpt-4o-mini-2024-07-18"),
    ("gpt-4.1", "gpt-4.1"),
    ("o1", "o1"),
    ("o3-mini", "o3-mini"),
    ("o4-mini", "o4-mini"),
    ("llama-3.3-70b-instruct", "llama-3.3-70b-instruct"),
    ("llama-3-3-70b-instruct", "llama-3.3-70b-instruct"),
    ("llama-3.1-70b-instruct", "llama-3.1-70b-instruct"),
    ("llama-3-1-70b-instruct", "llama-3.1-70b-instruct"),
    ("llama-3-8b-instruct", "llama-3-8b-instruct"),
    ("llama-3-70b-instruct", "llama-3-70b-instruct"),
    ("llama-3.2-3b-instruct", "llama-3.2-3b-instruct"),
    ("qwen-2.5-72b-instruct", "qwen-2.5-72b-instruct"),
    ("qwen-2-5-72b-instruct", "qwen-2.5-72b-instruct"),
    ("qwen-2-5-coder-32b-instruct", "qwen-2.5-coder-32b-instruct"),
    ("gemini-2.5-flash", "gemini-2.5-flash"),
    ("gemini-2-5-flash", "gemini-2.5-flash"),
    ("gemini-2.5-pro", "gemini-2.5-pro"),
    ("gemini-3-flash-preview", "gemini-3-flash-preview"),
    ("mistral-small-3.1-24b-instruct", "mistral-small-3.1-24b-instruct"),
    ("mistral-small-3-1-24b-instruct", "mistral-small-3.1-24b-instruct"),
    ("mistral-medium-3.1", "mistral-medium-3.1"),
    ("deepseek-chat-v3.1", "deepseek-chat-v3.1"),
    ("grok-4.1-fast", "grok-4.1-fast"),
    ("anthropic/claude-sonnet-4.6", "claude-sonnet-4.6"),
    ("openai/gpt-5.4-mini", "gpt-5.4-mini"),
    ("meta-llama/llama-3.3-70b-instruct", "llama-3.3-70b-instruct"),
]


class TestCandidateSlugs:
    @pytest.mark.parametrize(("sdk_name", "or_slug"), _REAL_OR_SLUGS)
    def test_resolves_to_or_slug(self, sdk_name: str, or_slug: str) -> None:
        assert or_slug in candidate_slugs(sdk_name), (
            f"{sdk_name!r} did not produce OR slug {or_slug!r}; got {candidate_slugs(sdk_name)}"
        )

    def test_version_dashes_do_not_mangle_size_segments(self) -> None:
        assert "llama-3.3.70b-instruct" not in candidate_slugs("llama-3-3-70b-instruct")
        assert "qwen-2.5.72b-instruct" not in candidate_slugs("qwen-2-5-72b-instruct")

    def test_dashed_date_not_converted(self) -> None:
        assert "gpt-4o-2024.08-06" not in candidate_slugs("gpt-4o-2024-08-06")
        assert "gpt-4o-2024-08.06" not in candidate_slugs("gpt-4o-2024-08-06")

    def test_dotted_dated_also_tries_dashed_dated(self) -> None:
        candidates = candidate_slugs("gpt-5.4-mini-2026-03-17")
        assert "gpt-5-4-mini-2026-03-17" in candidates
        assert "gpt-5.4-mini-2026-03-17" in candidates
        assert "gpt-5.4-mini" in candidates


@pytest.mark.integration
class TestCandidateSlugsAgainstLiveOpenRouter:
    def test_every_sdk_form_resolves_in_live_prices(self) -> None:
        try:
            prices = _fetch_openrouter_prices()
        except URLError:
            pytest.skip("OpenRouter unavailable in current environment")
        misses: list[tuple[str, str, list[str]]] = []
        for sdk_name, expected_or_slug in _REAL_OR_SLUGS:
            if expected_or_slug not in prices:
                continue
            candidates = candidate_slugs(sdk_name)
            if not any(c in prices for c in candidates):
                misses.append((sdk_name, expected_or_slug, candidates))
        assert not misses, f"SDK names that failed to resolve in live OR prices: {misses}"


class TestComputeCost:
    def test_none_model(self) -> None:
        assert _compute_cost(None, TokenCounts(prompt=100, completion=50, total=150)) is None

    def test_none_tokens(self) -> None:
        assert _compute_cost("claude-sonnet-4-6", None) is None

    @patch("kensa.pricing._MODEL_PRICES", MOCK_PRICES)
    def test_unknown_model(self) -> None:
        tokens = TokenCounts(prompt=100, completion=50, total=150)
        assert _compute_cost("unknown-model", tokens) is None

    @patch("kensa.pricing._MODEL_PRICES", MOCK_PRICES)
    def test_basic_no_cache(self) -> None:
        tokens = TokenCounts(prompt=1000, completion=500, total=1500)
        cost = _compute_cost("claude-sonnet-4-6", tokens)
        assert cost is not None
        assert cost.prompt == pytest.approx(1000 * 3e-06)
        assert cost.completion == pytest.approx(500 * 1.5e-05)
        assert cost.total == pytest.approx(cost.prompt + cost.completion)

    @patch("kensa.pricing._MODEL_PRICES", MOCK_PRICES)
    def test_with_cache_read(self) -> None:
        tokens = TokenCounts(prompt=1000, completion=500, total=1500, cache_read=800)
        cost = _compute_cost("claude-sonnet-4-6", tokens)
        assert cost is not None
        expected_prompt = 200 * 3e-06 + 800 * 3e-07
        assert cost.prompt == pytest.approx(expected_prompt)
        assert cost.total == pytest.approx(expected_prompt + 500 * 1.5e-05)

    @patch("kensa.pricing._MODEL_PRICES", MOCK_PRICES)
    def test_cache_read_cheaper_than_full(self) -> None:
        tokens_no_cache = TokenCounts(prompt=1000, completion=500, total=1500)
        tokens_cached = TokenCounts(prompt=1000, completion=500, total=1500, cache_read=800)
        cost_full = _compute_cost("claude-sonnet-4-6", tokens_no_cache)
        cost_cached = _compute_cost("claude-sonnet-4-6", tokens_cached)
        assert cost_full is not None
        assert cost_cached is not None
        assert cost_cached.total < cost_full.total

    @patch("kensa.pricing._MODEL_PRICES", MOCK_PRICES)
    def test_anthropic_dated_snapshot(self) -> None:
        tokens = TokenCounts(prompt=1000, completion=500, total=1500)
        cost = _compute_cost("claude-haiku-4-5-20251001", tokens)
        assert cost is not None
        assert cost.prompt == pytest.approx(1000 * 1e-06)

    @patch("kensa.pricing._MODEL_PRICES", MOCK_PRICES)
    def test_openai_dated_snapshot(self) -> None:
        tokens = TokenCounts(prompt=1000, completion=500, total=1500)
        cost = _compute_cost("gpt-5.4-mini-2026-03-17", tokens)
        assert cost is not None
        assert cost.prompt == pytest.approx(1000 * 7.5e-07)

    @patch("kensa.pricing._MODEL_PRICES", MOCK_PRICES)
    def test_provider_prefixed_model(self) -> None:
        tokens = TokenCounts(prompt=1000, completion=500, total=1500)
        cost = _compute_cost("anthropic/claude-sonnet-4.6", tokens)
        assert cost is not None
        assert cost.prompt == pytest.approx(1000 * 3e-06)

    def test_openrouter_failure_returns_none(self) -> None:
        tokens = TokenCounts(prompt=1000, completion=500, total=1500)
        with (
            patch("kensa.pricing._MODEL_PRICES", None),
            patch(
                "kensa.pricing.fetch_openrouter_prices",
                side_effect=URLError("pricing unavailable"),
            ),
        ):
            assert _compute_cost("claude-sonnet-4-6", tokens) is None


class TestKensaToOiMultiTool:
    """Tests for kensa_to_oi serializing multiple tools."""

    def test_single_tool_uses_tool_name_attr(self) -> None:
        from datetime import datetime, timezone

        span = Span(
            trace_id="t",
            span_id="s",
            name="llm",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            tools=[ToolInfo(name="search", args={"q": "test"})],
        )
        oi = kensa_to_oi(span)
        assert oi["attributes"]["tool.name"] == "search"
        assert oi["attributes"]["tool.parameters"] == {"q": "test"}
        assert not any(k.startswith("llm.tool_calls") for k in oi["attributes"])

    def test_multiple_tools_uses_indexed_attrs(self) -> None:
        from datetime import datetime, timezone

        span = Span(
            trace_id="t",
            span_id="s",
            name="llm",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            tools=[
                ToolInfo(name="search", args={"q": "a"}),
                ToolInfo(name="calculate", args={"expr": "1+1"}),
            ],
        )
        oi = kensa_to_oi(span)
        assert "tool.name" not in oi["attributes"]
        assert oi["attributes"]["llm.tool_calls.0.tool_call.function.name"] == "search"
        assert oi["attributes"]["llm.tool_calls.1.tool_call.function.name"] == "calculate"

    def test_multi_tool_round_trip(self) -> None:
        from datetime import datetime, timezone

        span = Span(
            trace_id="t",
            span_id="s",
            name="llm",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            tools=[
                ToolInfo(name="search", args={"q": "weather"}),
                ToolInfo(name="calc", args={"x": 42}),
            ],
        )
        oi = kensa_to_oi(span)
        restored = oi_to_kensa(oi)
        assert len(restored.tools) == 2
        assert restored.tools[0].name == "search"
        assert restored.tools[1].name == "calc"
        assert restored.tools[1].args == {"x": 42}


class TestKensaToOiValueRoundTrip:
    """Tests for output.value / input.value round-trip correctness."""

    def test_value_string_round_trips(self) -> None:
        """The primary production shape: API responses stored as JSON strings
        under {"value": ...} must survive the round trip so extract_output_text
        can parse them downstream."""
        import json
        from datetime import datetime, timezone

        api_response = json.dumps({"content": [{"type": "text", "text": "P1"}]})
        span = Span(
            trace_id="t",
            span_id="s",
            name="llm",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            output={"value": api_response},
            input={"value": "What is the weather?"},
        )
        oi = kensa_to_oi(span)
        assert oi["attributes"]["output.value"] == api_response
        assert oi["attributes"]["input.value"] == "What is the weather?"

        restored = oi_to_kensa(oi)
        assert restored.output == {"value": api_response}
        assert restored.input == {"value": "What is the weather?"}

    def test_messages_still_round_trips(self) -> None:
        from datetime import datetime, timezone

        span = Span(
            trace_id="t",
            span_id="s",
            name="llm",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            input={"messages": [{"role": "user", "content": "hi"}]},
            output={"messages": [{"role": "assistant", "content": "hello"}]},
        )
        oi = kensa_to_oi(span)
        restored = oi_to_kensa(oi)
        assert restored.input == {"messages": [{"role": "user", "content": "hi"}]}
        assert restored.output == {"messages": [{"role": "assistant", "content": "hello"}]}

    def test_arbitrary_dict_output_round_trips(self) -> None:
        from datetime import datetime, timezone

        span = Span(
            trace_id="t",
            span_id="s",
            name="llm",
            kind=SpanKind.LLM,
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            output={"result": 42},
        )
        oi = kensa_to_oi(span)
        assert isinstance(oi["attributes"]["output.value"], str)
