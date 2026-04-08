"""Tests for the scenario runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kensa.models import Scenario, Span, SpanKind
from kensa.runner import (
    _build_command,
    _read_spans,
    _trace_filename,
    _write_trace,
    load_dataset,
    load_dotenv,
    load_scenario,
    load_scenarios,
    read_trace,
)


class TestBuildCommand:
    def test_string_input(self) -> None:
        result = _build_command(["echo"], "hello world")
        assert result == ["echo", "hello world"]

    def test_dict_input(self) -> None:
        result = _build_command(["cmd"], {"key": "value"})
        assert result[0] == "cmd"
        assert '"key"' in result[1]

    def test_empty_input_not_appended(self) -> None:
        result = _build_command(["echo", "hello"], "")
        assert result == ["echo", "hello"]

    def test_shell_metacharacters_not_executed(self) -> None:
        # The argv list is passed directly to subprocess (shell=False), so any
        # metacharacters in the input remain a single literal argv element.
        result = _build_command(["echo"], "hello; rm -rf /")
        assert result == ["echo", "hello; rm -rf /"]

    def test_empty_command_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            _build_command([], "anything")

    def test_preserves_existing_args(self) -> None:
        result = _build_command(["python", "-c", "print('hi')"], "extra")
        assert result == ["python", "-c", "print('hi')", "extra"]


class TestTraceFilename:
    def test_uses_unix_milliseconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("kensa.runner.time.time_ns", lambda: 1_774_929_330_123_456_789)
        assert _trace_filename("weather") == "weather_1774929330123.jsonl"

    def test_changes_when_time_changes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        times = iter([1_000_000_000, 2_000_000_000])
        monkeypatch.setattr("kensa.runner.time.time_ns", lambda: next(times))
        assert _trace_filename("demo") != _trace_filename("demo")


class TestLoadDotenv:
    def test_loads_key_value_pairs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("FOO=bar\nBAZ=qux\n")
        result = load_dotenv()
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_skips_comments_and_blanks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("# comment\n\nKEY=value\n")
        result = load_dotenv()
        assert result == {"KEY": "value"}

    def test_strips_quotes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("A=\"hello\"\nB='world'\n")
        result = load_dotenv()
        assert result == {"A": "hello", "B": "world"}

    def test_missing_file_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert load_dotenv() == {}

    def test_walks_up_parent_dirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".env").write_text("ROOT_KEY=found\n")
        child = tmp_path / "sub" / "deep"
        child.mkdir(parents=True)
        monkeypatch.chdir(child)
        result = load_dotenv()
        assert result == {"ROOT_KEY": "found"}


class TestLoadScenario:
    def test_load_yaml(self, tmp_path: Path, sample_scenario: Scenario) -> None:
        path = tmp_path / "test.yaml"
        path.write_text(yaml.dump(sample_scenario.model_dump(mode="json")))
        loaded = load_scenario(path)
        assert loaded.id == "smoke_test"
        assert len(loaded.checks) == 3

    def test_load_scenarios_dir(self, tmp_path: Path, sample_scenario: Scenario) -> None:
        path = tmp_path / "smoke_test.yaml"
        path.write_text(yaml.dump(sample_scenario.model_dump(mode="json")))
        scenarios = load_scenarios(str(tmp_path))
        assert len(scenarios) == 1
        assert scenarios[0].id == "smoke_test"

    def test_filter_by_ids(self, tmp_path: Path, sample_scenario: Scenario) -> None:
        for sid in ["test_a", "test_b", "test_c"]:
            scenario = sample_scenario.model_copy(update={"id": sid})
            path = tmp_path / f"{sid}.yaml"
            path.write_text(yaml.dump(scenario.model_dump(mode="json")))

        scenarios = load_scenarios(str(tmp_path), ["test_a", "test_c"])
        assert len(scenarios) == 2
        ids = {s.id for s in scenarios}
        assert ids == {"test_a", "test_c"}

    def test_missing_scenario_id(self, tmp_path: Path, sample_scenario: Scenario) -> None:
        path = tmp_path / "test.yaml"
        path.write_text(yaml.dump(sample_scenario.model_dump(mode="json")))
        with pytest.raises(ValueError, match="not found"):
            load_scenarios(str(tmp_path), ["nonexistent"])

    def test_missing_dir(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_scenarios("/nonexistent/path")


class TestReadTrace:
    def test_read_jsonl(self, tmp_path: Path, sample_spans: list[Span]) -> None:
        trace_path = tmp_path / "trace.jsonl"
        with open(trace_path, "w") as f:
            for span in sample_spans:
                f.write(span.model_dump_json() + "\n")

        loaded = read_trace(str(trace_path))
        assert len(loaded) == 2
        assert loaded[0].trace_id == "trace_001"
        assert loaded[0].kind == SpanKind.LLM

    def test_read_skips_blank_lines(self, tmp_path: Path, sample_llm_span: Span) -> None:
        trace_path = tmp_path / "trace.jsonl"
        with open(trace_path, "w") as f:
            f.write(sample_llm_span.model_dump_json() + "\n")
            f.write("\n")  # blank line
            f.write("  \n")  # whitespace-only line

        loaded = read_trace(str(trace_path))
        assert len(loaded) == 1


class TestReadSpans:
    def test_reads_oi_spans(self, tmp_path: Path, sample_oi_span: dict) -> None:
        spans_file = tmp_path / "spans.jsonl"
        spans_file.write_text(json.dumps(sample_oi_span) + "\n")
        spans = _read_spans(tmp_path)
        assert len(spans) == 1
        assert spans[0].kind == SpanKind.LLM

    def test_empty_dir(self, tmp_path: Path) -> None:
        spans = _read_spans(tmp_path)
        assert spans == []

    def test_skips_blank_lines(self, tmp_path: Path, sample_oi_span: dict) -> None:
        spans_file = tmp_path / "spans.jsonl"
        spans_file.write_text(json.dumps(sample_oi_span) + "\n\n")
        spans = _read_spans(tmp_path)
        assert len(spans) == 1


class TestWriteTrace:
    def test_writes_jsonl(self, tmp_path: Path, sample_spans: list[Span]) -> None:
        output = tmp_path / "nested" / "trace.jsonl"
        _write_trace(sample_spans, output)
        assert output.exists()
        lines = output.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_creates_parent_dirs(self, tmp_path: Path, sample_llm_span: Span) -> None:
        output = tmp_path / "a" / "b" / "c" / "trace.jsonl"
        _write_trace([sample_llm_span], output)
        assert output.exists()


class TestRunScenario:
    def test_run_scenario_no_traces_raises(self, tmp_path: Path) -> None:
        """Scenario that produces no traces should raise RuntimeError."""
        from kensa.runner import run_scenario

        scenario = Scenario(
            id="empty",
            name="Empty test",
            run_command=["echo", "hello"],
            input="test",
        )
        with pytest.raises(RuntimeError, match="produced no traces"):
            run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=5)

    def test_run_scenario_no_traces_includes_stderr(self, tmp_path: Path) -> None:
        """RuntimeError should include stderr when subprocess fails."""
        from kensa.runner import run_scenario

        scenario = Scenario(
            id="failing",
            name="Failing test",
            run_command=["python3", "-c"],
            input="import sys; print('boom', file=sys.stderr); sys.exit(1)",
        )
        with pytest.raises(RuntimeError, match="boom"):
            run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=5)

    def test_run_scenario_timeout(self, tmp_path: Path) -> None:
        """Scenario that times out should raise with timeout message."""
        from kensa.runner import run_scenario

        scenario = Scenario(
            id="slow",
            name="Slow test",
            run_command=["sleep", "60"],
        )
        with pytest.raises(RuntimeError, match="timed out"):
            run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=1)

    def test_run_scenario_with_env_overrides(self, tmp_path: Path) -> None:
        """Scenario with env_overrides passes them to subprocess."""
        from kensa.runner import run_scenario

        scenario = Scenario(
            id="env_test",
            name="Env test",
            run_command=["env"],
            env_overrides={"MY_VAR": "hello"},
        )
        # Still fails because no traces, but tests the env path
        with pytest.raises(RuntimeError, match="produced no traces"):
            run_scenario(scenario, trace_dir=str(tmp_path / "traces"), timeout=5)


class TestLoadDataset:
    def test_reads_jsonl(self, tmp_path: Path) -> None:
        dataset = tmp_path / "data.jsonl"
        dataset.write_text('{"query": "q1"}\n{"query": "q2"}\n{"query": "q3"}\n')
        rows = load_dataset(tmp_path, "data.jsonl")
        assert len(rows) == 3
        assert rows[0]["query"] == "q1"
        assert rows[2]["query"] == "q3"

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        dataset = tmp_path / "data.jsonl"
        dataset.write_text('{"q": "a"}\n\n{"q": "b"}\n  \n')
        rows = load_dataset(tmp_path, "data.jsonl")
        assert len(rows) == 2

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_dataset(tmp_path, "nonexistent.jsonl")

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        dataset = tmp_path / "empty.jsonl"
        dataset.write_text("")
        rows = load_dataset(tmp_path, "empty.jsonl")
        assert rows == []

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="escapes scenario directory"):
            load_dataset(tmp_path, "../../etc/passwd")


class TestRunIdPrecision:
    def test_run_id_longer_than_seconds(self) -> None:
        import tempfile

        from kensa.runner import run_scenarios

        with tempfile.TemporaryDirectory() as d:
            scenario_dir = Path(d) / "scenarios"
            scenario_dir.mkdir()
            m = run_scenarios(scenario_dir=str(scenario_dir))
        assert len(m.run_id) == 18


class TestDatasetRowErrors:
    def test_missing_input_field_raises_with_context(self, tmp_path: Path) -> None:
        from kensa.runner import run_scenarios

        scenario_dir = tmp_path / "scenarios"
        scenario_dir.mkdir()
        scenario_file = scenario_dir / "test.yaml"
        scenario_file.write_text(
            "id: test\nname: test\nrun_command: [echo]\ndataset: data.jsonl\ninput_field: ticket\n"
        )
        dataset = scenario_dir / "data.jsonl"
        dataset.write_text('{"wrong_field": "hello"}\n')
        with pytest.raises(KeyError, match=r"Row 1.*missing field.*ticket"):
            run_scenarios(scenario_dir=str(scenario_dir))

    def test_missing_field_shows_available_keys(self, tmp_path: Path) -> None:
        from kensa.runner import run_scenarios

        scenario_dir = tmp_path / "scenarios"
        scenario_dir.mkdir()
        scenario_file = scenario_dir / "test.yaml"
        scenario_file.write_text(
            "id: test\nname: test\nrun_command: [echo]\ndataset: data.jsonl\ninput_field: ticket\n"
        )
        dataset = scenario_dir / "data.jsonl"
        dataset.write_text('{"question": "hi", "expected": "hello"}\n')
        with pytest.raises(KeyError, match=r"Available.*question.*expected"):
            run_scenarios(scenario_dir=str(scenario_dir))


class TestDatasetRowPassedThrough:
    def test_dataset_row_stored_in_scenario_run(self, tmp_path: Path) -> None:
        from kensa.runner import run_scenarios

        scenario_dir = tmp_path / "scenarios"
        scenario_dir.mkdir()
        scenario_file = scenario_dir / "test.yaml"
        scenario_file.write_text(
            "id: test\nname: test\nrun_command: [echo]\ndataset: data.jsonl\ninput_field: ticket\n"
        )
        dataset = scenario_dir / "data.jsonl"
        dataset.write_text(
            '{"ticket": "SSO down", "expected": "P1"}\n'
            '{"ticket": "Add dark mode", "expected": "P3"}\n'
        )
        manifest = run_scenarios(scenario_dir=str(scenario_dir))
        runs = manifest.scenarios["test"]
        assert len(runs) == 2
        assert runs[0].dataset_row == {"ticket": "SSO down", "expected": "P1"}
        assert runs[1].dataset_row == {"ticket": "Add dark mode", "expected": "P3"}
        assert runs[0].input == "SSO down"


class TestRunScenarios:
    def test_run_scenarios_empty(self, tmp_path: Path) -> None:
        """Running with an empty scenario dir produces empty manifest."""
        from kensa.runner import run_scenarios

        scenario_dir = tmp_path / "scenarios"
        scenario_dir.mkdir()
        manifest = run_scenarios(scenario_dir=str(scenario_dir))
        assert len(manifest.scenarios) == 0
        assert manifest.run_id is not None
