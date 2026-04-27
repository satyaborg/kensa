from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from kensa.cli import cli
from kensa.skills_install import (
    bundled_skills_dir,
    discover_skills,
    ensure_cli_in_project,
    install_skills,
    target_dirs,
)


def test_bundled_skills_dir_exists_and_has_skills() -> None:
    root = bundled_skills_dir()
    assert root.is_dir()
    skills = discover_skills(root)
    names = {s.name for s in skills}
    assert {
        "audit-evals",
        "diagnose-errors",
        "generate-judges",
        "generate-scenarios",
        "validate-judge",
    } <= names


def test_discover_skills_skips_top_level_files(tmp_path: Path) -> None:
    (tmp_path / "evals-directive.md").write_text("not a skill")
    (tmp_path / "real-skill").mkdir()
    (tmp_path / "real-skill" / "SKILL.md").write_text("---\nname: real\n---\n")
    (tmp_path / "empty-dir").mkdir()
    found = {s.name for s in discover_skills(tmp_path)}
    assert found == {"real-skill"}


def test_target_dirs_default_writes_both() -> None:
    base = Path("/tmp/x")
    assert target_dirs(base, claude=True, codex=True) == [
        base / ".claude" / "skills",
        base / ".agents" / "skills",
    ]


def test_target_dirs_claude_only() -> None:
    assert target_dirs(Path("/x"), claude=True, codex=False) == [Path("/x/.claude/skills")]


def test_target_dirs_codex_only() -> None:
    assert target_dirs(Path("/x"), claude=False, codex=True) == [Path("/x/.agents/skills")]


def test_install_skills_requires_at_least_one_target() -> None:
    with pytest.raises(ValueError, match="at least one"):
        install_skills(claude=False, codex=False)


def test_install_skills_project_writes_both_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = install_skills(project=True, claude=True, codex=True)
    assert (tmp_path / ".claude" / "skills" / "audit-evals" / "SKILL.md").is_file()
    assert (tmp_path / ".agents" / "skills" / "audit-evals" / "SKILL.md").is_file()
    assert any(".claude/skills" in p for p in result.targets)
    assert any(".agents/skills" in p for p in result.targets)
    assert result.written
    assert not result.skipped


def test_install_skills_global_writes_to_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    install_skills(project=False, claude=True, codex=True)
    assert (tmp_path / ".claude" / "skills" / "audit-evals" / "SKILL.md").is_file()
    assert (tmp_path / ".agents" / "skills" / "audit-evals" / "SKILL.md").is_file()


