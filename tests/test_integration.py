"""Integration tests — exercise the real subprocess + OTel + trace pipeline.

These tests don't need API keys. They write tiny agent scripts to tmp_path,
run them through the kensa harness, and verify the full data flow.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from kensa.models import (
    Check,
    CheckType,
    ResultStatus,
    Scenario,
    SpanKind,
)
from kensa.runner import read_trace, run_scenario

# The python that has kensa installed — must match the venv running pytest.
PYTHON = sys.executable


def _write_agent(tmp_path: Path, code: str, filename: str = "agent.py") -> Path:
    """Write a small agent script and return its path."""
    script = tmp_path / filename
    script.write_text(textwrap.dedent(code))
    return script


# ---------------------------------------------------------------------------
# Tier 1: subprocess + exporter round-trip
# ---------------------------------------------------------------------------


class TestExporterRoundTrip:
    """instrument() → OTel spans → JSONL → translate → kensa Spans."""

    def test_single_llm_span(self, tmp_path: Path) -> None:
        """A subprocess that creates one LLM span produces a readable trace."""
        agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            with tracer.start_as_current_span("ChatCompletion") as span:
                span.set_attribute("openinference.span.kind", "LLM")
                span.set_attribute("llm.model_name", "test-model")
                span.set_attribute("llm.token_count.prompt", 10)
                span.set_attribute("llm.token_count.completion", 5)
                span.set_attribute("llm.token_count.total", 15)
            """,
        )
        scenario = Scenario(
            id="exporter_roundtrip",
            name="Exporter round-trip",
            run_command=[PYTHON, str(agent)],
            input="hello",
        )
        _, run = run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)

        assert run.exit_code == 0
        spans = read_trace(run.trace_path)
        assert len(spans) == 1
        assert spans[0].kind == SpanKind.LLM
        assert spans[0].model == "test-model"
        assert spans[0].tokens is not None
        assert spans[0].tokens.total == 15

    def test_tool_span(self, tmp_path: Path) -> None:
        """A TOOL span with tool.name and JSON-string parameters round-trips correctly."""
        agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            with tracer.start_as_current_span("get_weather") as span:
                span.set_attribute("openinference.span.kind", "TOOL")
                span.set_attribute("tool.name", "get_weather")
                span.set_attribute("tool.parameters", '{"city": "SF"}')
            """,
        )
        scenario = Scenario(
            id="tool_roundtrip",
            name="Tool round-trip",
            run_command=[PYTHON, str(agent)],
            input="test",
        )
        _, run = run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)

        spans = read_trace(run.trace_path)
        assert len(spans) == 1
        assert spans[0].kind == SpanKind.TOOL
        assert len(spans[0].tools) == 1
        assert spans[0].tools[0].name == "get_weather"

    def test_multi_span_trace(self, tmp_path: Path) -> None:
        """Multiple spans from one agent are all captured in a single trace file."""
        agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            with tracer.start_as_current_span("ChatCompletion") as parent:
                parent.set_attribute("openinference.span.kind", "LLM")
                parent.set_attribute("llm.model_name", "test-model")
                with tracer.start_as_current_span("search") as child:
                    child.set_attribute("openinference.span.kind", "TOOL")
                    child.set_attribute("tool.name", "search")
            """,
        )
        scenario = Scenario(
            id="multi_span",
            name="Multi-span trace",
            run_command=[PYTHON, str(agent)],
            input="test",
        )
        _, run = run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)

        spans = read_trace(run.trace_path)
        assert len(spans) == 2

        kinds = {s.kind for s in spans}
        assert SpanKind.LLM in kinds
        assert SpanKind.TOOL in kinds

        # Parent-child relationship should be preserved
        tool_span = next(s for s in spans if s.kind == SpanKind.TOOL)
        llm_span = next(s for s in spans if s.kind == SpanKind.LLM)
        assert tool_span.parent_span_id == llm_span.span_id

    def test_output_messages_roundtrip(self, tmp_path: Path) -> None:
        """LLM input/output messages survive the full pipeline."""
        agent = _write_agent(
            tmp_path,
            """\
            import sys, json
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            with tracer.start_as_current_span("ChatCompletion") as span:
                span.set_attribute("openinference.span.kind", "LLM")
                span.set_attribute("llm.model_name", "test-model")
                span.set_attribute("output.value", "The answer is 42.")
            """,
        )
        scenario = Scenario(
            id="output_roundtrip",
            name="Output round-trip",
            run_command=[PYTHON, str(agent)],
            input="test",
        )
        _, run = run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)

        spans = read_trace(run.trace_path)
        assert spans[0].output is not None
        assert "42" in str(spans[0].output)


