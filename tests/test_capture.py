"""Tests for the `kensa capture` CLI command."""

from __future__ import annotations

import sys
from pathlib import Path

from click.testing import CliRunner

from kensa.cli import cli
from kensa.models import RunKind, RunManifest

FIXTURE_AGENT = Path(__file__).parent / "fixtures" / "capture_agent.py"


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
