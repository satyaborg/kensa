"""Scenario runner: validate, template-substitute, subprocess, span capture, trace write."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from kensa.models import RunManifest, Scenario, ScenarioRun, Span
from kensa.paths import RUN_DIR, SCENARIO_DIR, TRACE_DIR
from kensa.translate import oi_to_kensa

DEFAULT_TIMEOUT = 300

# Env vars that scenario overrides must never replace.
_PROTECTED_ENV_VARS: frozenset[str] = frozenset(
    {
        "KENSA_TRACE_DIR",
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "PYTHONPATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
    }
)


def _find_dotenv() -> Path | None:
    """Walk up from cwd to find the nearest .env file."""
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_dotenv() -> dict[str, str]:
    """Load KEY=VALUE pairs from the nearest .env file (walks up from cwd).

    Skips comments and blank lines. Strips surrounding quotes from values.
    """
    env_path = _find_dotenv()
    if env_path is None:
        return {}
    result: dict[str, str] = {}
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key:
                result[key] = value
    except OSError:
        pass
    return result


def ensure_dotenv_loaded() -> None:
    """Load the nearest .env into ``os.environ`` (no-op if keys already set)."""
    for k, v in load_dotenv().items():
        if k not in os.environ:
            os.environ[k] = v


def load_scenario(path: Path) -> Scenario:
    """Load a scenario from a YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return Scenario(**data)


