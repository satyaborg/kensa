"""Tests for the CLI module."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from kensa.cli import cli
from kensa.models import (
    Analysis,
    Distribution,
    FlaggedTrace,
    FlagType,
    Result,
    ResultStatus,
    RunManifest,
    ScenarioRun,
    ToolUsage,
    TraceSummary,
)


class TestValidateRunId:
    def test_rejects_path_traversal(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["judge", "--run-id", "../evil"])
        assert result.exit_code == 1
        assert "Invalid run ID" in result.output


class TestCliRun:
    def test_run_missing_scenario_dir(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--scenario-dir", "/nonexistent"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_run_empty_scenario_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--scenario-dir", str(tmp_path)])
        # No .yaml files → 0 scenarios executed, shown as empty table
        assert result.exit_code == 0
        assert "kensa run" in result.output

    def test_run_with_bad_command(self, tmp_path: Path) -> None:
        """Run command with a scenario whose run_command fails — partial results."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            scenario_dir = Path("scenarios")
            scenario_dir.mkdir()
            scenario = {
                "id": "bad_cmd",
                "name": "Bad command test",
                "run_command": ["false"],
                "input": "test",
            }
            with open(scenario_dir / "bad_cmd.yaml", "w") as f:
                yaml.dump(scenario, f)

            result = runner.invoke(
                cli, ["run", "--scenario-dir", str(scenario_dir), "--timeout", "5"]
            )
            # Partial results: scenario fails but run completes with error row
            assert result.exit_code == 0
            assert "bad_cmd" in result.output

    def test_run_success_prints_scenario_status(self, tmp_path: Path) -> None:
        """Successful run prints per-scenario status in a table."""
        runner = CliRunner()
        manifest = RunManifest(
            run_id="20260324T120000",
            timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
            scenarios={
                "s1": [ScenarioRun(trace_path="t/s1.jsonl", exit_code=0, duration_seconds=1.5)],
                "s2": [ScenarioRun(trace_path="t/s2.jsonl", exit_code=1, duration_seconds=2.0)],
            },
        )
        with patch("kensa.runner.run_scenarios", return_value=manifest):
            result = runner.invoke(cli, ["run", "--scenario-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "s1" in result.output
        assert "s2" in result.output
        assert "1.5s" in result.output
        assert "2.0s" in result.output

    def test_run_shows_error_for_failed_scenario(self, tmp_path: Path) -> None:
        """Failed scenario with no trace_path shows error status."""
        runner = CliRunner()
        manifest = RunManifest(
            run_id="20260324T120000",
            timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
            scenarios={
                "broken": [
                    ScenarioRun(
                        trace_path="",
                        exit_code=-1,
                        duration_seconds=0.0,
                        stderr="ModuleNotFoundError: No module named 'mylib'",
                    )
                ],
            },
        )
        with patch("kensa.runner.run_scenarios", return_value=manifest):
            result = runner.invoke(cli, ["run", "--scenario-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "broken" in result.output
        assert "error" in result.output.lower()

    def test_run_format_json(self, tmp_path: Path) -> None:
        """--format json dumps the full manifest as JSON."""
        runner = CliRunner()
        manifest = RunManifest(
            run_id="20260324T120000",
            timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
            scenarios={
                "s1": [ScenarioRun(trace_path="t/s1.jsonl", exit_code=0, duration_seconds=1.0)],
            },
        )
        with patch("kensa.runner.run_scenarios", return_value=manifest):
            result = runner.invoke(
                cli, ["run", "--scenario-dir", str(tmp_path), "--format", "json"]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["run_id"] == "20260324T120000"
        assert "s1" in data["scenarios"]

    def test_run_dry_run_text(self, tmp_path: Path) -> None:
        """--dry-run lists scenarios without executing."""
        runner = CliRunner()
        scenario_dir = tmp_path / "scenarios"
        scenario_dir.mkdir()
        scenario = {"id": "demo", "name": "Demo", "run_command": ["echo", "hi"], "input": "x"}
        with open(scenario_dir / "demo.yaml", "w") as f:
            yaml.dump(scenario, f)

        result = runner.invoke(cli, ["run", "--scenario-dir", str(scenario_dir), "--dry-run"])
        assert result.exit_code == 0
        assert "Would run 1 scenario(s)" in result.output
        assert "demo" in result.output

    def test_run_value_error(self, tmp_path: Path) -> None:
        """ValueError in run (e.g. bad scenario ID filter) exits 1."""
        runner = CliRunner()
        with patch("kensa.runner.run_scenarios", side_effect=ValueError("No scenario 'x' found")):
            result = runner.invoke(cli, ["run", "--scenario-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "No scenario 'x' found" in result.output

    def test_run_generic_exception(self, tmp_path: Path) -> None:
        """Unexpected exception in run exits 1."""
        runner = CliRunner()
        with patch("kensa.runner.run_scenarios", side_effect=RuntimeError("boom")):
            result = runner.invoke(cli, ["run", "--scenario-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "boom" in result.output

    def test_run_dry_run_json(self, tmp_path: Path) -> None:
        """--dry-run --format json returns structured list of scenario IDs."""
        runner = CliRunner()
        scenario_dir = tmp_path / "scenarios"
        scenario_dir.mkdir()
        scenario = {"id": "demo", "name": "Demo", "run_command": ["echo", "hi"], "input": "x"}
        with open(scenario_dir / "demo.yaml", "w") as f:
            yaml.dump(scenario, f)

        result = runner.invoke(
            cli, ["run", "--scenario-dir", str(scenario_dir), "--dry-run", "--format", "json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["dry_run"] is True
        assert data["scenarios"] == ["demo"]


class TestCliJudge:
    def test_judge_no_runs(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["judge"])
            assert result.exit_code == 1
            assert "Error" in result.output

    def test_judge_with_run_id_missing(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["judge", "--run-id", "nonexistent"])
            assert result.exit_code == 1

    def test_judge_no_api_keys_empty_manifest_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Judge command without API keys succeeds for an empty/checks-only manifest."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("KENSA_JUDGE_MODEL", raising=False)

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260317T143000",
                timestamp=datetime(2026, 3, 17, 14, 30, tzinfo=timezone.utc),
                scenarios={},
            )
            (runs_dir / "20260317T143000.json").write_text(manifest.model_dump_json())

            result = runner.invoke(cli, ["judge"])
            assert result.exit_code == 0
            assert "0/0 passed" in result.output

    def test_judge_happy_path_latest(self, tmp_path: Path) -> None:
        """Judge command loads latest manifest, judges scenarios, writes results."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            # Set up manifest
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            scenarios_dir = Path(".kensa/scenarios")
            scenarios_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260324T120000",
                timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
                scenarios={
                    "weather": [
                        ScenarioRun(
                            trace_path=".kensa/traces/weather.jsonl",
                            exit_code=0,
                            duration_seconds=1.0,
                        )
                    ],
                },
            )
            (runs_dir / "20260324T120000.json").write_text(manifest.model_dump_json())

            # Write scenario YAML
            scenario_data = {
                "id": "weather",
                "name": "Weather test",
                "run_command": ["echo", "test"],
                "input": "test",
            }
            with open(scenarios_dir / "weather.yaml", "w") as f:
                yaml.dump(scenario_data, f)

            judge_result = Result(
                scenario_id="weather",
                status=ResultStatus.PASS,
                check_results=[],
                trace=TraceSummary(
                    path=".kensa/traces/weather.jsonl",
                    llm_calls=1,
                    tool_calls=0,
                    total_tokens=100,
                    cost_usd=0.01,
                    duration_seconds=1.0,
                ),
            )

            with (
                patch("kensa.runner.read_trace", return_value=[]),
                patch("kensa.judge.judge_scenario", return_value=judge_result),
                patch("kensa.judge.get_judge", return_value=None),
            ):
                result = runner.invoke(cli, ["judge"])

            assert result.exit_code == 0
            assert "weather" in result.output
            assert "✓" in result.output
            assert "1/1 passed" in result.output
            # Results file should be written
            results_path = Path(".kensa/results/20260324T120000.json")
            assert results_path.exists()

    def test_judge_with_explicit_run_id(self, tmp_path: Path) -> None:
        """Judge command with --run-id loads specific manifest."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            scenarios_dir = Path(".kensa/scenarios")
            scenarios_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="specific_run",
                timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
                scenarios={
                    "s1": [
                        ScenarioRun(
                            trace_path=".kensa/traces/s1.jsonl",
                            exit_code=0,
                            duration_seconds=0.5,
                        )
                    ],
                },
            )
            (runs_dir / "specific_run.json").write_text(manifest.model_dump_json())

            scenario_data = {
                "id": "s1",
                "name": "Test scenario",
                "run_command": ["echo", "test"],
                "input": "test",
            }
            with open(scenarios_dir / "s1.yaml", "w") as f:
                yaml.dump(scenario_data, f)

            judge_result = Result(scenario_id="s1", status=ResultStatus.FAIL)

            with (
                patch("kensa.runner.read_trace", return_value=[]),
                patch("kensa.judge.judge_scenario", return_value=judge_result),
                patch("kensa.judge.get_judge", return_value=None),
            ):
                result = runner.invoke(cli, ["judge", "--run-id", "specific_run"])

            assert result.exit_code == 0
            assert "s1" in result.output
            assert "✗" in result.output
            assert "0/1 passed" in result.output

    def test_judge_format_json(self, tmp_path: Path) -> None:
        """--format json returns structured results with pass count."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            scenarios_dir = Path(".kensa/scenarios")
            scenarios_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260324T120000",
                timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
                scenarios={
                    "s1": [
                        ScenarioRun(
                            trace_path=".kensa/traces/s1.jsonl",
                            exit_code=0,
                            duration_seconds=0.5,
                        )
                    ],
                },
            )
            (runs_dir / "20260324T120000.json").write_text(manifest.model_dump_json())

            scenario_data = {
                "id": "s1",
                "name": "Test scenario",
                "run_command": ["echo", "test"],
                "input": "test",
            }
            with open(scenarios_dir / "s1.yaml", "w") as f:
                yaml.dump(scenario_data, f)

            judge_result = Result(scenario_id="s1", status=ResultStatus.PASS)

            with (
                patch("kensa.runner.read_trace", return_value=[]),
                patch("kensa.judge.judge_scenario", return_value=judge_result),
                patch("kensa.judge.get_judge", return_value=None),
            ):
                result = runner.invoke(cli, ["judge", "--format", "json"])

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["run_id"] == "20260324T120000"
            assert data["total"] == 1
            assert data["passed"] == 1
            assert len(data["results"]) == 1

    def test_judge_skips_missing_scenario_file(self, tmp_path: Path) -> None:
        """Judge skips scenarios whose YAML file is missing."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            Path(".kensa/scenarios").mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260324T120000",
                timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
                scenarios={
                    "missing": [
                        ScenarioRun(
                            trace_path=".kensa/traces/missing.jsonl",
                            exit_code=0,
                            duration_seconds=1.0,
                        )
                    ],
                },
            )
            (runs_dir / "20260324T120000.json").write_text(manifest.model_dump_json())

            with patch("kensa.judge.get_judge", return_value=None):
                result = runner.invoke(cli, ["judge"])

            assert result.exit_code == 0
            assert "scenario file not found" in result.output

    def test_judge_includes_failed_scenarios(self, tmp_path: Path) -> None:
        """Judge turns no-trace run failures into explicit ERROR results."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            scenarios_dir = Path(".kensa/scenarios")
            scenarios_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260324T120000",
                timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
                scenarios={
                    "failed": [
                        ScenarioRun(
                            trace_path="",
                            exit_code=-1,
                            duration_seconds=0.0,
                            stderr="crashed",
                        )
                    ],
                },
            )
            (runs_dir / "20260324T120000.json").write_text(manifest.model_dump_json())

            with open(scenarios_dir / "failed.yaml", "w") as f:
                yaml.dump(
                    {
                        "id": "failed",
                        "name": "Failed",
                        "run_command": ["echo", "x"],
                        "input": "x",
                    },
                    f,
                )

            result = runner.invoke(cli, ["judge"])

            assert result.exit_code == 0
            assert "failed" in result.output
            assert "crashed" in result.output
            assert "errors" in result.output

    def test_judge_checks_only_manifest_does_not_resolve_judge(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            scenarios_dir = Path(".kensa/scenarios")
            scenarios_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260324T120000",
                timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
                scenarios={
                    "s1": [
                        ScenarioRun(
                            trace_path=".kensa/traces/s1.jsonl",
                            exit_code=0,
                            duration_seconds=0.5,
                        )
                    ],
                },
            )
            (runs_dir / "20260324T120000.json").write_text(manifest.model_dump_json())
            with open(scenarios_dir / "s1.yaml", "w") as f:
                yaml.dump(
                    {"id": "s1", "name": "Test", "run_command": ["echo", "test"], "input": "x"}, f
                )

            with (
                patch("kensa.runner.read_trace", return_value=[]),
                patch("kensa.judge.get_judge") as mock_get_judge,
            ):
                result = runner.invoke(cli, ["judge"])

            assert result.exit_code == 0
            mock_get_judge.assert_not_called()


class TestCliReport:
    def test_report_no_results(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["report"])
            assert result.exit_code == 1
            assert "Error" in result.output

    def test_report_results_file_missing(self, tmp_path: Path) -> None:
        """Report errors when results file doesn't exist for a valid manifest."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260324T120000",
                timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
                scenarios={},
            )
            (runs_dir / "20260324T120000.json").write_text(manifest.model_dump_json())

            result = runner.invoke(cli, ["report"])
            assert result.exit_code == 1
            assert "No results for run" in result.output

    def test_report_json_format(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            # Create a fake run manifest and results
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260317T143000",
                timestamp=datetime(2026, 3, 17, 14, 30, tzinfo=timezone.utc),
                scenarios={
                    "test_1": [
                        ScenarioRun(
                            trace_path=".kensa/traces/test.jsonl",
                            exit_code=0,
                            duration_seconds=1.0,
                        )
                    ]
                },
            )
            (runs_dir / "20260317T143000.json").write_text(manifest.model_dump_json())

            results = [
                Result(
                    scenario_id="test_1",
                    status=ResultStatus.PASS,
                )
            ]
            (results_dir / "20260317T143000.json").write_text(
                json.dumps([r.model_dump(mode="json") for r in results])
            )

            result = runner.invoke(cli, ["report", "--format", "json"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data[0]["scenario_id"] == "test_1"
            assert "HTML report:" not in result.output

    def test_report_html_format(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260317T143000",
                timestamp=datetime(2026, 3, 17, 14, 30, tzinfo=timezone.utc),
                scenarios={},
            )
            (runs_dir / "20260317T143000.json").write_text(manifest.model_dump_json())

            results = [Result(scenario_id="test_1", status=ResultStatus.PASS)]
            (results_dir / "20260317T143000.json").write_text(
                json.dumps([r.model_dump(mode="json") for r in results])
            )

            result = runner.invoke(cli, ["report", "--format", "html"])
            assert result.exit_code == 0
            assert result.output.lstrip().startswith("<!DOCTYPE html>")
            assert "HTML report:" not in result.output

    def test_report_terminal_format(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260317T143000",
                timestamp=datetime(2026, 3, 17, 14, 30, tzinfo=timezone.utc),
                scenarios={},
            )
            (runs_dir / "20260317T143000.json").write_text(manifest.model_dump_json())

            results = [
                Result(scenario_id="s1", status=ResultStatus.PASS),
                Result(scenario_id="s2", status=ResultStatus.FAIL),
            ]
            (results_dir / "20260317T143000.json").write_text(
                json.dumps([r.model_dump(mode="json") for r in results])
            )

            result = runner.invoke(cli, ["report", "--format", "terminal"])
            assert result.exit_code == 0

    def test_report_markdown_format(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260317T143000",
                timestamp=datetime(2026, 3, 17, 14, 30, tzinfo=timezone.utc),
                scenarios={},
            )
            (runs_dir / "20260317T143000.json").write_text(manifest.model_dump_json())

            results = [
                Result(scenario_id="s1", status=ResultStatus.PASS),
            ]
            (results_dir / "20260317T143000.json").write_text(
                json.dumps([r.model_dump(mode="json") for r in results])
            )

            result = runner.invoke(cli, ["report", "--format", "markdown"])
            assert result.exit_code == 0
            assert "# kensa" in result.output or "passed" in result.output.lower()

    def test_report_verbose_terminal(self, tmp_path: Path) -> None:
        """--verbose with terminal format calls format_terminal(verbose=True)."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260317T143000",
                timestamp=datetime(2026, 3, 17, 14, 30, tzinfo=timezone.utc),
                scenarios={},
            )
            (runs_dir / "20260317T143000.json").write_text(manifest.model_dump_json())

            results = [Result(scenario_id="s1", status=ResultStatus.PASS)]
            (results_dir / "20260317T143000.json").write_text(
                json.dumps([r.model_dump(mode="json") for r in results])
            )

            result = runner.invoke(cli, ["report", "--format", "terminal", "--verbose"])
            assert result.exit_code == 0

    def test_report_output_to_file(self, tmp_path: Path) -> None:
        """--output writes report to a file."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260317T143000",
                timestamp=datetime(2026, 3, 17, 14, 30, tzinfo=timezone.utc),
                scenarios={},
            )
            (runs_dir / "20260317T143000.json").write_text(manifest.model_dump_json())

            results = [Result(scenario_id="s1", status=ResultStatus.PASS)]
            (results_dir / "20260317T143000.json").write_text(
                json.dumps([r.model_dump(mode="json") for r in results])
            )

            out_file = tmp_path / "out.json"
            result = runner.invoke(cli, ["report", "--format", "json", "--output", str(out_file)])
            assert result.exit_code == 0
            assert out_file.exists()
            assert "Report written to" in result.output

    def test_report_generic_exception(self, tmp_path: Path) -> None:
        """Non-FileNotFoundError in report exits 1."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)

            manifest = RunManifest(
                run_id="20260317T143000",
                timestamp=datetime(2026, 3, 17, 14, 30, tzinfo=timezone.utc),
                scenarios={},
            )
            (runs_dir / "20260317T143000.json").write_text(manifest.model_dump_json())

            # Write invalid JSON to trigger a parse error
            (results_dir / "20260317T143000.json").write_text("not valid json")

            result = runner.invoke(cli, ["report", "--format", "json"])
            assert result.exit_code == 1
            assert "Error" in result.output

    def test_report_with_run_id(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)

            results = [Result(scenario_id="s1", status=ResultStatus.PASS)]
            (results_dir / "my_run.json").write_text(
                json.dumps([r.model_dump(mode="json") for r in results])
            )

            result = runner.invoke(cli, ["report", "--run-id", "my_run", "--format", "json"])
            assert result.exit_code == 0


class TestCliEval:
    def test_eval_happy_path(self, tmp_path: Path) -> None:
        """Eval command runs + judges + reports in one shot."""
        runner = CliRunner()
        manifest = RunManifest(
            run_id="20260324T120000",
            timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
            scenarios={
                "s1": [
                    ScenarioRun(
                        trace_path="t/s1.jsonl",
                        exit_code=0,
                        duration_seconds=1.0,
                        stdout="hello world",
                    )
                ],
            },
        )
        judge_result = Result(scenario_id="s1", status=ResultStatus.PASS)

        with runner.isolated_filesystem(temp_dir=tmp_path):
            scenario_dir = Path(".kensa/scenarios")
            scenario_dir.mkdir(parents=True)
            scenario = {
                "id": "s1",
                "name": "Test",
                "run_command": ["echo", "test"],
                "input": "test",
            }
            with open(scenario_dir / "s1.yaml", "w") as f:
                yaml.dump(scenario, f)

            with (
                patch("kensa.runner.run_scenarios", return_value=manifest),
                patch("kensa.runner.read_trace", return_value=[]),
                patch("kensa.judge.judge_scenario", return_value=judge_result),
                patch("kensa.judge.get_judge", return_value=None),
            ):
                result = runner.invoke(cli, ["eval"])

            assert result.exit_code == 0
            assert "s1" in result.output

    def test_eval_with_failed_scenario(self, tmp_path: Path) -> None:
        """Eval with a failed scenario still completes."""
        runner = CliRunner()
        manifest = RunManifest(
            run_id="20260324T120000",
            timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
            scenarios={
                "ok": [
                    ScenarioRun(
                        trace_path="t/ok.jsonl",
                        exit_code=0,
                        duration_seconds=1.0,
                    )
                ],
                "broken": [
                    ScenarioRun(
                        trace_path="",
                        exit_code=-1,
                        duration_seconds=0.0,
                        stderr="crash",
                    )
                ],
            },
        )
        judge_result = Result(scenario_id="ok", status=ResultStatus.PASS)

        with runner.isolated_filesystem(temp_dir=tmp_path):
            scenario_dir = Path(".kensa/scenarios")
            scenario_dir.mkdir(parents=True)
            for sid in ["ok", "broken"]:
                scenario = {
                    "id": sid,
                    "name": sid,
                    "run_command": ["echo", "test"],
                    "input": "test",
                }
                with open(scenario_dir / f"{sid}.yaml", "w") as f:
                    yaml.dump(scenario, f)

            with (
                patch("kensa.runner.run_scenarios", return_value=manifest),
                patch("kensa.runner.read_trace", return_value=[]),
                patch("kensa.judge.judge_scenario", return_value=judge_result),
                patch("kensa.judge.get_judge", return_value=None),
            ):
                result = runner.invoke(cli, ["eval"])

            assert result.exit_code == 0
            assert "ok" in result.output
            assert "broken" in result.output

    def test_eval_checks_only_manifest_does_not_resolve_judge(self, tmp_path: Path) -> None:
        runner = CliRunner()
        manifest = RunManifest(
            run_id="20260324T120000",
            timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
            scenarios={
                "s1": [ScenarioRun(trace_path="t/s1.jsonl", exit_code=0, duration_seconds=1.0)],
            },
        )

        with runner.isolated_filesystem(temp_dir=tmp_path):
            scenario_dir = Path(".kensa/scenarios")
            scenario_dir.mkdir(parents=True)
            with open(scenario_dir / "s1.yaml", "w") as f:
                yaml.dump(
                    {"id": "s1", "name": "Test", "run_command": ["echo", "test"], "input": "x"}, f
                )

            with (
                patch("kensa.runner.run_scenarios", return_value=manifest),
                patch("kensa.runner.read_trace", return_value=[]),
                patch("kensa.judge.get_judge") as mock_get_judge,
            ):
                result = runner.invoke(cli, ["eval"])

            assert result.exit_code == 0
            mock_get_judge.assert_not_called()

    def test_eval_formatter_fallback(self, tmp_path: Path) -> None:
        """When FORMATTERS lacks the key, eval falls back to pass/total summary."""
        runner = CliRunner()
        manifest = RunManifest(
            run_id="20260324T120000",
            timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
            scenarios={
                "s1": [ScenarioRun(trace_path="t/s1.jsonl", exit_code=0, duration_seconds=1.0)],
            },
        )
        judge_result = Result(scenario_id="s1", status=ResultStatus.PASS)

        with runner.isolated_filesystem(temp_dir=tmp_path):
            scenario_dir = Path(".kensa/scenarios")
            scenario_dir.mkdir(parents=True)
            with open(scenario_dir / "s1.yaml", "w") as f:
                yaml.dump({"id": "s1", "name": "T", "run_command": ["echo", "t"], "input": "t"}, f)

            with (
                patch("kensa.runner.run_scenarios", return_value=manifest),
                patch("kensa.runner.read_trace", return_value=[]),
                patch("kensa.judge.judge_scenario", return_value=judge_result),
                patch("kensa.judge.get_judge", return_value=None),
                patch("kensa.report.FORMATTERS", {}),
            ):
                result = runner.invoke(cli, ["eval"])

            assert result.exit_code == 0
            assert "1/1 passed" in result.output

    def test_eval_file_not_found(self, tmp_path: Path) -> None:
        """FileNotFoundError in eval exits 1."""
        runner = CliRunner()
        with patch(
            "kensa.runner.run_scenarios",
            side_effect=FileNotFoundError("No scenarios dir"),
        ):
            result = runner.invoke(cli, ["eval", "--scenario-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "No scenarios dir" in result.output

    def test_eval_generic_exception(self, tmp_path: Path) -> None:
        """Unexpected exception in eval exits 1."""
        runner = CliRunner()
        with patch("kensa.runner.run_scenarios", side_effect=RuntimeError("kaboom")):
            result = runner.invoke(cli, ["eval", "--scenario-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "kaboom" in result.output


class TestLatestManifest:
    def test_latest_manifest_empty_dir(self, tmp_path: Path) -> None:
        """Runs dir exists but has no .json files."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            Path(".kensa/runs").mkdir(parents=True)
            result = runner.invoke(cli, ["report"])
            assert result.exit_code == 1
            assert "No run manifests found" in result.output

    def test_latest_manifest_picks_last(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runs_dir = Path(".kensa/runs")
            runs_dir.mkdir(parents=True)
            results_dir = Path(".kensa/results")
            results_dir.mkdir(parents=True)

            # Create two manifests — latest should be picked
            for run_id in ["20260317T100000", "20260317T200000"]:
                m = RunManifest(
                    run_id=run_id,
                    timestamp=datetime(2026, 3, 17, 14, 30, tzinfo=timezone.utc),
                    scenarios={},
                )
                (runs_dir / f"{run_id}.json").write_text(m.model_dump_json())
                (results_dir / f"{run_id}.json").write_text(
                    json.dumps(
                        [Result(scenario_id="s1", status=ResultStatus.PASS).model_dump(mode="json")]
                    )
                )

            result = runner.invoke(cli, ["report", "--format", "json"])
            assert result.exit_code == 0


# ---------------------------------------------------------------------------
# analyze command
# ---------------------------------------------------------------------------


def _sample_analysis() -> Analysis:
    return Analysis(
        trace_count=5,
        success_rate=0.8,
        cost_distribution=Distribution(p50=0.01, p90=0.05, p99=0.1, max=0.15),
        latency_distribution=Distribution(p50=1.0, p90=3.0, p99=5.0, max=7.0),
        tool_usage=[ToolUsage(tool="search", call_count=10, avg_latency_ms=200.0, error_rate=0.1)],
        flagged_traces=[
            FlaggedTrace(trace_id="trace_004", flag=FlagType.COST_OUTLIER, detail="10x median"),
        ],
    )


class TestCliAnalyze:
    def test_analyze_json_output(self) -> None:
        runner = CliRunner()
        with patch("kensa.analyzer.analyze_traces", return_value=_sample_analysis()):
            result = runner.invoke(cli, ["analyze", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["trace_count"] == 5
        assert data["success_rate"] == 0.8

    def test_analyze_json_includes_distributions(self) -> None:
        runner = CliRunner()
        with patch("kensa.analyzer.analyze_traces", return_value=_sample_analysis()):
            result = runner.invoke(cli, ["analyze", "--format", "json"])
        data = json.loads(result.output)
        assert data["cost_distribution"]["p50"] == 0.01
        assert data["latency_distribution"]["p90"] == 3.0

    def test_analyze_json_includes_tool_usage(self) -> None:
        runner = CliRunner()
        with patch("kensa.analyzer.analyze_traces", return_value=_sample_analysis()):
            result = runner.invoke(cli, ["analyze", "--format", "json"])
        data = json.loads(result.output)
        assert len(data["tool_usage"]) == 1
        assert data["tool_usage"][0]["tool"] == "search"

    def test_analyze_json_includes_flagged_traces(self) -> None:
        runner = CliRunner()
        with patch("kensa.analyzer.analyze_traces", return_value=_sample_analysis()):
            result = runner.invoke(cli, ["analyze", "--format", "json"])
        data = json.loads(result.output)
        assert len(data["flagged_traces"]) == 1
        assert data["flagged_traces"][0]["flag"] == "cost_outlier"

    def test_analyze_passes_custom_trace_dir(self) -> None:
        runner = CliRunner()
        with patch("kensa.analyzer.analyze_traces", return_value=_sample_analysis()) as mock:
            runner.invoke(cli, ["analyze", "--trace-dir", "custom/traces", "--format", "json"])
        mock.assert_called_once_with("custom/traces")

    def test_analyze_default_trace_dir(self) -> None:
        runner = CliRunner()
        with patch("kensa.analyzer.analyze_traces", return_value=_sample_analysis()) as mock:
            runner.invoke(cli, ["analyze", "--format", "json"])
        mock.assert_called_once_with(".kensa/traces")

    def test_analyze_empty_analysis(self) -> None:
        runner = CliRunner()
        with patch("kensa.analyzer.analyze_traces", return_value=Analysis()):
            result = runner.invoke(cli, ["analyze", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["trace_count"] == 0
        assert data["tool_usage"] == []
        assert data["flagged_traces"] == []

    def test_analyze_text_format(self) -> None:
        runner = CliRunner()
        with patch("kensa.analyzer.analyze_traces", return_value=_sample_analysis()):
            result = runner.invoke(cli, ["analyze"])
        assert result.exit_code == 0
        assert "5 traces" in result.output
        assert "80%" in result.output

    def test_analyze_text_shows_flagged_traces(self) -> None:
        runner = CliRunner()
        with patch("kensa.analyzer.analyze_traces", return_value=_sample_analysis()):
            result = runner.invoke(cli, ["analyze"])
        assert result.exit_code == 0
        assert "trace_004" in result.output
        assert "10x median" in result.output

    def test_analyze_generic_exception(self) -> None:
        """Unexpected exception in analyze exits 1."""
        runner = CliRunner()
        with patch("kensa.analyzer.analyze_traces", side_effect=RuntimeError("oops")):
            result = runner.invoke(cli, ["analyze"])
        assert result.exit_code == 1
        assert "oops" in result.output

    def test_analyze_output_to_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        out_file = tmp_path / "analysis.json"
        with patch("kensa.analyzer.analyze_traces", return_value=_sample_analysis()):
            result = runner.invoke(cli, ["analyze", "--format", "json", "--output", str(out_file)])
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["trace_count"] == 5


# ---------------------------------------------------------------------------
# init command
# ---------------------------------------------------------------------------


class TestCliInit:
    def test_init_creates_directories_and_example(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert Path(".kensa/scenarios").is_dir()
            assert Path(".kensa/traces").is_dir()
            assert Path(".kensa/judges").is_dir()
            assert Path(".kensa/agents").is_dir()
            assert Path(".kensa/agents/example.py").is_file()
            assert Path(".kensa/scenarios/example.yaml").is_file()
            assert "created" in result.output

    def test_init_example_is_valid_yaml(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            content = yaml.safe_load(Path(".kensa/scenarios/example.yaml").read_text())
            assert content["id"] == "example"
            assert content["run_command"]
            assert len(content["checks"]) >= 1

    def test_init_idempotent_no_overwrite(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            # Modify the example
            example = Path(".kensa/scenarios/example.yaml")
            example.write_text("id: custom\nname: Custom\n")

            result = runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            assert "scenario ready" in result.output
            # Content should be untouched
            assert example.read_text() == "id: custom\nname: Custom\n"

    def test_init_force_overwrites(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            runner.invoke(cli, ["init"])
            example = Path(".kensa/scenarios/example.yaml")
            example.write_text("id: custom\nname: Custom\n")

            result = runner.invoke(cli, ["init", "--force"])
            assert result.exit_code == 0
            content = yaml.safe_load(example.read_text())
            assert content["id"] == "example"

    def test_init_scaffolds_agent_without_instrument_boilerplate(self, tmp_path: Path) -> None:
        """Scaffolded agent has no `instrument()` boilerplate — the wrapper handles it."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            agent = Path(".kensa/agents/example.py").read_text()
            assert "from kensa import instrument" not in agent
            assert "instrument()" not in agent

    def test_init_scaffolds_stub_without_api_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["init"])
            assert result.exit_code == 0
            agent = Path(".kensa/agents/example.py").read_text()
            assert "from kensa import instrument" not in agent
            scenario = yaml.safe_load(Path(".kensa/scenarios/example.yaml").read_text())
            assert "export" in scenario["input"].lower()

    def test_init_blank_creates_dirs_without_examples(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["init", "--blank"])
            assert result.exit_code == 0
            assert Path(".kensa/scenarios").is_dir()
            assert Path(".kensa/traces").is_dir()
            assert Path(".kensa/judges").is_dir()
            assert Path(".kensa/agents").is_dir()
            assert not Path(".kensa/agents/example.py").exists()
            assert not Path(".kensa/scenarios/example.yaml").exists()
            assert not Path(".kensa/scenarios/example.jsonl").exists()
            assert "add your scenarios" in result.output

    def test_init_runs_doctor(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["init"])
            assert "checks passed" in result.output
