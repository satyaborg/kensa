"""Tests for the `kensa capture` CLI command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from kensa.cli import cli
from kensa.models import RunKind, RunManifest

FIXTURE_AGENT = Path(__file__).parent / "fixtures" / "capture_agent.py"


class _FakeCompleter:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def complete(self, prompt: str, *, response_format: str | None = None) -> str:
        return self.payload


def _generated_scenario(run_command: list[str]) -> dict[str, object]:
    return {
        "id": "captured_happy",
        "name": "Captured happy path",
        "description": "Generated from a captured trace.",
        "source": "traces",
        "input": "refund this order please",
        "run_command": run_command,
        "expected_outcome": "Agent handles the task.",
        "checks": [
            {
                "type": "output_contains",
                "params": {"value": "captured"},
                "description": "Keeps the captured behavior",
            },
            {"type": "max_turns", "params": {"max": 5}, "description": "Under 5 LLM calls"},
        ],
        "criteria": "The agent should complete the refund request clearly.",
    }


class TestCaptureCli:
    def test_capture_writes_manifest_and_trace(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "capture",
                    "-i",
                    "refund this order please",
                    "--",
                    sys.executable,
                    str(FIXTURE_AGENT),
                ],
            )

            assert result.exit_code == 0
            manifest_path = next(Path(".kensa/runs").glob("*.json"))
            manifest = RunManifest.model_validate_json(manifest_path.read_text())
            assert manifest.kind == RunKind.CAPTURE
            assert manifest.command == [sys.executable, str(FIXTURE_AGENT)]
            assert manifest.trace_path is not None
            assert Path(manifest.trace_path).is_file()
            assert manifest.span_count == 1
            assert "kensa generate" in result.output

    def test_capture_stores_argv_verbatim_when_input_omitted(self, tmp_path: Path) -> None:
        """Without -i, the full argv is stored; no heuristic splitting."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "capture",
                    "--",
                    sys.executable,
                    str(FIXTURE_AGENT),
                    "refund this order please",
                ],
            )

            assert result.exit_code == 0
            manifest_path = next(Path(".kensa/runs").glob("*.json"))
            manifest = RunManifest.model_validate_json(manifest_path.read_text())
            assert manifest.command == [
                sys.executable,
                str(FIXTURE_AGENT),
                "refund this order please",
            ]

    def test_capture_empty_argv_errors(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli, ["capture"])

            assert result.exit_code == 2
            assert "after `--`" in result.output
            assert not Path(".kensa/runs").exists()

    def test_capture_non_zero_exit_writes_manifest(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                ["capture", "--", sys.executable, "-c", "import sys; sys.exit(5)"],
            )

            assert result.exit_code == 5
            manifest_path = next(Path(".kensa/runs").glob("*.json"))
            manifest = RunManifest.model_validate_json(manifest_path.read_text())
            assert manifest.kind == RunKind.CAPTURE
            assert manifest.exit_code == 5

    def test_capture_zero_spans_warns_but_writes_manifest(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                ["capture", "--", sys.executable, "-c", "print('hello from no spans')"],
            )

            assert result.exit_code == 0
            manifest_path = next(Path(".kensa/runs").glob("*.json"))
            manifest = RunManifest.model_validate_json(manifest_path.read_text())
            assert manifest.kind == RunKind.CAPTURE
            assert manifest.span_count == 0
            assert manifest.trace_path is None
            assert "no spans captured" in result.output

    def test_generate_uses_latest_capture_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = CliRunner()
        payload = json.dumps(
            {"scenarios": [_generated_scenario([sys.executable, str(FIXTURE_AGENT)])]}
        )
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: _FakeCompleter(payload))

        with runner.isolated_filesystem(temp_dir=tmp_path):
            capture = runner.invoke(
                cli,
                [
                    "capture",
                    "-i",
                    "refund this order please",
                    "--",
                    sys.executable,
                    str(FIXTURE_AGENT),
                ],
            )
            assert capture.exit_code == 0

            result = runner.invoke(cli, ["generate"])

            assert result.exit_code == 0
            scenario = yaml.safe_load(Path(".kensa/scenarios/captured_happy.yaml").read_text())
            assert scenario["run_command"] == [sys.executable, str(FIXTURE_AGENT)]
            assert scenario["input"] == "refund this order please"

    def test_generate_rejects_no_i_capture_with_count_gt_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No-`-i` single-command capture + -n >1 would replay the same prompt N times."""
        runner = CliRunner()

        def _fail_completer(*_a: object, **_kw: object) -> None:
            raise AssertionError("LLM must not be called when input is baked-in and count > 1")

        monkeypatch.setattr("kensa.llm.get_completer", _fail_completer)

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                ["capture", "--", sys.executable, str(FIXTURE_AGENT), "hello"],
            )
            assert result.exit_code == 0, result.output

            gen = runner.invoke(cli, ["generate", "-n", "3"])
            assert gen.exit_code != 0
            assert "baked-in prompt" in gen.output
            assert "kensa capture -i" in gen.output

    def test_generate_no_i_capture_with_count_1_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single-scenario generation from a no-`-i` capture is still allowed (verbatim replay)."""
        runner = CliRunner()
        argv = [sys.executable, str(FIXTURE_AGENT), "hello"]
        payload = json.dumps({"scenarios": [_generated_scenario(argv)]})
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: _FakeCompleter(payload))

        with runner.isolated_filesystem(temp_dir=tmp_path):
            assert runner.invoke(cli, ["capture", "--", *argv]).exit_code == 0
            gen = runner.invoke(cli, ["generate", "-n", "1"])
            assert gen.exit_code == 0, gen.output
            assert (Path(".kensa/scenarios") / "captured_happy.yaml").exists()

    def test_generate_no_i_capture_with_run_command_override_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--run-command normalizes the argv; verbatim-replay lock is lifted and -n 2+ works."""
        runner = CliRunner()
        argv = [sys.executable, str(FIXTURE_AGENT), "hello"]
        normalized = [sys.executable, str(FIXTURE_AGENT)]
        payload = json.dumps(
            {
                "scenarios": [
                    {**_generated_scenario(normalized), "id": "s1"},
                    {**_generated_scenario(normalized), "id": "s2"},
                ]
            }
        )
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: _FakeCompleter(payload))

        with runner.isolated_filesystem(temp_dir=tmp_path):
            assert runner.invoke(cli, ["capture", "--", *argv]).exit_code == 0
            gen = runner.invoke(
                cli,
                ["generate", "-n", "2", "--run-command", " ".join(normalized)],
            )
            assert gen.exit_code == 0, gen.output
            assert (Path(".kensa/scenarios") / "s1.yaml").exists()
            assert (Path(".kensa/scenarios") / "s2.yaml").exists()

    def test_bare_judge_in_capture_only_workspace_points_to_generate(self, tmp_path: Path) -> None:
        """kensa judge in a capture-only workspace hints `kensa generate`, not `kensa run`."""
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                ["capture", "--", sys.executable, str(FIXTURE_AGENT), "hello"],
            )
            assert result.exit_code == 0, result.output

            judged = runner.invoke(cli, ["judge"])
            assert judged.exit_code == 1
            assert "kensa generate" in judged.output
            assert "Run: kensa run" not in judged.output

    def test_generate_from_no_i_capture_emits_empty_scenario_input(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verbatim-replay captures must not double-append via scenario.input."""
        runner = CliRunner()
        captured_argv = [sys.executable, str(FIXTURE_AGENT), "refund this order please"]
        payload = json.dumps({"scenarios": [_generated_scenario(captured_argv)]})
        monkeypatch.setattr("kensa.llm.get_completer", lambda model=None: _FakeCompleter(payload))

        with runner.isolated_filesystem(temp_dir=tmp_path):
            capture = runner.invoke(
                cli,
                ["capture", "--", *captured_argv],
            )
            assert capture.exit_code == 0

            result = runner.invoke(cli, ["generate", "-n", "1"])

            assert result.exit_code == 0, result.output
            scenario = yaml.safe_load(Path(".kensa/scenarios/captured_happy.yaml").read_text())
            assert scenario["run_command"] == captured_argv
            assert "input" not in scenario or scenario["input"] in (None, "")
