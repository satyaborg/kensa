"""End-to-end integration tests against real LLM APIs.

Skips cleanly when API keys or instrumentor packages are missing. Skip with
``pytest -m "not integration"``. Model overrides via ``KENSA_TEST_ANTHROPIC_MODEL``,
``KENSA_TEST_OPENAI_MODEL``, ``KENSA_TEST_ANTHROPIC_JUDGE_MODEL``, and
``KENSA_TEST_OPENAI_JUDGE_MODEL``.
"""

from __future__ import annotations

import importlib.util
import os
import textwrap
from pathlib import Path

import pytest

from kensa.judge import JudgeProvider
from kensa.models import (
    Check,
    CheckType,
    Result,
    ResultStatus,
    Scenario,
    ScenarioSource,
    Span,
    SpanKind,
)
from kensa.runner import ensure_dotenv_loaded, read_trace, run_scenario

_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
_GREETING_CRITERIA = (
    "Pass only if the agent's final response is a greeting containing hi or hello. Fail otherwise."
)


def _anthropic_model() -> str:
    return os.environ.get("KENSA_TEST_ANTHROPIC_MODEL", _DEFAULT_ANTHROPIC_MODEL)


def _openai_model() -> str:
    return os.environ.get("KENSA_TEST_OPENAI_MODEL", _DEFAULT_OPENAI_MODEL)


def _anthropic_judge_model() -> str:
    return os.environ.get("KENSA_TEST_ANTHROPIC_JUDGE_MODEL", _anthropic_model())


def _openai_judge_model() -> str:
    return os.environ.get("KENSA_TEST_OPENAI_JUDGE_MODEL", _openai_model())


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


