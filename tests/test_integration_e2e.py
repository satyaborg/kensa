"""End-to-end integration tests against real LLM APIs.

Skips cleanly when API keys or instrumentor packages are missing. Skip with
``pytest -m "not integration"``. Model overrides via ``KENSA_TEST_ANTHROPIC_MODEL``
/ ``KENSA_TEST_OPENAI_MODEL``. Cost budget <$0.01 per full pass.
"""

from __future__ import annotations

import importlib.util
import os
import textwrap
from pathlib import Path

import pytest

from kensa.models import Scenario, ScenarioSource, Span, SpanKind
from kensa.runner import ensure_dotenv_loaded, read_trace, run_scenario

_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"


def _anthropic_model() -> str:
    return os.environ.get("KENSA_TEST_ANTHROPIC_MODEL", _DEFAULT_ANTHROPIC_MODEL)


def _openai_model() -> str:
    return os.environ.get("KENSA_TEST_OPENAI_MODEL", _DEFAULT_OPENAI_MODEL)


def _has_any_instrumentor(sdk: str) -> bool:
    """True if either OpenInference or OTel-GenAI instrumentor is importable for ``sdk``."""
    return any(
        importlib.util.find_spec(mod) is not None
        for mod in (
            f"openinference.instrumentation.{sdk}",
            f"opentelemetry.instrumentation.{sdk}",
        )
    )


def _skip_unless_anthropic_ready() -> None:
    ensure_dotenv_loaded()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    if importlib.util.find_spec("anthropic") is None:
        pytest.skip("anthropic SDK not installed (uv sync --extra anthropic)")
    if not _has_any_instrumentor("anthropic"):
        pytest.skip("no anthropic instrumentor installed (uv sync --extra anthropic)")


def _skip_unless_openai_ready() -> None:
    ensure_dotenv_loaded()
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    if importlib.util.find_spec("openai") is None:
        pytest.skip("openai SDK not installed (uv sync --extra openai)")
    if not _has_any_instrumentor("openai"):
        pytest.skip("no openai instrumentor installed (uv sync --extra openai)")


def _write_agent(tmp_path: Path, body: str, filename: str = "agent.py") -> Path:
    path = tmp_path / filename
    path.write_text(textwrap.dedent(body).lstrip())
    return path


def _run_agent(
    tmp_path: Path,
    agent_path: Path,
    scenario_id: str,
    prompt: str,
    timeout: int = 60,
) -> list[Span]:
    scenario = Scenario(
        id=scenario_id,
        name=scenario_id,
        source=ScenarioSource.CODE,
        input=prompt,
        run_command=["python", str(agent_path)],
    )
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir(exist_ok=True)
    _, run = run_scenario(scenario, trace_dir=str(trace_dir), timeout=timeout)
    assert run.exit_code == 0, f"agent subprocess exited {run.exit_code}.\nstderr:\n{run.stderr}"
    return read_trace(run.trace_path)


_ANTHROPIC_SIMPLE_AGENT = """
    import os
    import sys
    from anthropic import Anthropic

    prompt = sys.argv[1] if len(sys.argv) > 1 else "Say 'ok'."
    client = Anthropic()
    resp = client.messages.create(
        model=os.environ.get("KENSA_TEST_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=30,
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            print(block.text)
            break
"""

_ANTHROPIC_TOOL_AGENT = """
    import json
    import os
    from anthropic import Anthropic

    client = Anthropic()
    resp = client.messages.create(
        model=os.environ.get("KENSA_TEST_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=200,
        tools=[{
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }],
        messages=[
            {
                "role": "user",
                "content": "What's the weather in Tokyo? You must call get_weather.",
            }
        ],
    )
    print(json.dumps({"stop_reason": resp.stop_reason}))
"""

_OPENAI_SIMPLE_AGENT = """
    import os
    import sys
    from openai import OpenAI

    prompt = sys.argv[1] if len(sys.argv) > 1 else "Say 'ok'."
    client = OpenAI()
    resp = client.chat.completions.create(
        model=os.environ.get("KENSA_TEST_OPENAI_MODEL", "gpt-5.4-mini"),
        max_completion_tokens=30,
        messages=[{"role": "user", "content": prompt}],
    )
    print(resp.choices[0].message.content)
"""

_OPENAI_TOOL_AGENT = """
    import json
    import os
    from openai import OpenAI

    client = OpenAI()
    resp = client.chat.completions.create(
        model=os.environ.get("KENSA_TEST_OPENAI_MODEL", "gpt-5.4-mini"),
        max_completion_tokens=200,
        tools=[{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }],
        tool_choice="required",
        messages=[
            {
                "role": "user",
                "content": "What's the weather in Tokyo? Call get_weather.",
            }
        ],
    )
    tool_calls = resp.choices[0].message.tool_calls or []
    print(json.dumps({"count": len(tool_calls)}))
"""