def load_dataset(scenario_dir: Path, filename: str) -> list[dict[str, Any]]:
    """Read a JSONL dataset file from the scenario directory.

    Each line is a JSON object. Blank lines are skipped.
    """
    path = (scenario_dir / filename).resolve()
    if not path.is_relative_to(scenario_dir.resolve()):
        raise ValueError(f"Dataset path escapes scenario directory: {filename}")
    rows: list[dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_scenarios(
    scenario_dir: str = str(SCENARIO_DIR),
    scenario_ids: list[str] | None = None,
) -> list[Scenario]:
    """Load scenarios from a directory, optionally filtering by IDs."""
    dir_path = Path(scenario_dir)
    try:
        contents = list(dir_path.iterdir())
    except (FileNotFoundError, NotADirectoryError) as e:
        raise FileNotFoundError(f"Scenario directory not found: {scenario_dir}") from e

    yaml_files = sorted(p for p in contents if p.suffix in (".yaml", ".yml"))
    scenarios: list[Scenario] = []
    for path in yaml_files:
        scenario = load_scenario(path)
        if scenario_ids is None or scenario.id in scenario_ids:
            scenarios.append(scenario)

    if scenario_ids and len(scenarios) != len(scenario_ids):
        found = {s.id for s in scenarios}
        missing = set(scenario_ids) - found
        raise ValueError(f"Scenarios not found: {missing}")

    return scenarios


def _build_command(command: list[str], input_value: str | dict[str, Any]) -> list[str]:
    """Build the subprocess argv from a list-form ``run_command`` and input.

    The input is appended as the final argv element when non-empty. Dict inputs
    are JSON-serialized. No shell, no parsing, no template substitution: every
    element of ``command`` is passed verbatim to ``subprocess.run``, so shell
    metacharacters in the input cannot be interpreted.
    """
    if not command:
        raise ValueError("run_command must be a non-empty list of argv elements")
    input_str = json.dumps(input_value) if isinstance(input_value, dict) else str(input_value)
    if input_str == "":
        return list(command)
    return [*command, input_str]


def _read_spans(trace_dir: Path) -> list[Span]:
    """Read OI spans from a trace directory and translate to kensa format."""
    spans: list[Span] = []
    spans_file = trace_dir / "spans.jsonl"
    if not spans_file.exists():
        return spans

    with open(spans_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            oi_span = json.loads(line)
            spans.append(oi_to_kensa(oi_span))
    return spans


def _write_trace(spans: list[Span], output_path: Path) -> None:
    """Write kensa spans as JSONL."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for span in spans:
            f.write(span.model_dump_json() + "\n")


def _trace_filename(scenario_id: str) -> str:
    """Build the persisted trace filename for a single scenario execution."""
    return f"{scenario_id}_{time.time_ns() // 1_000_000}.jsonl"


def run_scenario(
    scenario: Scenario,
    trace_dir: str = str(TRACE_DIR),
    timeout: int = DEFAULT_TIMEOUT,
    input_override: str | dict[str, Any] | None = None,
) -> tuple[str, ScenarioRun]:
    """Execute a single scenario.

    Returns (scenario_id, ScenarioRun) with trace path and metadata.
    Distinguishes three failure modes: subprocess crash, timeout, and no spans.
    When *input_override* is provided it replaces ``scenario.input`` for this
    execution (used by dataset expansion).
    """
    trace_filename = _trace_filename(scenario.id)
    trace_path = Path(trace_dir) / trace_filename

    with tempfile.TemporaryDirectory(prefix="kensa_") as tmp_dir:
        env = os.environ.copy()
        env.update(load_dotenv())
        safe_overrides = {
            k: v for k, v in scenario.env_overrides.items() if k not in _PROTECTED_ENV_VARS
        }
        env.update(safe_overrides)
        env["KENSA_TRACE_DIR"] = tmp_dir

        effective_input = input_override if input_override is not None else scenario.input
        argv = _build_command(scenario.run_command, effective_input)

        start = time.monotonic()
        stdout_output = ""
        stderr_output = ""
        timed_out = False
        try:
            result = subprocess.run(
                argv,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            exit_code = result.returncode
            stdout_output = result.stdout
            stderr_output = result.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code = -1
            timed_out = True
            stdout_output = str(exc.stdout or "")
            stderr_output = str(exc.stderr or "")
        duration = time.monotonic() - start

        spans = _read_spans(Path(tmp_dir))

    if not spans:
        if timed_out:
            msg = f"Scenario {scenario.id!r} timed out after {timeout}s."
        elif exit_code != 0:
            stderr_tail = stderr_output.strip()[-500:] if stderr_output else "(no stderr)"
            msg = (
                f"Scenario {scenario.id!r} subprocess crashed (exit={exit_code}).\n"
                f"stderr:\n{stderr_tail}"
            )
        else:
            msg = (
                f"Scenario {scenario.id!r} ran successfully but produced no traces.\n"
                "Ensure the agent calls: from kensa import instrument; instrument()\n"
                "and that the matching SDK instrumentor is installed "
                "(e.g. uv add kensa[openai] or: pip install kensa[openai])."
            )
            if stderr_output.strip():
                msg += f"\nstderr:\n{stderr_output.strip()[-500:]}"
        raise RuntimeError(msg)

    _write_trace(spans, trace_path)

    return scenario.id, ScenarioRun(
        trace_path=str(trace_path),
        exit_code=exit_code,
        duration_seconds=round(duration, 2),
        stdout=stdout_output.strip()[-2000:] if stdout_output else "",
        stderr=stderr_output.strip()[-500:] if stderr_output else "",
        input=effective_input if effective_input != "" else None,
    )


def _run_one(
    scenario: Scenario,
    trace_dir: str,
    timeout: int,
    input_override: str | dict[str, Any] | None = None,
    dataset_row: dict[str, Any] | None = None,
) -> ScenarioRun:
    """Run a single scenario, returning a ScenarioRun even on failure."""
    effective_input = input_override if input_override is not None else scenario.input
    try:
        _, run_result = run_scenario(scenario, trace_dir, timeout, input_override)
        run_result.dataset_row = dataset_row
        return run_result
    except Exception as e:
        return ScenarioRun(
            trace_path="",
            exit_code=-1,
            duration_seconds=0.0,
            stderr=str(e)[-500:],
            input=effective_input if effective_input != "" else None,
            dataset_row=dataset_row,
        )


def run_scenarios(
    scenario_dir: str = str(SCENARIO_DIR),
    scenario_ids: list[str] | None = None,
    trace_dir: str = str(TRACE_DIR),
    timeout: int = DEFAULT_TIMEOUT,
    scenarios: list[Scenario] | None = None,
) -> RunManifest:
    """Run all scenarios (or filtered subset) and return a manifest.

    Continues on individual scenario failure so partial results are available.
    Failed scenarios get exit_code=-1 and the error in stderr.

    Pass pre-loaded ``scenarios`` to skip the redundant ``load_scenarios`` call.
    """
    if scenarios is None:
        scenarios = load_scenarios(scenario_dir, scenario_ids)
    timestamp = datetime.now(tz=timezone.utc)
    run_id = timestamp.strftime("%Y%m%dT%H%M%S%f")[:18]

    manifest = RunManifest(run_id=run_id, timestamp=timestamp)

    for scenario in scenarios:
        if scenario.dataset and scenario.input_field:
            rows = load_dataset(Path(scenario_dir), scenario.dataset)
            runs: list[ScenarioRun] = []
            for i, row in enumerate(rows, 1):
                field = scenario.input_field
                if field not in row:
                    raise KeyError(
                        f"Row {i} of {scenario.dataset} missing field {field!r}. "
                        f"Available: {list(row.keys())}"
                    )
                runs.append(
                    _run_one(
                        scenario,
                        trace_dir,
                        timeout,
                        input_override=row[field],
                        dataset_row=row,
                    )
                )
            manifest.scenarios[scenario.id] = runs
        else:
            manifest.scenarios[scenario.id] = [_run_one(scenario, trace_dir, timeout)]

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    manifest_file = RUN_DIR / f"{run_id}.json"
    with open(manifest_file, "w") as f:
        f.write(manifest.model_dump_json(indent=2))

    return manifest


def read_trace(trace_path: str) -> list[Span]:
    """Read kensa spans from a JSONL trace file."""
    spans: list[Span] = []
    with open(trace_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            spans.append(Span.model_validate_json(line))
    return spans
