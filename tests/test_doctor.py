"""Tests for the doctor module."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from kensa.cli import cli
from kensa.doctor import (
    _check_api_key,
    _check_dotenv,
    _check_python_version,
    _check_scenarios,
    _check_sdk,
    _check_trace_dir_writable,
    _detect_sdks,
    format_doctor,
)
from kensa.utils import detect_package_manager, install_hint


class TestCheckPythonVersion:
    def test_current_python_passes(self) -> None:
        ok, detail = _check_python_version()
        assert ok
        assert "Python" in detail


class TestCheckScenarios:
    def test_missing_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        ok, detail = _check_scenarios()
        assert not ok
        assert "does not exist" in detail

    def test_empty_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".kensa" / "scenarios").mkdir(parents=True)
        ok, detail = _check_scenarios()
        assert not ok
        assert "no YAML" in detail

    def test_has_scenarios(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        d = tmp_path / ".kensa" / "scenarios"
        d.mkdir(parents=True)
        (d / "test.yaml").write_text("id: test")
        ok, detail = _check_scenarios()
        assert ok
        assert "1 scenario" in detail


class TestCheckApiKey:
    def test_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
        ok, detail = _check_api_key("ANTHROPIC_API_KEY")
        assert ok
        assert "set" in detail
        assert "chars" not in detail

    def test_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        ok, detail = _check_api_key("ANTHROPIC_API_KEY")
        assert not ok
        assert "not set" in detail


class TestCheckTraceDirWritable:
    def test_writable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        ok, detail = _check_trace_dir_writable()
        assert ok
        assert "writable" in detail


class TestCheckDotenv:
    def test_no_dotenv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        ok, _ = _check_dotenv()
        assert not ok

    def test_has_dotenv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("KEY=value")
        ok, detail = _check_dotenv()
        assert ok
        assert ".env" in detail


class TestFormatDoctor:
    def test_all_pass(self, capsys: pytest.CaptureFixture[str]) -> None:
        checks = [("python", True, "Python 3.12"), ("ruff", True, "found")]
        format_doctor(checks)
        output = capsys.readouterr().out
        assert "2/2" in output
        assert "✓" in output

    def test_failure_shows_fix(self, capsys: pytest.CaptureFixture[str]) -> None:
        checks = [
            ("python", False, "Python 3.8"),
            ("ANTHROPIC_API_KEY", True, "set"),
        ]
        format_doctor(checks)
        output = capsys.readouterr().out
        assert "✗" in output
        assert "fix: python" in output

    def test_no_api_keys_shows_fix(self, capsys: pytest.CaptureFixture[str]) -> None:
        checks = [
            ("python", True, "Python 3.12"),
            ("ANTHROPIC_API_KEY", False, "not set"),
            ("OPENAI_API_KEY", False, "not set"),
        ]
        format_doctor(checks)
        output = capsys.readouterr().out
        assert "at least one API key" in output


class TestDetectPackageManager:
    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        detect_package_manager.cache_clear()

    def test_uv_lock(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "uv.lock").write_text("")
        assert detect_package_manager() == "uv"

    def test_pyproject_with_tool_uv(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text("[tool.uv]\n")
        assert detect_package_manager() == "uv"

    def test_pipfile(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "Pipfile").write_text("")
        assert detect_package_manager() == "pipenv"

    def test_requirements_txt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "requirements.txt").write_text("")
        assert detect_package_manager() == "pip"

    def test_fallback_pip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert detect_package_manager() == "pip"


class TestInstallHint:
    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        detect_package_manager.cache_clear()

    def test_uv_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "uv.lock").write_text("")
        assert install_hint("openai") == 'uv add "kensa[openai]"'

    def test_pip_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert install_hint("openai") == 'pip install "kensa[openai]"'


class TestDetectSdks:
    def test_detects_openai_from_scenario(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        scenarios = tmp_path / ".kensa" / "scenarios"
        scenarios.mkdir(parents=True)
        agent = tmp_path / "agent.py"
        agent.write_text("from openai import OpenAI\nclient = OpenAI()\n")
        (scenarios / "test.yaml").write_text(
            "id: test\nname: test\nrun_command: python agent.py {{input}}\n"
        )
        assert "openai" in _detect_sdks()

    def test_detects_anthropic_from_agents_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".kensa" / "scenarios").mkdir(parents=True)
        agents = tmp_path / ".kensa" / "agents"
        agents.mkdir(parents=True)
        (agents / "bot.py").write_text("import anthropic\n")
        assert "anthropic" in _detect_sdks()

    def test_empty_when_no_scenarios(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert _detect_sdks() == set()

    def test_detects_from_uv_run_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        scenarios = tmp_path / ".kensa" / "scenarios"
        scenarios.mkdir(parents=True)
        agent = tmp_path / "agent.py"
        agent.write_text("from langchain.chat_models import ChatOpenAI\n")
        (scenarios / "test.yaml").write_text(
            "id: test\nname: test\nrun_command: uv run python agent.py {{input}}\n"
        )
        assert "langchain" in _detect_sdks()


class TestCheckSdk:
    def test_both_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("kensa.doctor._is_importable", lambda _name: True)
        ok, detail = _check_sdk("openai")
        assert ok
        assert "instrumentor installed" in detail

    def test_sdk_only_no_instrumentor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "kensa.doctor._is_importable",
            lambda name: not name.startswith("openinference"),
        )
        ok, detail = _check_sdk("openai")
        assert not ok
        assert "instrumentor missing" in detail

    def test_nothing_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("kensa.doctor._is_importable", lambda _name: False)
        ok, detail = _check_sdk("openai")
        assert not ok
        assert "not installed" in detail


class TestDoctorCli:
    def test_doctor_command_runs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        d = tmp_path / ".kensa" / "scenarios"
        d.mkdir(parents=True)
        (d / "test.yaml").write_text("id: test")
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor"])
        assert "kensa doctor" in result.output
        assert "python" in result.output.lower()