@pytest.mark.integration
class TestAnthropicEndToEnd:
    def test_basic_call_produces_llm_span(self, tmp_path: Path) -> None:
        _skip_unless_anthropic_ready()
        os.environ.setdefault("KENSA_TEST_ANTHROPIC_MODEL", _anthropic_model())
        agent = _write_agent(tmp_path, _ANTHROPIC_SIMPLE_AGENT)

        spans = _run_agent(tmp_path, agent, "anthropic_basic", "Say 'hi' only.")

        llm_spans = [s for s in spans if s.kind == SpanKind.LLM]
        assert llm_spans, f"no LLM spans captured; got kinds: {[s.kind for s in spans]}"
        span = llm_spans[0]
        assert span.model
        assert span.tokens is not None
        assert span.tokens.prompt > 0
        assert span.tokens.completion > 0

        from kensa.utils import get_agent_output

        assert get_agent_output(llm_spans).strip()

    def test_tool_call_is_extracted(self, tmp_path: Path) -> None:
        _skip_unless_anthropic_ready()
        os.environ.setdefault("KENSA_TEST_ANTHROPIC_MODEL", _anthropic_model())
        agent = _write_agent(tmp_path, _ANTHROPIC_TOOL_AGENT)

        spans = _run_agent(tmp_path, agent, "anthropic_tools", "")

        llm_spans = [s for s in spans if s.kind == SpanKind.LLM]
        assert llm_spans
        tool_names = [t.name for s in llm_spans for t in s.tools]
        assert "get_weather" in tool_names
        for span in llm_spans:
            for tool in span.tools:
                assert isinstance(tool.args, dict)

    def test_cost_computed_from_tokens(self, tmp_path: Path) -> None:
        _skip_unless_anthropic_ready()
        os.environ.setdefault("KENSA_TEST_ANTHROPIC_MODEL", _anthropic_model())
        agent = _write_agent(tmp_path, _ANTHROPIC_SIMPLE_AGENT)

        spans = _run_agent(tmp_path, agent, "anthropic_cost", "Hi.")

        llm_spans = [s for s in spans if s.kind == SpanKind.LLM]
        assert llm_spans
        assert any(s.cost is not None for s in llm_spans)


@pytest.mark.integration
class TestOpenaiEndToEnd:
    def test_basic_call_produces_llm_span(self, tmp_path: Path) -> None:
        _skip_unless_openai_ready()
        os.environ.setdefault("KENSA_TEST_OPENAI_MODEL", _openai_model())
        agent = _write_agent(tmp_path, _OPENAI_SIMPLE_AGENT)

        spans = _run_agent(tmp_path, agent, "openai_basic", "Say 'hi' only.")

        llm_spans = [s for s in spans if s.kind == SpanKind.LLM]
        assert llm_spans, f"no LLM spans captured; got kinds: {[s.kind for s in spans]}"
        span = llm_spans[0]
        assert span.model
        assert span.tokens is not None
        assert span.tokens.prompt > 0
        assert span.tokens.completion > 0

    def test_tool_call_is_extracted(self, tmp_path: Path) -> None:
        _skip_unless_openai_ready()
        os.environ.setdefault("KENSA_TEST_OPENAI_MODEL", _openai_model())
        agent = _write_agent(tmp_path, _OPENAI_TOOL_AGENT)

        spans = _run_agent(tmp_path, agent, "openai_tools", "")

        llm_spans = [s for s in spans if s.kind == SpanKind.LLM]
        assert llm_spans
        tool_names = [t.name for s in llm_spans for t in s.tools]
        assert "get_weather" in tool_names, (
            f"get_weather tool call not extracted; tools across spans: {tool_names}"
        )


@pytest.mark.integration
class TestChecksAgainstRealSpans:
    def test_tools_called_check_sees_real_tool_call(self, tmp_path: Path) -> None:
        _skip_unless_anthropic_ready()
        os.environ.setdefault("KENSA_TEST_ANTHROPIC_MODEL", _anthropic_model())
        agent = _write_agent(tmp_path, _ANTHROPIC_TOOL_AGENT)

        spans = _run_agent(tmp_path, agent, "anthropic_checks", "")

        from kensa.checks import check_tools_called

        result = check_tools_called(spans, {"tools": ["get_weather"]})
        assert result.passed, result.detail

    def test_max_turns_check_sees_real_llm_spans(self, tmp_path: Path) -> None:
        _skip_unless_anthropic_ready()
        os.environ.setdefault("KENSA_TEST_ANTHROPIC_MODEL", _anthropic_model())
        agent = _write_agent(tmp_path, _ANTHROPIC_SIMPLE_AGENT)

        spans = _run_agent(tmp_path, agent, "anthropic_maxturns", "Hi.")

        from kensa.checks import check_max_turns

        result = check_max_turns(spans, {"max": 5})
        assert result.passed, result.detail
