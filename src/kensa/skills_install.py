"""Install bundled skills into Claude Code and open-standard agent skill directories."""

from __future__ import annotations

import shutil
import subprocess
from importlib.resources import as_file, files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

CliInstallStatus = Literal["added", "no_pyproject", "no_uv", "failed"]


class CliInstallResult(BaseModel):
    status: CliInstallStatus
    detail: str


class InstallResult(BaseModel):
    written: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)


def bundled_skills_dir() -> Path:
    with as_file(files("kensa") / "skills") as p:
        return Path(p)


def discover_skills(src: Path | None = None) -> list[Path]:
    """Return directories under the bundled skills root that contain SKILL.md."""
    root = src or bundled_skills_dir()
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())


def target_dirs(base: Path, claude: bool, agents: bool) -> list[Path]:
    targets: list[Path] = []
    if claude:
        targets.append(base / ".claude" / "skills")
    if agents:
        targets.append(base / ".agents" / "skills")
    return targets


def install_skills(
    *,
    project: bool = True,
    claude: bool = True,
    agents: bool = True,
    force: bool = False,
    src: Path | None = None,
) -> InstallResult:
    """Copy bundled skills into .claude/skills and/or .agents/skills."""
    if not (claude or agents):
        raise ValueError("at least one of claude or agents must be enabled")

    base = Path.cwd() if project else Path.home()
    skills = discover_skills(src)
    targets = target_dirs(base, claude=claude, agents=agents)

    written: list[str] = []
    skipped: list[str] = []
    for target_root in targets:
        target_root.mkdir(parents=True, exist_ok=True)
        for skill_src in skills:
            dest = target_root / skill_src.name
            if dest.exists():
                if not force:
                    skipped.append(str(dest))
                    continue
                shutil.rmtree(dest)
            shutil.copytree(skill_src, dest)
            written.append(str(dest))

    return InstallResult(
        written=written,
        skipped=skipped,
        targets=[str(t) for t in targets],
    )


def ensure_cli_in_project() -> CliInstallResult:
    """Run ``uv add --dev kensa`` if pyproject.toml is present and uv is on PATH."""
    pyproject = Path.cwd() / "pyproject.toml"
    if not pyproject.exists():
        return CliInstallResult(
            status="no_pyproject",
            detail="no pyproject.toml in CWD. Install kensa however your project prefers.",
        )
    if not shutil.which("uv"):
        return CliInstallResult(
            status="no_uv",
            detail="uv not on PATH. Install with: pip install kensa",
        )
    completed = subprocess.run(
        ["uv", "add", "--dev", "kensa"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return CliInstallResult(
            status="failed",
            detail=(
                f"uv add --dev kensa failed: {completed.stderr.strip() or completed.stdout.strip()}"
            ),
        )
    return CliInstallResult(status="added", detail="kensa added to dev deps in pyproject.toml")
