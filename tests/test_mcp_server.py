"""Tests for the kensa MCP server.

Three layers:
  1. Unit tests — call tools and resources directly, isolated via ``tmp_path``.
  2. Integration — spin up an in-memory ``fastmcp.Client`` against the server.
  3. Coverage gate — enforced by the pytest --cov-fail-under gate.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")

import yaml
from click.testing import CliRunner
from fastmcp import Client
from fastmcp.exceptions import ResourceError, ToolError

from kensa.cli import cli
from kensa.mcp_server import (
    EvalSummary,
    JudgeSummary,
    MCPError,
    ReportResponse,
    RunSummary,
    _summarise_results,
    _validate_run_id,
    analyze,
    doctor,
    eval,
    init,
    judge,
    judge_detail,
    judges_list,
    main,
    mcp,
    report,
    run,
    run_detail,
    run_results,
    run_server,
    run_trace,
    runs_list,
    scenario_detail,
    scenarios_list,
)
from kensa.models import Result, ResultStatus, RunManifest, ScenarioRun

EXPECTED_TOOLS = {"init", "doctor", "run", "judge", "eval", "report", "analyze"}
EXPECTED_STATIC_RESOURCES = {"kensa://runs", "kensa://scenarios", "kensa://judges"}
EXPECTED_TEMPLATES = {
    "kensa://runs/{run_id}",
    "kensa://runs/{run_id}/results",
    "kensa://runs/{run_id}/trace/{scenario}/{index}",
    "kensa://scenarios/{scenario_id}",
    "kensa://judges/{name}",
}


def _isolated(tmp_path: Path):
    """Fresh cwd inside tmp_path — paths.* all resolve relative to this."""
    return CliRunner().isolated_filesystem(temp_dir=tmp_path)


def _write_scenario(scenario_dir: Path, scenario_id: str, **overrides: object) -> None:
    doc: dict[str, object] = {
        "id": scenario_id,
        "name": scenario_id.title(),
        "run_command": ["true"],
        "input": "x",
    }
    doc.update(overrides)
    scenario_dir.mkdir(parents=True, exist_ok=True)
    (scenario_dir / f"{scenario_id}.yaml").write_text(yaml.safe_dump(doc))


def _write_manifest(
    run_id: str,
    scenario_ids: list[str] | None = None,
    runs_per_scenario: int = 1,
) -> Path:
    run_dir = Path(".kensa/runs")
    trace_dir = Path(".kensa/traces")
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    sids = scenario_ids or ["s1"]
    scenarios: dict[str, list[ScenarioRun]] = {}
    for sid in sids:
        runs: list[ScenarioRun] = []
        for i in range(runs_per_scenario):
            suffix = "" if runs_per_scenario == 1 else f"-{i}"
            trace_file = trace_dir / f"{sid}{suffix}.jsonl"
            trace_file.write_text("")
            runs.append(
                ScenarioRun(
                    trace_path=str(trace_file),
                    exit_code=0,
                    duration_seconds=0.1,
                    dataset_row={"row": i} if runs_per_scenario > 1 else None,
                )
            )
        scenarios[sid] = runs
    manifest = RunManifest(
        run_id=run_id,
        timestamp=datetime(2026, 3, 1, tzinfo=timezone.utc),
        scenarios=scenarios,
    )
    path = run_dir / f"{run_id}.json"
    path.write_text(manifest.model_dump_json())
    return path


class TestSurface:
    def test_seven_tools(self) -> None:
        tools = asyncio.run(mcp.list_tools())
        assert {t.name for t in tools} == EXPECTED_TOOLS

    def test_three_static_resources(self) -> None:
        static = asyncio.run(mcp.list_resources())
        assert {str(r.uri) for r in static} == EXPECTED_STATIC_RESOURCES

    def test_five_resource_templates(self) -> None:
        templates = asyncio.run(mcp.list_resource_templates())
        assert {t.uri_template for t in templates} == EXPECTED_TEMPLATES

    def test_server_name_and_instructions(self) -> None:
        assert mcp.name == "kensa"
        assert mcp.instructions is not None
        assert "kensa" in mcp.instructions.lower()


class TestHelpers:
    @pytest.mark.parametrize("value", ["abc123", "2026-03-24T12", "run.42", "run_42"])
    def test_validate_run_id_accepts(self, value: str) -> None:
        assert _validate_run_id(value)

    @pytest.mark.parametrize("value", ["../evil", "a/b", "foo bar", ""])
    def test_validate_run_id_rejects(self, value: str) -> None:
        assert not _validate_run_id(value)

    def test_summarise_results(self) -> None:
        results = [
            Result(scenario_id="a", status=ResultStatus.PASS),
            Result(scenario_id="b", status=ResultStatus.FAIL),
            Result(scenario_id="c", status=ResultStatus.ERROR),
            Result(scenario_id="d", status=ResultStatus.UNCERTAIN),
        ]
        counts = _summarise_results(results)
        assert (counts.total, counts.passed, counts.failed, counts.errored, counts.uncertain) == (
            4,
            1,
            1,
            1,
            1,
        )


class TestInit:
    def test_blank_creates_dirs_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with _isolated(tmp_path):
            out = init(blank=True)
            assert Path(".kensa/scenarios").is_dir()
            assert not Path(".kensa/scenarios/example.yaml").exists()
        assert not isinstance(out, MCPError)
        assert len(out.directories_created) == 4
        assert out.files_written == []

    def test_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with _isolated(tmp_path):
            init()
            out = init()
        assert not isinstance(out, MCPError)
        assert out.directories_created == []
        assert out.example_already_existed is True


class TestDoctor:
    def test_shape(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            out = doctor()
        assert out.total == len(out.checks)
        assert out.passed == sum(1 for c in out.checks if c.ok)
        assert all(c.ok is False for c in out.failures)
        assert all(c.ok is False for c in out.hard_failures)
        assert {c.name for c in out.hard_failures}.issubset({c.name for c in out.failures})

    def test_single_provider_is_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Setting only one API key keeps a healthy env ``ready=True`` — the
        other provider's missing key is a soft failure, matching CLI semantics.
        """
        monkeypatch.setattr(
            "kensa.doctor.run_doctor",
            lambda: [
                ("python", True, "ok"),
                ("pkg manager", True, "uv"),
                ("scenarios", True, "1 scenario"),
                (".env file", True, ".env"),
                ("trace dir", True, "writable"),
                ("ANTHROPIC_API_KEY", True, "set"),
                ("OPENAI_API_KEY", False, "not set"),
                ("judge", True, "anthropic"),
            ],
        )
        out = doctor()
        assert out.ready is True
        assert out.hard_failures == []
        names = {f.name for f in out.failures}
        assert "OPENAI_API_KEY" in names

    def test_hard_failure_blocks_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A genuinely broken env (no scenarios dir, no .env) stays not-ready
        even with both API keys set — hard failures still gate readiness.
        """
        monkeypatch.setattr(
            "kensa.doctor.run_doctor",
            lambda: [
                ("python", True, "ok"),
                ("pkg manager", True, "uv"),
                ("scenarios", False, ".kensa/scenarios/ does not exist"),
                (".env file", False, "no .env found"),
                ("trace dir", True, "writable"),
                ("ANTHROPIC_API_KEY", True, "set"),
                ("OPENAI_API_KEY", True, "set"),
                ("judge", True, "anthropic"),
            ],
        )
        out = doctor()
        assert out.ready is False
        assert out.hard_failures
        hard_names = {c.name for c in out.hard_failures}
        assert "scenarios" in hard_names

    def test_sdk_failure_blocks_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SDK and judge failures are hard failures, matching CLI exit logic."""
        monkeypatch.setattr(
            "kensa.doctor.run_doctor",
            lambda: [
                ("python", True, "ok"),
                ("pkg manager", True, "uv"),
                ("scenarios", True, "1 scenario"),
                (".env file", True, ".env"),
                ("trace dir", True, "writable"),
                ("ANTHROPIC_API_KEY", False, "not set"),
                ("OPENAI_API_KEY", True, "set"),
                ("openai sdk", False, "instrumentor missing"),
                ("judge", True, "openai"),
            ],
        )
        out = doctor()
        assert out.ready is False
        hard_names = {c.name for c in out.hard_failures}
        assert "openai sdk" in hard_names


