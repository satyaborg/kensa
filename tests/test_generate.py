"""Tests for kensa.generate and the `kensa generate` CLI command."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from kensa.cli import cli
from kensa.generate import (
    InvalidScenarioIdError,
    _build_prompt,
    _parse_response,
    _scenario_to_yaml,
    _validate_scenario_id,
    collect_run_commands,
    generate_from_traces,
    resolve_trace_paths,
    write_scenarios,
)
from kensa.models import (
    CostInfo,
    RunManifest,
    Scenario,
    ScenarioRun,
    Span,
    SpanKind,
    TokenCounts,
)


def _make_span(
    *,
    trace_id: str = "t1",
    span_id: str = "s1",
    kind: SpanKind = SpanKind.LLM,
    input_text: str = "What's the weather in Tokyo?",
    output_text: str = "It's 22°C and sunny in Tokyo.",
    input_shape: str = "messages",
) -> Span:
    now = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    if input_shape == "value":
        input_payload = {
            "value": json.dumps(
                {"messages": [{"role": "user", "content": input_text}], "model": "claude-haiku"}
            )
        }
    else:
        input_payload = {"messages": [{"role": "user", "content": input_text}]}
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        name="llm.call",
        kind=kind,
        start_time=now,
        end_time=now,
        model="claude-sonnet-4-6",
        input=input_payload,
        output={"messages": [{"role": "assistant", "content": output_text}]},
        tokens=TokenCounts(prompt=100, completion=50, total=150),
        cost=CostInfo(prompt=0.001, completion=0.002, total=0.003),
    )


def _write_trace(path: Path, spans: list[Span]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(s.model_dump_json() for s in spans) + "\n")


def _valid_scenario_dict(sid: str = "weather_happy") -> dict:
    return {
        "id": sid,
        "name": "Weather happy path",
        "description": "Basic weather query returns temperature.",
        "source": "traces",
        "input": "What's the weather in Tokyo?",
        "run_command": ["python", "agent.py"],
        "expected_outcome": "Agent returns current weather.",
        "checks": [
            {
                "type": "output_contains",
                "params": {"value": "Tokyo"},
                "description": "Mentions city",
            },
            {"type": "max_turns", "params": {"max": 5}, "description": "Under 5 LLM calls"},
            {"type": "max_cost", "params": {"max": 0.10}, "description": "Under 10 cents"},
        ],
        "criteria": "The agent should return a clear weather report for Tokyo.",
    }


class _FakeCompleter:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str | None]] = []

    def complete(self, prompt: str, *, response_format: str | None = None) -> str:
        self.calls.append((prompt, response_format))
        return self.payload


class TestParseResponse:
    def test_parses_scenarios_wrapper(self) -> None:
        raw = json.dumps({"scenarios": [_valid_scenario_dict()]})
        assert len(_parse_response(raw)) == 1

    def test_parses_bare_array(self) -> None:
        raw = json.dumps([_valid_scenario_dict(), _valid_scenario_dict("other")])
        assert len(_parse_response(raw)) == 2

    def test_strips_markdown_fences(self) -> None:
        raw = f"```json\n{json.dumps([_valid_scenario_dict()])}\n```"
        assert len(_parse_response(raw)) == 1

    def test_rejects_non_json(self) -> None:
        with pytest.raises(ValueError, match="valid JSON"):
            _parse_response("not json at all")

    def test_rejects_wrong_shape(self) -> None:
        with pytest.raises(ValueError, match="list"):
            _parse_response(json.dumps({"scenarios": {"not": "a list"}}))


class TestGenerateFromTraces:
    def test_parses_valid_scenarios(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        fake = _FakeCompleter(json.dumps({"scenarios": [_valid_scenario_dict()]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        scenarios = generate_from_traces([trace], count=1)
        assert len(scenarios) == 1
        assert scenarios[0].id == "weather_happy"
        assert scenarios[0].source.value == "traces"
        assert fake.calls
        assert fake.calls[0][1] == "json"

    def test_rejects_invalid_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        fake = _FakeCompleter("definitely not json")
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        with pytest.raises(ValueError, match="valid JSON"):
            generate_from_traces([trace], count=1)

    def test_rejects_empty_list(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="at least one trace"):
            generate_from_traces([], count=1)

    def test_rejects_zero_scenarios(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        fake = _FakeCompleter(json.dumps({"scenarios": []}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        with pytest.raises(ValueError, match="zero scenarios"):
            generate_from_traces([trace], count=1)

    def test_partial_validation_failure_keeps_good_scenarios(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        good = _valid_scenario_dict("good")
        bad = {
            "id": "bad",
            "name": "Bad",
            "run_command": ["python", "agent.py"],
            "checks": [{"type": "tools_not_called", "params": {"tools": []}}],
        }
        fake = _FakeCompleter(json.dumps({"scenarios": [good, bad]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        scenarios = generate_from_traces([trace], count=2)
        assert [s.id for s in scenarios] == ["good"]

    def test_raises_when_all_scenarios_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        bad = {
            "id": "bad",
            "name": "Bad",
            "run_command": ["python", "agent.py"],
            "checks": [{"type": "tools_not_called", "params": {"tools": []}}],
        }
        fake = _FakeCompleter(json.dumps({"scenarios": [bad]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        with pytest.raises(ValueError, match="failed validation"):
            generate_from_traces([trace], count=1)


class TestGeneratorValidation:
    """Generator-specific invariants beyond Scenario.model_validate."""

    def test_rejects_empty_run_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        sd = _valid_scenario_dict("no_cmd")
        sd["run_command"] = []
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        with pytest.raises(ValueError, match="run_command is empty"):
            generate_from_traces([trace], count=1)

    def test_rejects_no_checks_and_no_criteria(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        sd = _valid_scenario_dict("noop")
        sd["checks"] = []
        sd["criteria"] = None
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        with pytest.raises(ValueError, match="no checks and no judge criterion"):
            generate_from_traces([trace], count=1)

    def test_rejects_missing_cost_and_turn_bounds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        sd = _valid_scenario_dict("unbounded")
        sd["checks"] = [
            {"type": "output_contains", "params": {"value": "Tokyo"}, "description": "x"},
        ]
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        with pytest.raises(ValueError, match="max_cost or max_turns"):
            generate_from_traces([trace], count=1)

    def test_rejects_judge_file_reference(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """generate does not write judge prompt files, so judge: refs would dangle."""
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        sd = _valid_scenario_dict("judge_ref")
        sd.pop("criteria", None)
        sd["judge"] = "some_judge_name"
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        with pytest.raises(ValueError, match="must use 'criteria'"):
            generate_from_traces([trace], count=1)

    def test_accepts_criteria_only_scenario(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scenario with criteria but no deterministic checks still needs bounds."""
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        sd = _valid_scenario_dict("criteria_only")
        sd["checks"] = [{"type": "max_turns", "params": {"max": 5}, "description": "bound"}]
        sd["criteria"] = "Agent responds politely."
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        scenarios = generate_from_traces([trace], count=1)
        assert scenarios[0].id == "criteria_only"


