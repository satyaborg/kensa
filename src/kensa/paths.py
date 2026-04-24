"""Centralized path resolution for all .kensa/ directories and files."""

from __future__ import annotations

import json
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

    Raises FileNotFoundError if no eval manifests exist. If only capture
    manifests are present, the error points the user at ``kensa generate``
    rather than ``kensa run`` so capture-first workspaces aren't silently
    told to start over.
    """
    if not RUN_DIR.exists():
        raise FileNotFoundError("No runs found. Run `kensa run` first.")
    manifests = sorted(RUN_DIR.glob("*.json"), reverse=True)
    if not manifests:
        raise FileNotFoundError("No run manifests found. Run `kensa run` first.")
    saw_capture = False
    for path in manifests:
        try:
            kind = json.loads(path.read_text()).get("kind", "eval")
        except (OSError, ValueError, TypeError):
            continue
        if kind == "eval":
            return path
        if kind == "capture":
            saw_capture = True
    if saw_capture:
        raise FileNotFoundError(
            "No eval runs yet. Found capture run(s); turn them into scenarios with "
            "`kensa generate`, then `kensa run`."
        )
    raise FileNotFoundError("No run manifests found. Run `kensa run` first.")


def latest_capture_manifest() -> Path:
    """Return the path to the most recent capture manifest."""
    if not RUN_DIR.exists():
        raise FileNotFoundError("No runs found. Run `kensa capture` first.")
    manifests = sorted(RUN_DIR.glob("*.json"), reverse=True)
    if not manifests:
        raise FileNotFoundError("No capture manifests found. Run `kensa capture` first.")
    for path in manifests:
        try:
            kind = json.loads(path.read_text()).get("kind", "eval")
        except (OSError, ValueError, TypeError):
            continue
        if kind == "capture":
            return path
    raise FileNotFoundError("No capture manifests found. Run `kensa capture` first.")