def _strip_markdown_fences(text: str) -> str:
    """Strip a leading ``` fence (optionally ```json) and a trailing ``` fence."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.split("\n")
    lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
    return "\n".join(lines).strip()


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
    spans, _, _ = _run_agent_capture(tmp_path, agent_path, scenario_id, prompt, timeout)
    return spans


def _run_agent_capture(
    tmp_path: Path,
    agent_path: Path,
    scenario_id: str,
    prompt: str,
    timeout: int = 60,
) -> tuple[list[Span], str, str]:
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
    return read_trace(run.trace_path), run.trace_path, run.stdout


def _judge_live_output(
    tmp_path: Path,
    agent_body: str,
    run_scenario_id: str,
    agent_prompt: str,
    judge_provider: JudgeProvider,
) -> Result:
    from kensa.judge import judge_scenario

    agent = _write_agent(tmp_path, agent_body)
    spans, trace_path, stdout = _run_agent_capture(
        tmp_path,
        agent,
        run_scenario_id,
        agent_prompt,
    )
    scenario = Scenario(
        id="live_judge_greeting",
        name="Live judge greeting rubric",
        source=ScenarioSource.CODE,
        checks=[Check(type=CheckType.MAX_TURNS, params={"max": 5})],
        criteria=_GREETING_CRITERIA,
    )
    result = judge_scenario(
        scenario,
        spans,
        trace_path,
        judge_provider=judge_provider,
        stdout=stdout,
    )
    assert result.judge_result is not None, "judge result missing for scenario with criteria"
    assert all(check.passed for check in result.check_results), result.check_results
    return result


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
class TestAnthropicJudgeEndToEnd:
    def test_live_judge_passes_for_greeting_output(self, tmp_path: Path) -> None:
        _skip_unless_anthropic_ready()
        os.environ.setdefault("KENSA_TEST_ANTHROPIC_MODEL", _anthropic_model())

        from kensa.judge import AnthropicJudge

        result = _judge_live_output(
            tmp_path,
            _ANTHROPIC_SIMPLE_AGENT,
            "anthropic_judge_pass",
            "Reply with hello only.",
            AnthropicJudge(model=_anthropic_judge_model()),
        )

        assert result.status == ResultStatus.PASS
        assert result.judge_result is not None
        assert result.judge_result.passed is True
        assert result.judge_result.verdict == ResultStatus.PASS
        assert result.judge_result.reasoning

    def test_live_judge_fails_for_non_greeting_output(self, tmp_path: Path) -> None:
        _skip_unless_anthropic_ready()
        os.environ.setdefault("KENSA_TEST_ANTHROPIC_MODEL", _anthropic_model())

        from kensa.judge import AnthropicJudge

        result = _judge_live_output(
            tmp_path,
            _ANTHROPIC_SIMPLE_AGENT,
            "anthropic_judge_fail",
            "Reply with banana only.",
            AnthropicJudge(model=_anthropic_judge_model()),
        )

        assert result.status == ResultStatus.FAIL
        assert result.judge_result is not None
        assert result.judge_result.passed is False
        assert result.judge_result.verdict == ResultStatus.FAIL
        assert result.judge_result.reasoning


@pytest.mark.integration
class TestOpenaiJudgeEndToEnd:
    def test_live_judge_passes_for_greeting_output(self, tmp_path: Path) -> None:
        _skip_unless_openai_ready()
        os.environ.setdefault("KENSA_TEST_OPENAI_MODEL", _openai_model())

        from kensa.judge import OpenAIJudge

        result = _judge_live_output(
            tmp_path,
            _OPENAI_SIMPLE_AGENT,
            "openai_judge_pass",
            "Reply with hello only.",
            OpenAIJudge(model=_openai_judge_model()),
        )

        assert result.status == ResultStatus.PASS
        assert result.judge_result is not None
        assert result.judge_result.passed is True
        assert result.judge_result.verdict == ResultStatus.PASS
        assert result.judge_result.reasoning

    def test_live_judge_fails_for_non_greeting_output(self, tmp_path: Path) -> None:
        _skip_unless_openai_ready()
        os.environ.setdefault("KENSA_TEST_OPENAI_MODEL", _openai_model())

        from kensa.judge import OpenAIJudge

        result = _judge_live_output(
            tmp_path,
            _OPENAI_SIMPLE_AGENT,
            "openai_judge_fail",
            "Reply with banana only.",
            OpenAIJudge(model=_openai_judge_model()),
        )

        assert result.status == ResultStatus.FAIL
        assert result.judge_result is not None
        assert result.judge_result.passed is False
        assert result.judge_result.verdict == ResultStatus.FAIL
        assert result.judge_result.reasoning


@pytest.mark.integration
class TestGenerateEndToEnd:
    def test_generate_from_real_trace_produces_executable_scenarios(self, tmp_path: Path) -> None:
        _skip_unless_anthropic_ready()
        os.environ.setdefault("KENSA_TEST_ANTHROPIC_MODEL", _anthropic_model())

        agent_path = _write_agent(tmp_path, _ANTHROPIC_SIMPLE_AGENT)
        run_command = ["python", str(agent_path)]
        prompt = "Classify this ticket priority as P1, P2, or P3: Our checkout is down."

        scenario = Scenario(
            id="seed",
            name="Seed",
            source=ScenarioSource.CODE,
            input=prompt,
            run_command=run_command,
        )
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir(exist_ok=True)
        _, seed_run = run_scenario(scenario, trace_dir=str(trace_dir))
        assert seed_run.exit_code == 0, seed_run.stderr
        trace_path = Path(seed_run.trace_path)

        from kensa.generate import _scenario_to_yaml, generate_from_traces
        from kensa.runner import load_scenario

        scenarios = generate_from_traces(
            [trace_path],
            count=2,
            run_commands=[run_command],
        )

        assert scenarios, "live generator returned no valid scenarios"
        assert len(scenarios) <= 2, "generator should cap to requested count"

        generated = scenarios[0]
        assert generated.source == ScenarioSource.TRACES
        assert generated.run_command == run_command, (
            f"LLM did not reuse the observed run_command: got {generated.run_command}"
        )
        assert generated.checks, "generated scenario should have at least one check"
        assert generated.id
        assert generated.name

        yaml_text = _scenario_to_yaml(generated)
        out_path = tmp_path / f"{generated.id}.yaml"
        out_path.write_text(yaml_text)
        reloaded = load_scenario(out_path)
        assert reloaded.id == generated.id
        assert reloaded.run_command == run_command

    def test_generated_scenario_roundtrips_through_runner(self, tmp_path: Path) -> None:
        """Seed → generate → re-run → checks. Closes the generate pipeline loop."""
        _skip_unless_anthropic_ready()
        os.environ.setdefault("KENSA_TEST_ANTHROPIC_MODEL", _anthropic_model())

        agent_path = _write_agent(tmp_path, _ANTHROPIC_SIMPLE_AGENT)
        run_command = ["python", str(agent_path)]
        prompt = "Say hello."

        seed = Scenario(
            id="seed",
            name="Seed",
            source=ScenarioSource.CODE,
            input=prompt,
            run_command=run_command,
        )
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir(exist_ok=True)
        _, seed_run = run_scenario(seed, trace_dir=str(trace_dir))
        assert seed_run.exit_code == 0, seed_run.stderr

        from kensa.checks import run_checks
        from kensa.generate import generate_from_traces

        scenarios = generate_from_traces(
            [Path(seed_run.trace_path)],
            count=1,
            run_commands=[run_command],
        )
        assert scenarios, "live generator returned no valid scenarios"
        generated = scenarios[0]

        _, generated_run = run_scenario(generated, trace_dir=str(trace_dir))
        assert generated_run.exit_code == 0, (
            f"generated scenario failed to execute:\n{generated_run.stderr}"
        )

        spans = read_trace(generated_run.trace_path)
        assert spans, "generated scenario produced no spans"

        check_dicts = [c.model_dump(mode="json") for c in generated.checks]
        check_results = run_checks(spans, check_dicts)
        assert len(check_results) == len(generated.checks), (
            "run_checks dropped or duplicated a check"
        )

    def test_generate_from_openai_trace(self, tmp_path: Path) -> None:
        """Same pipeline as Anthropic, but through the OpenAI provider."""
        _skip_unless_openai_ready()
        os.environ.setdefault("KENSA_TEST_OPENAI_MODEL", _openai_model())
        os.environ.pop("ANTHROPIC_API_KEY", None)

        agent_path = _write_agent(tmp_path, _OPENAI_SIMPLE_AGENT)
        run_command = ["python", str(agent_path)]

        seed = Scenario(
            id="seed_openai",
            name="Seed OpenAI",
            source=ScenarioSource.CODE,
            input="Say hello.",
            run_command=run_command,
        )
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir(exist_ok=True)
        _, seed_run = run_scenario(seed, trace_dir=str(trace_dir))
        assert seed_run.exit_code == 0, seed_run.stderr

        from kensa.generate import generate_from_traces

        scenarios = generate_from_traces(
            [Path(seed_run.trace_path)],
            count=1,
            run_commands=[run_command],
        )
        assert scenarios, "openai generator returned no valid scenarios"
        assert scenarios[0].run_command == run_command
        assert scenarios[0].checks

    def test_cli_generate_end_to_end(self, tmp_path: Path) -> None:
        """kensa generate CLI wires resolve_trace_paths + completer + write together."""
        from click.testing import CliRunner

        _skip_unless_anthropic_ready()
        os.environ.setdefault("KENSA_TEST_ANTHROPIC_MODEL", _anthropic_model())

        agent_path = _write_agent(tmp_path, _ANTHROPIC_SIMPLE_AGENT)
        run_command = ["python", str(agent_path)]

        seed = Scenario(
            id="seed_cli",
            name="Seed CLI",
            source=ScenarioSource.CODE,
            input="Say hello.",
            run_command=run_command,
        )
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir(exist_ok=True)
        _, seed_run = run_scenario(seed, trace_dir=str(trace_dir))
        assert seed_run.exit_code == 0, seed_run.stderr

        from kensa.cli import cli

        scenarios_dir = tmp_path / "scenarios"
        result = CliRunner().invoke(
            cli,
            [
                "generate",
                "--trace",
                seed_run.trace_path,
                "-n",
                "1",
                "--scenario-dir",
                str(scenarios_dir),
                "--run-command",
                " ".join(run_command),
            ],
        )
        assert result.exit_code == 0, result.output
        written = list(scenarios_dir.glob("*.yaml"))
        assert len(written) == 1, f"expected 1 scenario file, got {written}"
        assert "1 written" in result.output


@pytest.mark.integration
class TestLlmCompleterEndToEnd:
    """Live Completer round-trips; sub-cent total at haiku / gpt-5.4-mini prices."""

    @staticmethod
    def _skip_unless_anthropic_sdk() -> None:
        ensure_dotenv_loaded()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")
        if importlib.util.find_spec("anthropic") is None:
            pytest.skip("anthropic SDK not installed")

    @staticmethod
    def _skip_unless_openai_sdk() -> None:
        ensure_dotenv_loaded()
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not set")
        if importlib.util.find_spec("openai") is None:
            pytest.skip("openai SDK not installed")

    def test_anthropic_completer_plain_roundtrip(self) -> None:
        self._skip_unless_anthropic_sdk()
        from kensa.llm import AnthropicCompleter

        completer = AnthropicCompleter(model=_anthropic_model(), max_tokens=50)
        text = completer.complete("Reply with exactly the word: hello")
        assert text.strip(), "completer returned empty text"
        assert "hello" in text.lower()

    def test_anthropic_completer_json_mode_returns_parseable_json(self) -> None:
        import json

        self._skip_unless_anthropic_sdk()
        from kensa.llm import AnthropicCompleter

        completer = AnthropicCompleter(model=_anthropic_model(), max_tokens=50)
        text = completer.complete(
            'Return this JSON exactly and nothing else: {"ok": true}',
            response_format="json",
        )
        payload = json.loads(_strip_markdown_fences(text))
        assert payload.get("ok") is True

    def test_openai_completer_plain_roundtrip(self) -> None:
        self._skip_unless_openai_sdk()
        from kensa.llm import OpenAICompleter

        completer = OpenAICompleter(model=_openai_model(), max_tokens=50)
        text = completer.complete("Reply with exactly the word: hello")
        assert text.strip(), "completer returned empty text"
        assert "hello" in text.lower()

    def test_openai_completer_json_mode_returns_parseable_json(self) -> None:
        import json

        self._skip_unless_openai_sdk()
        from kensa.llm import OpenAICompleter

        completer = OpenAICompleter(model=_openai_model(), max_tokens=50)
        text = completer.complete(
            'Return this JSON exactly and nothing else: {"ok": true}',
            response_format="json",
        )
        payload = json.loads(text)
        assert payload.get("ok") is True


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