# ---------------------------------------------------------------------------
# Tier 1: deterministic checks on real traces
# ---------------------------------------------------------------------------


class TestDeterministicChecks:
    """Full run → judge (no LLM) pipeline with deterministic checks only."""

    def test_checks_pass(self, tmp_path: Path) -> None:
        """Scenario with passing deterministic checks gets PASS status."""
        from kensa.judge import judge_scenario

        agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            with tracer.start_as_current_span("ChatCompletion") as span:
                span.set_attribute("openinference.span.kind", "LLM")
                span.set_attribute("llm.model_name", "test-model")
                span.set_attribute("output.value", "San Francisco weather is sunny")
            with tracer.start_as_current_span("get_weather") as span:
                span.set_attribute("openinference.span.kind", "TOOL")
                span.set_attribute("tool.name", "get_weather")
            """,
        )
        scenario = Scenario(
            id="checks_pass",
            name="Checks pass test",
            run_command=[PYTHON, str(agent)],
            input="What is the weather?",
            checks=[
                Check(type=CheckType.TOOL_CALLED, params={"name": "get_weather"}),
                Check(type=CheckType.OUTPUT_CONTAINS, params={"value": "sunny"}),
                Check(type=CheckType.MAX_TURNS, params={"max": 5}),
            ],
        )
        _, run = run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)
        spans = read_trace(run.trace_path)

        result = judge_scenario(scenario, spans, run.trace_path, judge_provider=None)

        assert result.status == ResultStatus.PASS
        assert all(cr.passed for cr in result.check_results)
        assert result.trace is not None
        assert result.trace.llm_calls == 1
        assert result.trace.tool_calls == 1

    def test_checks_fail(self, tmp_path: Path) -> None:
        """Scenario with a failing check gets FAIL status without calling judge."""
        from kensa.judge import judge_scenario

        agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            with tracer.start_as_current_span("ChatCompletion") as span:
                span.set_attribute("openinference.span.kind", "LLM")
                span.set_attribute("llm.model_name", "test-model")
                span.set_attribute("output.value", "I don't know")
            """,
        )
        scenario = Scenario(
            id="checks_fail",
            name="Checks fail test",
            run_command=[PYTHON, str(agent)],
            input="test",
            checks=[
                Check(type=CheckType.TOOL_CALLED, params={"name": "missing_tool"}),
                Check(type=CheckType.OUTPUT_CONTAINS, params={"value": "answer"}),
            ],
            # criteria set, but judge should be skipped because checks fail
            criteria="Should use the correct tool",
        )
        _, run = run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)
        spans = read_trace(run.trace_path)

        result = judge_scenario(scenario, spans, run.trace_path, judge_provider=None)

        assert result.status == ResultStatus.FAIL
        assert not all(cr.passed for cr in result.check_results)
        assert result.judge_result is None  # skipped because checks failed

    def test_max_turns_exceeded(self, tmp_path: Path) -> None:
        """max_turns check fails when LLM call count exceeds threshold."""
        from kensa.judge import judge_scenario

        agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            for i in range(5):
                with tracer.start_as_current_span(f"ChatCompletion_{i}") as span:
                    span.set_attribute("openinference.span.kind", "LLM")
                    span.set_attribute("llm.model_name", "test-model")
            """,
        )
        scenario = Scenario(
            id="max_turns_exceeded",
            name="Max turns exceeded",
            run_command=[PYTHON, str(agent)],
            input="test",
            checks=[Check(type=CheckType.MAX_TURNS, params={"max": 3})],
        )
        _, run = run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)
        spans = read_trace(run.trace_path)

        result = judge_scenario(scenario, spans, run.trace_path, judge_provider=None)
        assert result.status == ResultStatus.FAIL

        turns_check = result.check_results[0]
        assert not turns_check.passed
        assert "5 LLM calls" in turns_check.detail


# ---------------------------------------------------------------------------
# Tier 1: full eval pipeline (run → judge → report artifacts)
# ---------------------------------------------------------------------------


class TestEvalPipeline:
    """End-to-end: scenario YAML → run → judge → result artifacts on disk."""

    def test_full_pipeline_artifacts(self, tmp_path: Path) -> None:
        """Full pipeline creates manifest, trace, and results files."""
        from kensa.judge import judge_scenario
        from kensa.runner import run_scenarios

        # Write scenario YAML
        scenario_dir = tmp_path / "scenarios"
        scenario_dir.mkdir()
        agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            with tracer.start_as_current_span("ChatCompletion") as span:
                span.set_attribute("openinference.span.kind", "LLM")
                span.set_attribute("llm.model_name", "test-model")
                span.set_attribute("output.value", "Hello from the agent")
            print("Hello from the agent")
            """,
        )
        scenario_data = {
            "id": "pipeline_test",
            "name": "Pipeline integration test",
            "run_command": [PYTHON, str(agent)],
            "input": "hello",
            "checks": [
                {"type": "output_contains", "params": {"value": "Hello"}},
                {"type": "max_turns", "params": {"max": 5}},
            ],
        }
        with open(scenario_dir / "pipeline_test.yaml", "w") as f:
            yaml.dump(scenario_data, f)

        # Run
        trace_dir = str(tmp_path / "traces")
        manifest = run_scenarios(
            scenario_dir=str(scenario_dir),
            trace_dir=trace_dir,
            timeout=15,
        )

        # Verify manifest
        assert manifest.run_id is not None
        assert "pipeline_test" in manifest.scenarios
        sr = manifest.scenarios["pipeline_test"][0]
        assert sr.exit_code == 0
        assert sr.trace_path != ""
        assert "Hello from the agent" in sr.stdout

        # Verify trace file exists and is valid
        trace_path = Path(sr.trace_path)
        assert trace_path.exists()
        spans = read_trace(str(trace_path))
        assert len(spans) >= 1

        # Verify manifest was persisted to .kensa/runs/
        runs_dir = Path(".kensa/runs")
        manifest_files = list(runs_dir.glob(f"{manifest.run_id}.json"))
        assert len(manifest_files) == 1

        # Judge (deterministic only, no API keys)
        scenario = Scenario.model_validate(scenario_data)
        result = judge_scenario(scenario, spans, str(trace_path), judge_provider=None)
        assert result.status == ResultStatus.PASS
        assert result.trace is not None
        assert result.trace.llm_calls == 1

    def test_multiple_scenarios(self, tmp_path: Path) -> None:
        """Multiple scenarios run independently and all produce results."""
        from kensa.runner import run_scenarios

        scenario_dir = tmp_path / "scenarios"
        scenario_dir.mkdir()

        for i in range(3):
            agent = _write_agent(
                tmp_path,
                f"""\
                import sys
                sys.path.insert(0, "src")
                from kensa import instrument
                instrument()
                from opentelemetry import trace
                tracer = trace.get_tracer("test-agent-{i}")
                with tracer.start_as_current_span("ChatCompletion") as span:
                    span.set_attribute("openinference.span.kind", "LLM")
                    span.set_attribute("llm.model_name", "model-{i}")
                """,
                filename=f"agent_{i}.py",
            )
            scenario_data = {
                "id": f"scenario_{i}",
                "name": f"Test scenario {i}",
                "run_command": [PYTHON, str(agent)],
                "input": f"input_{i}",
            }
            with open(scenario_dir / f"scenario_{i}.yaml", "w") as f:
                yaml.dump(scenario_data, f)

        manifest = run_scenarios(
            scenario_dir=str(scenario_dir),
            trace_dir=str(tmp_path / "traces"),
            timeout=15,
        )

        assert len(manifest.scenarios) == 3
        for i in range(3):
            sid = f"scenario_{i}"
            assert sid in manifest.scenarios
            sr = manifest.scenarios[sid][0]
            assert sr.exit_code == 0
            assert sr.trace_path != ""

            spans = read_trace(sr.trace_path)
            assert len(spans) == 1
            assert spans[0].model == f"model-{i}"