class TestRun:
    def test_missing_scenario_dir(self, tmp_path: Path) -> None:
        out = asyncio.run(run(scenario_dir=str(tmp_path / "missing")))
        assert isinstance(out, MCPError)
        assert out.code == "scenarios_missing"

    def test_unknown_scenario_id(self, tmp_path: Path) -> None:
        scenarios = tmp_path / "scenarios"
        _write_scenario(scenarios, "a")
        out = asyncio.run(run(scenario_dir=str(scenarios), scenario_ids=["ghost"]))
        assert isinstance(out, MCPError)
        assert out.code == "scenario_not_found"
        assert "ghost" in out.error

    def test_invalid_scenario_returns_scenario_invalid(self, tmp_path: Path) -> None:
        scenarios = tmp_path / "scenarios"
        _write_scenario(scenarios, "s1", dataset="rows.jsonl")
        out = asyncio.run(run(scenario_dir=str(scenarios)))
        assert isinstance(out, MCPError)
        assert out.code == "scenario_invalid"
        assert "input_field" in out.error

    def test_happy_path_no_spans(self, tmp_path: Path) -> None:
        """A trivial scenario runs but emits no spans. The runner records the
        failure inside the manifest; the tool still returns a RunSummary."""
        with _isolated(tmp_path):
            scenarios = Path(".kensa/scenarios")
            _write_scenario(scenarios, "s1")
            out = asyncio.run(run(scenario_dir=str(scenarios), scenario_ids=["s1"], timeout=5))
        assert isinstance(out, RunSummary)
        assert out.total == 1
        assert out.manifest_uri == f"kensa://runs/{out.run_id}"


