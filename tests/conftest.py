"""Shared fixtures for kensa tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kensa.models import (
    Check,
    CheckType,
    CostInfo,
    Scenario,
    ScenarioSource,
    Span,
    SpanKind,
    TokenCounts,
    ToolInfo,
)

BASE_TIME = datetime(2026, 3, 17, 14, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_llm_span() -> Span:
    return Span(
        trace_id="trace_001",
        span_id="span_001",
        name="ChatCompletion",
        kind=SpanKind.LLM,
        start_time=BASE_TIME,
        end_time=BASE_TIME + timedelta(seconds=1.5),
        status="ok",
        model="claude-sonnet-4-6",
        provider="anthropic",
        input={"messages": [{"role": "user", "content": "What is the weather?"}]},
        output={
            "messages": [
                {"role": "assistant", "content": "The weather in San Francisco is 65°F and sunny."}
            ]
        },
        tokens=TokenCounts(prompt=20, completion=15, total=35),
        cost=CostInfo(prompt=0.001, completion=0.002, total=0.003),
    )


@pytest.fixture
def sample_tool_span() -> Span:
    return Span(
        trace_id="trace_001",
        span_id="span_002",
        parent_span_id="span_001",
        name="get_weather",
        kind=SpanKind.TOOL,
        start_time=BASE_TIME + timedelta(seconds=0.5),
        end_time=BASE_TIME + timedelta(seconds=1.0),
        status="ok",
        tools=[
            ToolInfo(
                name="get_weather",
                args={"city": "San Francisco"},
                result={"temp": 65, "condition": "sunny"},
            )
        ],
    )


@pytest.fixture
def sample_spans(sample_llm_span: Span, sample_tool_span: Span) -> list[Span]:
    return [sample_llm_span, sample_tool_span]


@pytest.fixture
def sample_scenario() -> Scenario:
    return Scenario(
        id="smoke_test",
        name="Weather query smoke test",
        description="Basic weather query to verify agent works",
        source=ScenarioSource.CODE,
        input="What is the weather in San Francisco?",
        run_command=["python", "agent.py"],
        expected_outcome="Agent returns weather information for San Francisco",
        checks=[
            Check(
                type=CheckType.TOOLS_CALLED,
                params={"tools": ["get_weather"]},
                description="Agent should call the weather tool",
            ),
            Check(
                type=CheckType.OUTPUT_CONTAINS,
                params={"value": "San Francisco"},
                description="Output mentions the city",
            ),
            Check(
                type=CheckType.MAX_TURNS,
                params={"max": 5},
                description="Should complete in under 5 LLM calls",
            ),
        ],
        criteria="The agent should provide accurate weather information for the requested city.",
    )


@pytest.fixture
def sample_dataset_scenario() -> Scenario:
    return Scenario(
        id="sql_analyst",
        name="SQL analyst correctness",
        description="Tests query generation across difficulty levels",
        source=ScenarioSource.CODE,
        run_command=["python", "agent.py"],
        dataset="sql_queries.jsonl",
        input_field="query",
        expected_outcome="Agent produces valid SQL query",
        checks=[
            Check(
                type=CheckType.OUTPUT_CONTAINS,
                params={"value": "SELECT"},
                description="Output contains SQL SELECT",
            ),
        ],
        criteria="The agent should produce a syntactically valid SQL query.",
    )


@pytest.fixture
def sample_oi_span() -> dict:
    """Sample OpenInference span dict."""
    return {
        "trace_id": "abc123",
        "span_id": "span_001",
        "parent_span_id": None,
        "name": "ChatCompletion",
        "start_time": "2026-03-17T14:30:00+00:00",
        "end_time": "2026-03-17T14:30:01.500+00:00",
        "status": {"status_code": "OK"},
        "attributes": {
            "openinference.span.kind": "LLM",
            "llm.model_name": "claude-sonnet-4-6",
            "llm.provider": "anthropic",
            "llm.token_count.prompt": 20,
            "llm.token_count.completion": 15,
            "llm.token_count.total": 35,
            "llm.cost.prompt": 0.001,
            "llm.cost.completion": 0.002,
            "llm.cost.total": 0.003,
            "llm.input_messages": [{"role": "user", "content": "Hello"}],
            "llm.output_messages": [{"role": "assistant", "content": "Hi there!"}],
        },
    }


@pytest.fixture
def multi_trace_spans() -> list[Span]:
    """Multiple traces for analyzer testing."""
    spans = []
    for i in range(5):
        t = BASE_TIME + timedelta(minutes=i * 10)
        cost_multiplier = 10.0 if i == 4 else 1.0  # Last trace is a cost outlier

        spans.append(
            Span(
                trace_id=f"trace_{i:03d}",
                span_id=f"span_{i:03d}_llm",
                name="ChatCompletion",
                kind=SpanKind.LLM,
                start_time=t,
                end_time=t + timedelta(seconds=2 * (1 + i * 0.5)),
                status="error" if i == 2 else "ok",
                model="claude-sonnet-4-6",
                tokens=TokenCounts(prompt=100, completion=50, total=150),
                cost=CostInfo(total=0.01 * cost_multiplier),
            )
        )
        spans.append(
            Span(
                trace_id=f"trace_{i:03d}",
                span_id=f"span_{i:03d}_tool",
                parent_span_id=f"span_{i:03d}_llm",
                name="search",
                kind=SpanKind.TOOL,
                start_time=t + timedelta(seconds=0.5),
                end_time=t + timedelta(seconds=1.0),
                status="ok",
                tools=[ToolInfo(name="search", args={"q": f"query_{i}"})],
            )
        )
    return spans