# ---------------------------------------------------------------------------
# Tier 1: error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    """Subprocess failures are handled gracefully."""

    def test_agent_crash_captured(self, tmp_path: Path) -> None:
        """A crashing agent produces a RuntimeError with stderr."""
        agent = _write_agent(
            tmp_path,
            """\
            import sys
            print("fatal error", file=sys.stderr)
            sys.exit(1)
            """,
        )
        scenario = Scenario(
            id="crash_test",
            name="Crash test",
            run_command=[PYTHON, str(agent)],
            input="test",
        )
        with pytest.raises(RuntimeError, match="fatal error"):
            run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)

    def test_agent_no_instrument_gives_clear_error(self, tmp_path: Path) -> None:
        """An agent that doesn't call instrument() gets a helpful error."""
        agent = _write_agent(
            tmp_path,
            """\
            print("I forgot to instrument")
            """,
        )
        scenario = Scenario(
            id="no_instrument",
            name="No instrument test",
            run_command=[PYTHON, str(agent)],
            input="test",
        )
        with pytest.raises(RuntimeError, match="produced no traces"):
            run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)

    def test_partial_failure_in_batch(self, tmp_path: Path) -> None:
        """One failing scenario doesn't block others in run_scenarios."""
        from kensa.runner import run_scenarios

        scenario_dir = tmp_path / "scenarios"
        scenario_dir.mkdir()

        # Good agent
        good_agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("good-agent")
            with tracer.start_as_current_span("ChatCompletion") as span:
                span.set_attribute("openinference.span.kind", "LLM")
                span.set_attribute("llm.model_name", "test-model")
            """,
            filename="good_agent.py",
        )

        # Bad agent
        bad_agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.exit(1)
            """,
            filename="bad_agent.py",
        )

        for sid, agent_path in [("good", good_agent), ("bad", bad_agent)]:
            scenario_data = {
                "id": sid,
                "name": f"{sid} scenario",
                "run_command": [PYTHON, str(agent_path)],
                "input": "test",
            }
            with open(scenario_dir / f"{sid}.yaml", "w") as f:
                yaml.dump(scenario_data, f)

        manifest = run_scenarios(
            scenario_dir=str(scenario_dir),
            trace_dir=str(tmp_path / "traces"),
            timeout=15,
        )

        assert len(manifest.scenarios) == 2

        # Good scenario succeeded
        assert manifest.scenarios["good"][0].exit_code == 0
        assert manifest.scenarios["good"][0].trace_path != ""

        # Bad scenario failed gracefully
        assert manifest.scenarios["bad"][0].exit_code == -1
        assert manifest.scenarios["bad"][0].trace_path == ""

    def test_env_overrides_reach_subprocess(self, tmp_path: Path) -> None:
        """Scenario env_overrides are visible inside the subprocess."""
        agent = _write_agent(
            tmp_path,
            """\
            import os, sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            val = os.environ.get("MY_CUSTOM_VAR", "NOT_SET")
            with tracer.start_as_current_span("ChatCompletion") as span:
                span.set_attribute("openinference.span.kind", "LLM")
                span.set_attribute("llm.model_name", "test-model")
                span.set_attribute("output.value", f"var={val}")
            """,
        )
        scenario = Scenario(
            id="env_test",
            name="Env override test",
            run_command=[PYTHON, str(agent)],
            input="test",
            env_overrides={"MY_CUSTOM_VAR": "integration_value"},
        )
        _, run = run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)

        spans = read_trace(run.trace_path)
        assert spans[0].output is not None
        assert "integration_value" in str(spans[0].output)

    def test_explicit_blank_input_reaches_subprocess(self, tmp_path: Path) -> None:
        """An explicit empty-string input should be passed as argv[1]."""
        agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            arg = sys.argv[1] if len(sys.argv) > 1 else "<missing>"
            with tracer.start_as_current_span("ChatCompletion") as span:
                span.set_attribute("openinference.span.kind", "LLM")
                span.set_attribute("llm.model_name", "test-model")
                span.set_attribute("output.value", repr(arg))
            """,
        )
        scenario = Scenario(
            id="blank_input",
            name="Blank input test",
            run_command=[PYTHON, str(agent)],
            input="",
        )
        _, run = run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)

        spans = read_trace(run.trace_path)
        assert run.input == ""
        assert spans[0].output == {"value": "''"}

    def test_omitted_input_does_not_reach_subprocess(self, tmp_path: Path) -> None:
        """A scenario without input should not receive an extra argv element."""
        agent = _write_agent(
            tmp_path,
            """\
            import sys
            sys.path.insert(0, "src")
            from kensa import instrument
            instrument()
            from opentelemetry import trace
            tracer = trace.get_tracer("test-agent")
            with tracer.start_as_current_span("ChatCompletion") as span:
                span.set_attribute("openinference.span.kind", "LLM")
                span.set_attribute("llm.model_name", "test-model")
                span.set_attribute("output.value", str(len(sys.argv)))
            """,
        )
        scenario = Scenario(
            id="no_input",
            name="No input test",
            run_command=[PYTHON, str(agent)],
        )
        _, run = run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=15)

        spans = read_trace(run.trace_path)
        assert run.input is None
        assert spans[0].output == {"value": "1"}


# ---------------------------------------------------------------------------
# Tier 1: CLI end-to-end (no API keys)
# ---------------------------------------------------------------------------


class TestCliIntegration:
    """CLI commands exercise the real pipeline (deterministic checks only).

    These mock only get_judge (which requires API keys) — everything else
    is real: subprocess execution, OTel instrumentation, trace I/O, checks.
    """

    def test_eval_cli_end_to_end(self, tmp_path: Path) -> None:
        """'kensa eval' through Click's test runner with a real agent."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from kensa.cli import cli

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            # Write the agent script
            agent = Path("agent.py")
            agent.write_text(
                textwrap.dedent("""\
                import sys
                sys.path.insert(0, "src")
                from kensa import instrument
                instrument()
                from opentelemetry import trace
                tracer = trace.get_tracer("cli-agent")
                with tracer.start_as_current_span("ChatCompletion") as span:
                    span.set_attribute("openinference.span.kind", "LLM")
                    span.set_attribute("llm.model_name", "test-model")
                    span.set_attribute("output.value", "CLI test output")
                print("CLI test output")
                """)
            )

            # Write scenario (no criteria → judge is called but does nothing)
            scenario_dir = Path(".kensa/scenarios")
            scenario_dir.mkdir(parents=True)
            scenario_data = {
                "id": "cli_test",
                "name": "CLI integration test",
                "run_command": [PYTHON, str(agent.resolve())],
                "input": "hello",
                "checks": [
                    {"type": "output_contains", "params": {"value": "CLI test"}},
                    {"type": "max_turns", "params": {"max": 5}},
                ],
            }
            with open(scenario_dir / "cli_test.yaml", "w") as f:
                yaml.dump(scenario_data, f)

            # Mock only get_judge — no API keys needed since no criteria
            with patch("kensa.judge.get_judge", return_value=None):
                result = runner.invoke(cli, ["eval", "--timeout", "15"])

            assert result.exit_code == 0, f"CLI failed:\n{result.output}"
            assert "cli_test" in result.output
            assert "passed" in result.output

    def test_run_then_report_json(self, tmp_path: Path) -> None:
        """'kensa run' → 'kensa judge' → 'kensa report --format json'."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from kensa.cli import cli

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            agent = Path("agent.py")
            agent.write_text(
                textwrap.dedent("""\
                import sys
                sys.path.insert(0, "src")
                from kensa import instrument
                instrument()
                from opentelemetry import trace
                tracer = trace.get_tracer("cli-agent")
                with tracer.start_as_current_span("ChatCompletion") as span:
                    span.set_attribute("openinference.span.kind", "LLM")
                    span.set_attribute("llm.model_name", "test-model")
                """)
            )

            scenario_dir = Path(".kensa/scenarios")
            scenario_dir.mkdir(parents=True)
            scenario_data = {
                "id": "run_report",
                "name": "Run then report",
                "run_command": [PYTHON, str(agent.resolve())],
                "input": "test",
            }
            with open(scenario_dir / "run_report.yaml", "w") as f:
                yaml.dump(scenario_data, f)

            # Step 1: run (fully real)
            run_result = runner.invoke(cli, ["run", "--timeout", "15"])
            assert run_result.exit_code == 0, f"Run failed:\n{run_result.output}"

            # Step 2: judge (mock only get_judge)
            with patch("kensa.judge.get_judge", return_value=None):
                judge_result = runner.invoke(cli, ["judge"])
            assert judge_result.exit_code == 0, f"Judge failed:\n{judge_result.output}"

            # Step 3: report (fully real — reads artifacts from disk)
            report_result = runner.invoke(cli, ["report", "--format", "json"])
            assert report_result.exit_code == 0, f"Report failed:\n{report_result.output}"
            # report command appends "HTML report: ..." after the JSON
            json_text = report_result.output.split("\nHTML report:")[0]
            data = json.loads(json_text)
            assert len(data) >= 1
            assert data[0]["scenario_id"] == "run_report"