class TestGenerateCountEnforcement:
    def test_caps_overproduction_to_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        dicts = [_valid_scenario_dict(f"s{i}") for i in range(5)]
        fake = _FakeCompleter(json.dumps({"scenarios": dicts}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        with pytest.warns(UserWarning, match="capping"):
            scenarios = generate_from_traces([trace], count=2)
        assert len(scenarios) == 2
        assert [s.id for s in scenarios] == ["s0", "s1"]

    def test_deduplicates_ids(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        first = _valid_scenario_dict("dup")
        first["name"] = "first"
        second = _valid_scenario_dict("dup")
        second["name"] = "second"
        fake = _FakeCompleter(json.dumps({"scenarios": [first, second]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        scenarios = generate_from_traces([trace], count=2)
        assert len(scenarios) == 1
        assert scenarios[0].name == "first"


class TestGeneratedCheckParams:
    """Generator-strict check param validation (beyond Check.model_validator)."""

    def _with_checks(self, checks: list[dict]) -> dict:
        sd = _valid_scenario_dict("check_test")
        sd["checks"] = checks
        return sd

    def test_rejects_max_turns_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])
        sd = self._with_checks([{"type": "max_turns", "params": {"max": "5"}}])
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)
        with pytest.raises(ValueError, match=r"max_turns.*numeric"):
            generate_from_traces([trace], count=1)

    def test_rejects_max_cost_bool(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])
        sd = self._with_checks(
            [
                {"type": "max_cost", "params": {"max": True}},
                {"type": "max_turns", "params": {"max": 5}},
            ]
        )
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)
        with pytest.raises(ValueError, match=r"max_cost.*numeric"):
            generate_from_traces([trace], count=1)

    def test_rejects_empty_output_contains_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])
        sd = self._with_checks(
            [
                {"type": "output_contains", "params": {"value": ""}},
                {"type": "max_turns", "params": {"max": 5}},
            ]
        )
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)
        with pytest.raises(ValueError, match=r"output_contains.*non-empty"):
            generate_from_traces([trace], count=1)


