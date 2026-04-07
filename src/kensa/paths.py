"""Centralized path resolution for all .kensa/ directories and files."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(".kensa")
SCENARIO_DIR = ROOT / "scenarios"
TRACE_DIR = ROOT / "traces"
RUN_DIR = ROOT / "runs"
RESULT_DIR = ROOT / "results"
REPORT_DIR = ROOT / "reports"
JUDGE_DIR = ROOT / "judges"
AGENT_DIR = ROOT / "agents"


def manifest_path(run_id: str) -> Path:
    return RUN_DIR / f"{run_id}.json"


def results_path(run_id: str) -> Path:
    return RESULT_DIR / f"{run_id}.json"


def report_path(run_id: str, ext: str = "html") -> Path:
    return REPORT_DIR / f"{run_id}.{ext}"


def judge_prompt_path(name: str) -> Path:
    path = (JUDGE_DIR / f"{name}.yaml").resolve()
    if not path.is_relative_to(JUDGE_DIR.resolve()):
        raise ValueError(f"Judge name escapes judges directory: {name}")
    return path


def latest_report_link() -> Path:
    return REPORT_DIR / "latest.html"


def latest_manifest() -> Path:
    """Return the path to the most recent run manifest.

    Raises FileNotFoundError if no manifests exist.
    """
    if not RUN_DIR.exists():
        raise FileNotFoundError("No runs found. Run `kensa run` first.")
    manifests = sorted(RUN_DIR.glob("*.json"))
    if not manifests:
        raise FileNotFoundError("No run manifests found. Run `kensa run` first.")
    return manifests[-1]