class TestJudge:
    def test_missing_manifest(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            out = asyncio.run(judge())
        assert isinstance(out, MCPError)
        assert out.code == "run_not_found"

    def test_invalid_run_id(self) -> None:
        out = asyncio.run(judge(run_id="../evil"))
        assert isinstance(out, MCPError)
        assert out.code == "invalid_run_id"

    def test_no_judge_required(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            _write_scenario(Path(".kensa/scenarios"), "s1")
            _write_manifest("20260301T000000")
            out = asyncio.run(judge())
        assert isinstance(out, JudgeSummary)
        assert out.run_id == "20260301T000000"
        assert out.results_uri == "kensa://runs/20260301T000000/results"

    def test_malformed_yaml_returns_scenario_invalid(self, tmp_path: Path) -> None:
        """A malformed scenario YAML referenced by the manifest must surface as
        a typed MCPError(scenario_invalid), not bubble out as a generic error."""
        with _isolated(tmp_path):
            scenario_dir = Path(".kensa/scenarios")
            scenario_dir.mkdir(parents=True)
            (scenario_dir / "s1.yaml").write_text("{: not valid yaml")
            _write_manifest("20260301T000001", ["s1"])
            out = asyncio.run(judge())
        assert isinstance(out, MCPError)
        assert out.code == "scenario_invalid"

    def test_schema_violation_returns_scenario_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scenario whose YAML parses but fails model validation (e.g. dataset
        without input_field) must map to scenario_invalid."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        with _isolated(tmp_path):
            scenario_dir = Path(".kensa/scenarios")
            _write_scenario(
                scenario_dir,
                "s1",
                dataset="rows.jsonl",
                criteria="must pass",
            )
            _write_manifest("20260301T000002", ["s1"])
            out = asyncio.run(judge())
        assert isinstance(out, MCPError)
        assert out.code == "scenario_invalid"
        assert "input_field" in out.error

    def test_later_invalid_scenario_returns_scenario_invalid_after_judge_preflight(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid later scenarios must not escape after judge preflight passes."""
        monkeypatch.setattr("kensa.judge.get_judge", lambda model=None: None)
        with _isolated(tmp_path):
            scenario_dir = Path(".kensa/scenarios")
            _write_scenario(scenario_dir, "s1", criteria="must pass")
            (scenario_dir / "s2.yaml").write_text("{: not valid yaml")
            _write_manifest("20260301T000003", ["s1", "s2"])
            out = asyncio.run(judge())
        assert isinstance(out, MCPError)
        assert out.code == "scenario_invalid"


class TestEval:
    def test_missing_scenario_dir(self, tmp_path: Path) -> None:
        out = asyncio.run(eval(scenario_dir=str(tmp_path / "missing")))
        assert isinstance(out, MCPError)
        assert out.code == "scenarios_missing"

    def test_unknown_scenario_id(self, tmp_path: Path) -> None:
        scenarios = tmp_path / "scenarios"
        _write_scenario(scenarios, "a")
        out = asyncio.run(eval(scenario_dir=str(scenarios), scenario_ids=["ghost"]))
        assert isinstance(out, MCPError)
        assert out.code == "scenario_not_found"
        assert "ghost" in out.error

    def test_no_judge_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)
        with _isolated(tmp_path):
            scenarios = Path("scenarios")
            _write_scenario(scenarios, "s1", criteria="must pass")
            Path(".env").write_text("")
            out = asyncio.run(eval(scenario_dir=str(scenarios)))
        if isinstance(out, MCPError):
            assert out.code in {"no_judge_key", "internal"}

    def test_happy_path_without_judge(self, tmp_path: Path) -> None:
        """Scenario with no criteria → no judge required → full eval flow runs
        against a trivial no-span subprocess. The runner surfaces the failure in
        the manifest, but the tool returns a full EvalSummary either way."""
        with _isolated(tmp_path):
            scenarios = Path(".kensa/scenarios")
            _write_scenario(scenarios, "s1")
            out = asyncio.run(eval(scenario_dir=str(scenarios), scenario_ids=["s1"], timeout=5))
        assert isinstance(out, EvalSummary)
        assert out.run_id
        assert out.total == 1
        assert out.results_uri == f"kensa://runs/{out.run_id}/results"

    def test_malformed_yaml_returns_scenario_invalid(self, tmp_path: Path) -> None:
        """Malformed YAML in the scenario dir must surface as a typed
        MCPError(scenario_invalid), not escape as a generic tool failure."""
        scenarios = tmp_path / "scenarios"
        scenarios.mkdir()
        (scenarios / "bad.yaml").write_text("{: not valid yaml")
        out = asyncio.run(eval(scenario_dir=str(scenarios)))
        assert isinstance(out, MCPError)
        assert out.code == "scenario_invalid"

    def test_schema_violation_returns_scenario_invalid(self, tmp_path: Path) -> None:
        """Scenario YAML that parses but fails pydantic validation must map to
        scenario_invalid (e.g. dataset without input_field)."""
        scenarios = tmp_path / "scenarios"
        _write_scenario(scenarios, "s1", dataset="rows.jsonl")
        out = asyncio.run(eval(scenario_dir=str(scenarios)))
        assert isinstance(out, MCPError)
        assert out.code == "scenario_invalid"
        assert "input_field" in out.error


class TestReport:
    def test_invalid_run_id(self) -> None:
        out = report(run_id="../evil")
        assert isinstance(out, MCPError)
        assert out.code == "invalid_run_id"

    def test_missing_results(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            out = report(run_id="nonexistent")
        assert isinstance(out, MCPError)
        assert out.code == "run_not_found"

    def test_renders(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)
            run_id = "20260301T120000"
            results = [Result(scenario_id="s1", status=ResultStatus.PASS)]
            (results_dir / f"{run_id}.json").write_text(
                json.dumps([r.model_dump(mode="json") for r in results])
            )
            out = report(run_id=run_id, format="markdown")
        assert isinstance(out, ReportResponse)
        assert out.run_id == run_id
        assert out.format == "markdown"
        assert out.total == 1
        assert out.content


class TestAnalyze:
    def test_empty_trace_dir(self, tmp_path: Path) -> None:
        out = analyze(trace_dir=str(tmp_path / "empty"))
        assert out.trace_count == 0


class TestRunsResource:
    def test_empty(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            assert runs_list() == []

    def test_lists(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            _write_manifest("r1")
            out = runs_list()
        assert len(out) == 1
        assert out[0].run_id == "r1"


class TestRunDetailResource:
    def test_invalid_id(self) -> None:
        with pytest.raises(ResourceError):
            run_detail("../evil")

    def test_not_found(self, tmp_path: Path) -> None:
        with _isolated(tmp_path), pytest.raises(ResourceError):
            run_detail("nonexistent")

    def test_happy_path(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            _write_manifest("r1")
            detail = run_detail("r1")
        assert detail.run_id == "r1"
        assert detail.manifest.run_id == "r1"
        assert detail.summary is None


class TestRunResultsResource:
    def test_invalid_id(self) -> None:
        with pytest.raises(ResourceError):
            run_results("../evil")

    def test_not_found(self, tmp_path: Path) -> None:
        with _isolated(tmp_path), pytest.raises(ResourceError):
            run_results("nonexistent")

    def test_happy_path(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)
            results = [Result(scenario_id="s1", status=ResultStatus.PASS)]
            (results_dir / "r1.json").write_text(
                json.dumps([r.model_dump(mode="json") for r in results])
            )
            loaded = run_results("r1")
        assert len(loaded) == 1
        assert loaded[0].status == ResultStatus.PASS


class TestRunTraceResource:
    def test_invalid_ids(self) -> None:
        with pytest.raises(ResourceError):
            run_trace("../evil", "s1", "0")
        with pytest.raises(ResourceError):
            run_trace("r1", "../evil", "0")

    def test_run_not_found(self, tmp_path: Path) -> None:
        with _isolated(tmp_path), pytest.raises(ResourceError):
            run_trace("nonexistent", "s1", "0")

    def test_scenario_not_in_run(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            _write_manifest("r1", ["s1"])
            with pytest.raises(ResourceError):
                run_trace("r1", "ghost", "0")

    def test_happy_path(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            _write_manifest("r1", ["s1"])
            out = run_trace("r1", "s1", "0")
        assert out == []

    def test_dataset_expanded_runs_all_reachable(self, tmp_path: Path) -> None:
        """Every dataset row's trace must be reachable, not just index 0."""
        with _isolated(tmp_path):
            _write_manifest("r1", ["s1"], runs_per_scenario=3)
            traces = [run_trace("r1", "s1", str(i)) for i in range(3)]
        assert traces == [[], [], []]

    def test_index_out_of_range(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            _write_manifest("r1", ["s1"], runs_per_scenario=2)
            with pytest.raises(ResourceError, match="out of range"):
                run_trace("r1", "s1", "5")

    @pytest.mark.parametrize("bad", ["abc", "-1", "", "1.5"])
    def test_invalid_index(self, tmp_path: Path, bad: str) -> None:
        with _isolated(tmp_path):
            _write_manifest("r1", ["s1"])
            with pytest.raises(ResourceError, match="non-negative integer"):
                run_trace("r1", "s1", bad)


class TestScenariosResource:
    def test_empty(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            assert scenarios_list() == []

    def test_lists(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            _write_scenario(Path(".kensa/scenarios"), "s1", criteria="x")
            out = scenarios_list()
        assert len(out) == 1
        assert out[0].id == "s1"
        assert out[0].needs_judge is True


class TestScenarioDetailResource:
    def test_not_found(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            Path(".kensa/scenarios").mkdir(parents=True)
            with pytest.raises(ResourceError):
                scenario_detail("ghost")

    def test_happy_path(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            _write_scenario(Path(".kensa/scenarios"), "s1")
            out = scenario_detail("s1")
        assert out.id == "s1"


class TestJudgesResource:
    def test_empty(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            assert judges_list() == []

    def test_lists(self, tmp_path: Path) -> None:
        with _isolated(tmp_path):
            jd = Path(".kensa/judges")
            jd.mkdir(parents=True)
            (jd / "a.yaml").write_text("criterion: x\npass_definition: y\nfail_definition: z\n")
            out = judges_list()
        assert out == ["a"]


class TestJudgeDetailResource:
    def test_invalid_name(self) -> None:
        with pytest.raises(ResourceError):
            judge_detail("../evil")

    def test_not_found(self, tmp_path: Path) -> None:
        with _isolated(tmp_path), pytest.raises(ResourceError):
            judge_detail("ghost")


class TestRunServer:
    def test_rejects_unknown_transport(self) -> None:
        with pytest.raises(ToolError):
            run_server(transport="smoke-signal")  # type: ignore[arg-type]


class TestCliAlias:
    def test_kensa_mcp_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "--http" in result.output


class TestMainEntryPoint:
    def test_main_parses_args_and_invokes_server(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: dict[str, object] = {}

        def fake_run_server(**kwargs: object) -> None:
            called.update(kwargs)

        monkeypatch.setattr("kensa.mcp_server.run_server", fake_run_server)
        monkeypatch.setattr("sys.argv", ["kensa-mcp", "--http", "--port", "9000"])
        main()
        assert called == {"transport": "http", "host": "127.0.0.1", "port": 9000}


class TestLauncher:
    def test_exits_with_hint_when_fastmcp_missing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The launcher (used by the ``kensa-mcp`` shim) must print a clean
        install hint when ``fastmcp`` is missing, not a multi-frame traceback."""
        import sys

        from kensa import _mcp_launcher

        monkeypatch.setitem(sys.modules, "fastmcp", None)

        with pytest.raises(SystemExit) as exc:
            _mcp_launcher.main()

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "mcp" in err.lower()
        assert "install" in err.lower()

    def test_delegates_when_fastmcp_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When fastmcp is importable, the launcher hands off to mcp_server.main."""
        from kensa import _mcp_launcher

        called: list[bool] = []

        def fake_main() -> None:
            called.append(True)

        monkeypatch.setattr("kensa.mcp_server.main", fake_main)
        _mcp_launcher.main()
        assert called == [True]


@pytest.mark.integration
class TestIntegration:
    def test_full_protocol_roundtrip(self, tmp_path: Path) -> None:
        """Exercise the MCP protocol end-to-end via an in-memory client."""
        import dataclasses

        async def scenario() -> None:
            async with Client(mcp) as client:
                tools = await client.list_tools()
                assert {t.name for t in tools} == EXPECTED_TOOLS

                static = await client.list_resources()
                assert {str(r.uri) for r in static} == EXPECTED_STATIC_RESOURCES

                templates = await client.list_resource_templates()
                assert {t.uriTemplate for t in templates} == EXPECTED_TEMPLATES

                result = await client.call_tool("doctor", {})
                dumped = dataclasses.asdict(result.data)
                assert "ready" in dumped
                assert "checks" in dumped

                err = await client.call_tool("run", {"scenario_dir": "/does/not/exist"})
                err_data = dataclasses.asdict(err.data)
                assert err_data.get("code") == "scenarios_missing"

                scenarios_res = await client.read_resource("kensa://scenarios")
                assert scenarios_res[0].text in ("[]", "null")

        with _isolated(tmp_path):
            asyncio.run(scenario())