class TestRunCommandEnforcement:
    def test_rewrites_run_command_when_single_observed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With one observed command, the LLM's run_command gets overwritten silently."""
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        sd = _valid_scenario_dict("drift")
        sd["run_command"] = ["python", "wrong.py"]
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        observed = ["python", ".kensa/agents/real.py"]
        scenarios = generate_from_traces([trace], count=1, run_commands=[observed])
        assert scenarios[0].run_command == observed

    def test_rejects_run_command_not_in_multi_allowlist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        sd = _valid_scenario_dict("off_allowlist")
        sd["run_command"] = ["python", "hallucinated.py"]
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        allowlist = [["python", "a.py"], ["python", "b.py"]]
        with pytest.raises(ValueError, match="not in observed entrypoints"):
            generate_from_traces([trace], count=1, run_commands=allowlist)

    def test_accepts_run_command_in_multi_allowlist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        sd = _valid_scenario_dict("on_allowlist")
        sd["run_command"] = ["python", "b.py"]
        fake = _FakeCompleter(json.dumps({"scenarios": [sd]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        allowlist = [["python", "a.py"], ["python", "b.py"]]
        scenarios = generate_from_traces([trace], count=1, run_commands=allowlist)
        assert scenarios[0].run_command == ["python", "b.py"]


class TestUnderproductionWarning:
    def test_warns_when_fewer_valid_than_requested(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        good = _valid_scenario_dict("good")
        bad = _valid_scenario_dict("bad")
        bad["run_command"] = []
        fake = _FakeCompleter(json.dumps({"scenarios": [good, bad]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        with pytest.warns(UserWarning, match="only 1 returned"):
            scenarios = generate_from_traces([trace], count=2)
        assert len(scenarios) == 1

    def test_warns_on_clean_underproduction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the LLM returns fewer valid scenarios than requested, warn even with no rejections."""
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        fake = _FakeCompleter(json.dumps({"scenarios": [_valid_scenario_dict("only_one")]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        with pytest.warns(UserWarning, match="fewer than requested"):
            scenarios = generate_from_traces([trace], count=2)
        assert len(scenarios) == 1


class TestWriteScenarios:
    def test_writes_new_files(self, tmp_path: Path) -> None:
        scenario = Scenario(**_valid_scenario_dict())
        written, skipped = write_scenarios([scenario], scenario_dir=tmp_path)
        assert len(written) == 1
        assert skipped == []
        data = yaml.safe_load(written[0].read_text())
        assert data["id"] == "weather_happy"
        assert data["run_command"] == ["python", "agent.py"]

    def test_skips_existing_without_force(self, tmp_path: Path) -> None:
        scenario = Scenario(**_valid_scenario_dict())
        write_scenarios([scenario], scenario_dir=tmp_path)

        modified = tmp_path / "weather_happy.yaml"
        modified.write_text("id: weather_happy\nname: user-edited\nrun_command: [x]\n")

        written, skipped = write_scenarios([scenario], scenario_dir=tmp_path)
        assert written == []
        assert len(skipped) == 1
        assert "user-edited" in modified.read_text()

    def test_force_overwrites(self, tmp_path: Path) -> None:
        scenario = Scenario(**_valid_scenario_dict())
        write_scenarios([scenario], scenario_dir=tmp_path)

        target = tmp_path / "weather_happy.yaml"
        target.write_text("old content")

        written, skipped = write_scenarios([scenario], scenario_dir=tmp_path, force=True)
        assert len(written) == 1
        assert skipped == []
        assert "old content" not in target.read_text()

    def test_yaml_roundtrips_through_loader(self, tmp_path: Path) -> None:
        from kensa.runner import load_scenario

        scenario = Scenario(**_valid_scenario_dict())
        written, _ = write_scenarios([scenario], scenario_dir=tmp_path)
        loaded = load_scenario(written[0])
        assert loaded.id == scenario.id
        assert loaded.run_command == scenario.run_command


class TestResolveTracePaths:
    def test_explicit_traces_take_priority(self, tmp_path: Path) -> None:
        t1 = tmp_path / "a.jsonl"
        t1.write_text("")
        t2 = tmp_path / "b.jsonl"
        t2.write_text("")
        paths = resolve_trace_paths(run_id=None, traces=(t1, t2))
        assert paths == [t1, t2]

    def test_run_id_reads_manifest(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            manifest = RunManifest(
                run_id="r1",
                timestamp=datetime(2026, 4, 22, tzinfo=timezone.utc),
                scenarios={
                    "s1": [
                        ScenarioRun(
                            trace_path=".kensa/traces/s1.jsonl",
                            exit_code=0,
                            duration_seconds=1.0,
                        )
                    ]
                },
            )
            (runs_dir / "r1.json").write_text(manifest.model_dump_json())
            paths = resolve_trace_paths(run_id="r1", traces=())
            assert paths == [Path(".kensa/traces/s1.jsonl")]

    def test_no_runs_raises(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with (
            runner.isolated_filesystem(temp_dir=tmp_path),
            pytest.raises(FileNotFoundError),
        ):
            resolve_trace_paths(run_id=None, traces=())

    def test_manifest_without_traces_raises(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            manifest = RunManifest(
                run_id="r1",
                timestamp=datetime(2026, 4, 22, tzinfo=timezone.utc),
                scenarios={
                    "s1": [
                        ScenarioRun(
                            trace_path="",
                            exit_code=-1,
                            duration_seconds=0.0,
                            stderr="crashed",
                        )
                    ]
                },
            )
            (runs_dir / "r1.json").write_text(manifest.model_dump_json())
            with pytest.raises(FileNotFoundError, match="no trace files"):
                resolve_trace_paths(run_id="r1", traces=())


class TestFirstUserInput:
    def test_extracts_from_messages_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span(input_text="find the weather", input_shape="messages")])

        captured: dict[str, str] = {}

        class Capture:
            def complete(self, prompt: str, *, response_format: str | None = None) -> str:
                captured["prompt"] = prompt
                return json.dumps({"scenarios": [_valid_scenario_dict()]})

        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: Capture())
        generate_from_traces([trace], count=1)
        assert "find the weather" in captured["prompt"]

    def test_extracts_from_value_shape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span(input_text="triage this ticket", input_shape="value")])

        captured: dict[str, str] = {}

        class Capture:
            def complete(self, prompt: str, *, response_format: str | None = None) -> str:
                captured["prompt"] = prompt
                return json.dumps({"scenarios": [_valid_scenario_dict()]})

        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: Capture())
        generate_from_traces([trace], count=1)
        assert "triage this ticket" in captured["prompt"]


class TestCollectRunCommands:
    def test_returns_unique_commands_from_manifest_scenarios(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            scenario_dir = Path(".kensa/scenarios")
            scenario_dir.mkdir(parents=True)

            (scenario_dir / "s1.yaml").write_text(
                "id: s1\nname: S1\nrun_command: [python, agents/a.py]\n"
            )
            (scenario_dir / "s2.yaml").write_text(
                "id: s2\nname: S2\nrun_command: [python, agents/a.py]\n"
            )
            (scenario_dir / "s3.yaml").write_text(
                "id: s3\nname: S3\nrun_command: [python, agents/b.py]\n"
            )

            manifest = RunManifest(
                run_id="r1",
                timestamp=datetime(2026, 4, 22, tzinfo=timezone.utc),
                scenarios={
                    "s1": [ScenarioRun(trace_path="t/1.jsonl", exit_code=0, duration_seconds=1.0)],
                    "s2": [ScenarioRun(trace_path="t/2.jsonl", exit_code=0, duration_seconds=1.0)],
                    "s3": [ScenarioRun(trace_path="t/3.jsonl", exit_code=0, duration_seconds=1.0)],
                },
            )
            (runs_dir / "r1.json").write_text(manifest.model_dump_json())

            commands = collect_run_commands(run_id="r1", scenario_dir=scenario_dir)
            assert sorted(commands) == [["python", "agents/a.py"], ["python", "agents/b.py"]]

    def test_matches_scenario_by_id_not_filename(self, tmp_path: Path) -> None:
        """A scenario whose filename differs from its id must still be found."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            scenario_dir = Path(".kensa/scenarios")
            scenario_dir.mkdir(parents=True)

            (scenario_dir / "smoke.yaml").write_text(
                "id: weather_happy\nname: S\nrun_command: [python, agents/a.py]\n"
            )

            manifest = RunManifest(
                run_id="r1",
                timestamp=datetime(2026, 4, 22, tzinfo=timezone.utc),
                scenarios={
                    "weather_happy": [
                        ScenarioRun(trace_path="t/1.jsonl", exit_code=0, duration_seconds=1.0)
                    ],
                },
            )
            (runs_dir / "r1.json").write_text(manifest.model_dump_json())

            assert collect_run_commands(run_id="r1", scenario_dir=scenario_dir) == [
                ["python", "agents/a.py"]
            ]

    def test_falls_back_to_manifest_lookup_by_trace_path(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            scenario_dir = Path(".kensa/scenarios")
            scenario_dir.mkdir(parents=True)
            traces_dir = Path(".kensa/traces")
            traces_dir.mkdir(parents=True)

            trace_file = traces_dir / "s1.jsonl"
            trace_file.write_text("")
            (scenario_dir / "s1.yaml").write_text(
                "id: s1\nname: S1\nrun_command: [python, agents/entry.py]\n"
            )

            manifest = RunManifest(
                run_id="r1",
                timestamp=datetime(2026, 4, 22, tzinfo=timezone.utc),
                scenarios={
                    "s1": [
                        ScenarioRun(
                            trace_path=str(trace_file),
                            exit_code=0,
                            duration_seconds=1.0,
                        )
                    ]
                },
            )
            (runs_dir / "r1.json").write_text(manifest.model_dump_json())

            commands = collect_run_commands(
                run_id=None,
                scenario_dir=scenario_dir,
                trace_paths=[trace_file],
            )
            assert commands == [["python", "agents/entry.py"]]

    def test_returns_empty_when_no_manifest(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            assert collect_run_commands(run_id=None, scenario_dir=Path(".kensa/scenarios")) == []


class TestValidateScenarioId:
    def test_accepts_snake_case(self) -> None:
        assert _validate_scenario_id("weather_happy") == "weather_happy"
        assert _validate_scenario_id("a1-b2_c3") == "a1-b2_c3"

    def test_rejects_path_separator(self) -> None:
        with pytest.raises(InvalidScenarioIdError):
            _validate_scenario_id("../judges/foo")

    def test_rejects_leading_dot(self) -> None:
        with pytest.raises(InvalidScenarioIdError):
            _validate_scenario_id(".hidden")

    def test_rejects_whitespace(self) -> None:
        with pytest.raises(InvalidScenarioIdError):
            _validate_scenario_id("bad name")

    def test_rejects_empty(self) -> None:
        with pytest.raises(InvalidScenarioIdError):
            _validate_scenario_id("")


class TestWriteScenariosRejectsBadIds:
    def test_write_rejects_traversal(self, tmp_path: Path) -> None:
        """Even if a Scenario object is hand-built, write_scenarios must refuse."""
        scenario = Scenario.model_construct(
            id="../evil",
            name="evil",
            run_command=["echo", "x"],
        )
        with pytest.raises(InvalidScenarioIdError):
            write_scenarios([scenario], scenario_dir=tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_generate_drops_scenarios_with_unsafe_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace = tmp_path / "t.jsonl"
        _write_trace(trace, [_make_span()])

        good = _valid_scenario_dict("good")
        evil = _valid_scenario_dict("../evil")
        fake = _FakeCompleter(json.dumps({"scenarios": [good, evil]}))
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

        scenarios = generate_from_traces([trace], count=2)
        assert [s.id for s in scenarios] == ["good"]


class TestBuildPrompt:
    def test_includes_run_commands_block_when_provided(self) -> None:
        prompt = _build_prompt(
            summaries=[{"path": "t.jsonl", "input": "x"}],
            count=1,
            run_commands=[["python", ".kensa/agents/example.py"]],
        )
        assert "Observed run_commands" in prompt
        assert ".kensa/agents/example.py" in prompt

    def test_omits_run_commands_block_when_absent(self) -> None:
        prompt = _build_prompt(
            summaries=[{"path": "t.jsonl", "input": "x"}], count=1, run_commands=None
        )
        assert "Observed run_commands" not in prompt


class TestScenarioToYaml:
    def test_drops_empty_optionals(self) -> None:
        scenario = Scenario(**_valid_scenario_dict())
        yaml_text = _scenario_to_yaml(scenario)
        data = yaml.safe_load(yaml_text)
        assert "dataset" not in data
        assert "input_field" not in data
        assert "judge" not in data
        assert "trace_refs" not in data


class TestCliGenerate:
    def test_uses_latest_run_when_no_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            traces_dir = Path(".kensa/traces")
            traces_dir.mkdir(parents=True)
            scenarios_dir = Path(".kensa/scenarios")
            scenarios_dir.mkdir(parents=True)

            trace_path = traces_dir / "s1.jsonl"
            _write_trace(trace_path, [_make_span()])

            manifest = RunManifest(
                run_id="20260422T120000",
                timestamp=datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
                scenarios={
                    "s1": [
                        ScenarioRun(
                            trace_path=str(trace_path),
                            exit_code=0,
                            duration_seconds=1.0,
                        )
                    ]
                },
            )
            (runs_dir / "20260422T120000.json").write_text(manifest.model_dump_json())

            fake = _FakeCompleter(json.dumps({"scenarios": [_valid_scenario_dict()]}))
            monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

            result = runner.invoke(cli, ["generate", "-n", "1"])
            assert result.exit_code == 0, result.output
            assert "1 written" in result.output
            assert (scenarios_dir / "weather_happy.yaml").exists()

    def test_dry_run_does_not_write(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            traces_dir = Path("traces")
            traces_dir.mkdir()
            scenarios_dir = Path(".kensa/scenarios")
            scenarios_dir.mkdir(parents=True)

            trace_path = traces_dir / "s1.jsonl"
            _write_trace(trace_path, [_make_span()])

            fake = _FakeCompleter(json.dumps({"scenarios": [_valid_scenario_dict()]}))
            monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

            result = runner.invoke(
                cli,
                ["generate", "--trace", str(trace_path), "-n", "1", "--dry-run"],
            )
            assert result.exit_code == 0, result.output
            assert "weather_happy" in result.output
            assert list(scenarios_dir.iterdir()) == []

    def test_errors_when_no_runs(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["generate"])
            assert result.exit_code == 1
            assert "Error" in result.output

    def test_explicit_trace_bypasses_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            traces_dir = Path("traces")
            traces_dir.mkdir()
            scenarios_dir = Path(".kensa/scenarios")
            scenarios_dir.mkdir(parents=True)

            trace_path = traces_dir / "s1.jsonl"
            _write_trace(trace_path, [_make_span()])

            fake = _FakeCompleter(json.dumps({"scenarios": [_valid_scenario_dict()]}))
            monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

            result = runner.invoke(
                cli,
                ["generate", "--trace", str(trace_path), "-n", "1"],
            )
            assert result.exit_code == 0, result.output
            assert (scenarios_dir / "weather_happy.yaml").exists()

    def test_rejects_bad_run_id(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["generate", "--run-id", "../evil"])
            assert result.exit_code != 0
            assert "Invalid run ID" in result.output

    def test_run_command_flag_seeds_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--run-command overrides LLM guessing even when no manifest is present."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            traces_dir = Path("traces")
            traces_dir.mkdir()
            Path(".kensa/scenarios").mkdir(parents=True)

            trace_path = traces_dir / "s1.jsonl"
            _write_trace(trace_path, [_make_span()])

            captured: dict[str, str] = {}

            class Capture:
                def complete(self, prompt: str, *, response_format: str | None = None) -> str:
                    captured["prompt"] = prompt
                    return json.dumps({"scenarios": [_valid_scenario_dict()]})

            monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: Capture())

            result = runner.invoke(
                cli,
                [
                    "generate",
                    "--trace",
                    str(trace_path),
                    "-n",
                    "1",
                    "--run-command",
                    "python .kensa/agents/real.py",
                ],
            )
            assert result.exit_code == 0, result.output
            assert "Observed run_commands" in captured["prompt"]
            assert ".kensa/agents/real.py" in captured["prompt"]

    def test_warns_when_no_run_command_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            traces_dir = Path("traces")
            traces_dir.mkdir()
            Path(".kensa/scenarios").mkdir(parents=True)

            trace_path = traces_dir / "s1.jsonl"
            _write_trace(trace_path, [_make_span()])

            fake = _FakeCompleter(json.dumps({"scenarios": [_valid_scenario_dict()]}))
            monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: fake)

            result = runner.invoke(
                cli,
                ["generate", "--trace", str(trace_path), "-n", "1", "--dry-run"],
            )
            assert result.exit_code == 0, result.output
            assert "LLM may hallucinate" in result.output

    def test_scenario_dir_separated_from_source_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--scenario-dir=out must still pick up run_command from .kensa/scenarios."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            traces_dir = Path(".kensa/traces")
            traces_dir.mkdir(parents=True)
            source_dir = Path(".kensa/scenarios")
            source_dir.mkdir(parents=True)
            out_dir = Path("out")

            trace_path = traces_dir / "s1.jsonl"
            _write_trace(trace_path, [_make_span()])

            (source_dir / "seed.yaml").write_text(
                "id: seed\nname: Seed\nrun_command: [python, agents/real.py]\n"
            )
            manifest = RunManifest(
                run_id="20260423T120000",
                timestamp=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
                scenarios={
                    "seed": [
                        ScenarioRun(
                            trace_path=str(trace_path),
                            exit_code=0,
                            duration_seconds=1.0,
                        )
                    ]
                },
            )
            (runs_dir / "20260423T120000.json").write_text(manifest.model_dump_json())

            captured: dict[str, str] = {}

            class Capture:
                def complete(self, prompt: str, *, response_format: str | None = None) -> str:
                    captured["prompt"] = prompt
                    return json.dumps({"scenarios": [_valid_scenario_dict()]})

            monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: Capture())

            result = runner.invoke(
                cli,
                ["generate", "-n", "1", "--scenario-dir", str(out_dir)],
            )
            assert result.exit_code == 0, result.output
            assert "entrypoints: 1" in result.output
            assert "agents/real.py" in captured["prompt"]
            assert (out_dir / "weather_happy.yaml").exists()
            assert not list(source_dir.glob("weather_happy*")), (
                "scenario written into source dir instead of --scenario-dir"
            )