def test_install_skills_skips_existing_without_force(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    install_skills(project=True, claude=True, codex=False)
    second = install_skills(project=True, claude=True, codex=False)
    assert second.skipped
    assert not second.written


def test_install_skills_force_overwrites(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    install_skills(project=True, claude=True, codex=False)
    target = tmp_path / ".claude" / "skills" / "audit-evals" / "SKILL.md"
    target.write_text("STALE")
    install_skills(project=True, claude=True, codex=False, force=True)
    assert target.read_text() != "STALE"


def test_install_skills_claude_only_skips_agents(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    install_skills(project=True, claude=True, codex=False)
    assert (tmp_path / ".claude" / "skills").is_dir()
    assert not (tmp_path / ".agents").exists()


def test_install_skills_codex_only_skips_claude(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    install_skills(project=True, claude=False, codex=True)
    assert (tmp_path / ".agents" / "skills").is_dir()
    assert not (tmp_path / ".claude").exists()


def test_skills_install_cli_default_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["skills", "install"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".claude" / "skills" / "audit-evals" / "SKILL.md").is_file()
    assert (tmp_path / ".agents" / "skills" / "audit-evals" / "SKILL.md").is_file()


def test_skills_install_cli_global(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["skills", "install", "--global"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".claude" / "skills" / "audit-evals" / "SKILL.md").is_file()


def test_skills_install_cli_claude_codex_mutually_exclusive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["skills", "install", "--claude", "--codex"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_init_no_skills_flag_skips_install(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--blank", "--no-skills"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".agents").exists()


def test_init_skills_flag_installs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--blank", "--skills"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".claude" / "skills" / "audit-evals" / "SKILL.md").is_file()
    assert (tmp_path / ".agents" / "skills" / "audit-evals" / "SKILL.md").is_file()


def test_init_non_interactive_does_not_prompt(tmp_path: Path, monkeypatch) -> None:
    """Without TTY and without --skills/--no-skills, init must not install or prompt."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--blank"], input="")
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".agents").exists()


def test_ensure_cli_in_project_no_pyproject(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = ensure_cli_in_project()
    assert result.status == "no_pyproject"


def test_ensure_cli_in_project_no_uv(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    monkeypatch.chdir(tmp_path)
    with patch("kensa.skills_install.shutil.which", return_value=None):
        result = ensure_cli_in_project()
    assert result.status == "no_uv"


def test_ensure_cli_in_project_runs_uv_add(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    monkeypatch.chdir(tmp_path)
    with (
        patch("kensa.skills_install.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "kensa.skills_install.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ) as mock_run,
    ):
        result = ensure_cli_in_project()
    assert result.status == "added"
    mock_run.assert_called_once()
    args = mock_run.call_args.args[0]
    assert args == ["uv", "add", "--dev", "kensa"]


def test_ensure_cli_in_project_handles_uv_failure(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    monkeypatch.chdir(tmp_path)
    with (
        patch("kensa.skills_install.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "kensa.skills_install.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="resolution failed"
            ),
        ),
    ):
        result = ensure_cli_in_project()
    assert result.status == "failed"
    assert "resolution failed" in result.detail


def test_init_no_cli_flag_skips_uv_add(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    with patch("kensa.skills_install.subprocess.run") as mock_run:
        result = runner.invoke(cli, ["init", "--blank", "--no-cli", "--no-skills"])
    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()


def test_init_cli_flag_runs_uv_add(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    with (
        patch("kensa.skills_install.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "kensa.skills_install.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ) as mock_run,
    ):
        result = runner.invoke(cli, ["init", "--blank", "--cli", "--no-skills"])
    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()


def test_init_warns_when_project_env_mutated(tmp_path: Path, monkeypatch) -> None:
    """After uv add succeeds, init must flag that doctor checks reflect the wrong env."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    with (
        patch("kensa.skills_install.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "kensa.skills_install.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ),
    ):
        result = runner.invoke(cli, ["init", "--blank", "--cli", "--no-skills"])
    assert result.exit_code == 0, result.output
    assert "uv run kensa doctor" in result.output


def test_init_does_not_warn_when_uv_add_skipped(tmp_path: Path, monkeypatch) -> None:
    """No warning when no project env was mutated (no_pyproject path)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--blank", "--cli", "--no-skills"])
    assert result.exit_code == 0, result.output
    assert "uv run kensa doctor" not in result.output


def test_init_does_not_warn_when_running_in_project_venv(tmp_path: Path, monkeypatch) -> None:
    """If sys.prefix points at the CWD's .venv, doctor checks are accurate, no warning."""
    import sys as real_sys

    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    project_venv = tmp_path / ".venv"
    project_venv.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(real_sys, "prefix", str(project_venv))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()
    with (
        patch("kensa.skills_install.shutil.which", return_value="/usr/bin/uv"),
        patch(
            "kensa.skills_install.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ),
    ):
        result = runner.invoke(cli, ["init", "--blank", "--cli", "--no-skills"])
    assert result.exit_code == 0, result.output
    assert "uv run kensa doctor" not in result.output


def test_init_force_does_not_overwrite_skills(tmp_path: Path, monkeypatch) -> None:
    """init --force regenerates the example scenario but must NOT clobber installed skills."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()

    runner.invoke(cli, ["init", "--blank", "--skills"])
    user_edit = tmp_path / ".claude" / "skills" / "audit-evals" / "SKILL.md"
    user_edit.write_text("USER LOCAL EDIT")

    result = runner.invoke(cli, ["init", "--blank", "--skills", "--force"])
    assert result.exit_code == 0, result.output
    assert user_edit.read_text() == "USER LOCAL EDIT", "init --force must not clobber skills"
