"""Synthesize scenario YAMLs from captured traces via an LLM."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import yaml

from kensa.models import Check, CheckType, RunKind, RunManifest, Scenario, Span
from kensa.paths import SCENARIO_DIR, latest_capture_manifest, latest_manifest, manifest_path

MAX_TRACES_IN_PROMPT = 10
MAX_OUTPUT_CHARS = 500

# Filesystem-safe scenario ids: snake_case-ish, no separators, no dots.
_SAFE_SCENARIO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class InvalidScenarioIdError(ValueError):
    """Raised when a scenario id cannot safely be used as a filename."""


def _validate_scenario_id(scenario_id: str) -> str:
    """Reject ids that could escape the target directory or shadow control chars."""
    if not _SAFE_SCENARIO_ID.match(scenario_id):
        raise InvalidScenarioIdError(
            f"Invalid scenario id {scenario_id!r}: must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}} "
            "(no path separators, dots, or whitespace)."
        )
    return scenario_id


_NUMERIC_CHECK_PARAMS: dict[CheckType, tuple[str, ...]] = {
    CheckType.MAX_COST: ("max", "max_usd"),
    CheckType.MAX_TURNS: ("max",),
    CheckType.MAX_DURATION: ("max_seconds",),
}


def _validate_generated_check_params(check: Check) -> None:
    """Reject wrong-typed numeric bounds and empty output_contains/matches values."""
    if check.type in _NUMERIC_CHECK_PARAMS:
        keys = _NUMERIC_CHECK_PARAMS[check.type]
        present = {k: check.params[k] for k in keys if k in check.params}
        if not present:
            raise ValueError(f"{check.type.value}: missing numeric bound ({' or '.join(keys)})")
        for key, value in present.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"{check.type.value}: '{key}' must be numeric, got {type(value).__name__}"
                )
    elif check.type == CheckType.OUTPUT_CONTAINS:
        value = check.params.get("value")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("output_contains: 'value' must be a non-empty string")
    elif check.type == CheckType.OUTPUT_MATCHES:
        pattern = check.params.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("output_matches: 'pattern' must be a non-empty string")


def _validate_generated_scenario(
    scenario: Scenario, allowed_run_commands: list[list[str]] | None = None
) -> None:
    """Stricter than Scenario.model_validate: generated scenarios must be runnable and testable."""
    if not scenario.run_command:
        raise ValueError("run_command is empty; generator requires an executable entrypoint")
    if allowed_run_commands is not None and list(scenario.run_command) not in allowed_run_commands:
        raise ValueError(
            f"run_command {scenario.run_command} not in observed entrypoints "
            f"{allowed_run_commands}; LLM hallucinated"
        )
    if scenario.judge:
        raise ValueError(
            "generated scenarios must use 'criteria' (inline string), not 'judge' (file ref); "
            "generate does not create judge prompt files"
        )
    if not scenario.checks and not scenario.criteria:
        raise ValueError(
            "scenario has no checks and no judge criterion; it would pass automatically"
        )
    check_types = {check.type for check in scenario.checks}
    if CheckType.MAX_COST not in check_types and CheckType.MAX_TURNS not in check_types:
        raise ValueError(
            "scenario must include at least one of max_cost or max_turns (prompt hard rule)"
        )
    for check in scenario.checks:
        _validate_generated_check_params(check)


_SYSTEM_PROMPT = """\
You design eval scenarios for AI agents using the kensa framework. You are
given summaries of real agent executions (traces) and must propose scenario
YAML files that would usefully test the same agent in the future.

Return a JSON object with a single key "scenarios" whose value is an array.
Each array element must be a scenario object matching the schema below.
Do NOT wrap the response in markdown fences. Do NOT include prose.

Cover as many of these categories as the traces support:
1. Happy path: basic functionality works
2. Tool usage: correct tools get called
3. Edge case: unusual, empty, or long input
4. Error handling: failure modes
5. Cost/latency bounds: stays within limits

Ground every scenario in evidence from the supplied traces: reuse real inputs,
reference actual tool names, set cost/turn bounds from observed values.
"""


_SCHEMA_BLOCK = """\
Scenario JSON schema (field order matters for YAML readability):

{
  "id": "snake_case_unique_id",
  "name": "Human-readable name",
  "description": "What this tests and why",
  "source": "traces",
  "input": "string OR object — what to send the agent",
  "run_command": ["argv", "list", "verbatim"],
  "expected_outcome": "Plain-language expected behavior",
  "checks": [
    { "type": "output_contains", "params": {"value": "key phrase"},
      "description": "..." },
    { "type": "tools_called", "params": {"tools": ["tool_name"]},
      "description": "..." },
    { "type": "max_cost", "params": {"max": 0.10},
      "description": "..." },
    { "type": "max_turns", "params": {"max": 5},
      "description": "..." }
  ],
  "criteria": "Natural language criterion for LLM judge (optional; omit or set to null)"
}

Available check types:
- output_contains: params { value: string, case_sensitive?: bool }
- output_matches:  params { pattern: regex }         (avoid for LLM output)
- tools_called:    params { tools: [string] }
- tools_not_called:params { tools: [string] }
- tool_order:      params { order: [string] }        (use sparingly)
- max_cost:        params { max: float }             (USD)
- max_turns:       params { max: int }               (LLM call count)
- max_duration:    params { max_seconds: float }
- no_repeat_calls: params {}

Hard rules:
- run_command MUST be a list (e.g. ["python", "agent.py"]), never a string.
- Every scenario MUST include at least one of max_cost or max_turns.
- Prefer output_contains with a key phrase over output_matches.
- "criteria" and "judge" are mutually exclusive. Use "criteria" (string) for
  subjective judging, or omit both for check-only scenarios.
- Keep ids unique across the array and snake_case.
- tools_called and tools_not_called MUST list at least one specific tool name
  observed in the traces. Never use an empty list, a wildcard ("*"),
  or placeholder names like "any_tool". If the agent called no tools at all,
  omit both checks instead of inventing one.
"""


def resolve_trace_paths(
    run_id: str | None,
    traces: tuple[Path, ...] | None,
) -> list[Path]:
    """Pick trace files to feed the generator.

    Priority: explicit ``--trace`` paths > ``--run-id`` manifest lookup > latest run.
    Raises FileNotFoundError when nothing resolves.
    """
    if traces:
        return [Path(t) for t in traces]

    manifest_file = _resolve_generate_manifest(run_id)
    manifest = RunManifest.model_validate_json(manifest_file.read_text())

    if manifest.kind == RunKind.CAPTURE:
        if not manifest.trace_path:
            raise FileNotFoundError(
                f"Capture {manifest.run_id} has no trace file. Re-run `kensa capture` with an "
                "instrumented SDK or call `kensa.instrument()`."
            )
        return [Path(manifest.trace_path)]

    paths: list[Path] = [
        Path(sr.trace_path) for runs in manifest.scenarios.values() for sr in runs if sr.trace_path
    ]
    if not paths:
        raise FileNotFoundError(
            f"Manifest {manifest.run_id} has no trace files. Run `kensa run` first."
        )
    return paths


def _resolve_generate_manifest(run_id: str | None) -> Path:
    """Resolve the manifest source for ``kensa generate``.

    Prefers an explicit run ID, then the latest capture, then the latest eval run.
    """
    if run_id:
        return manifest_path(run_id)
    try:
        return latest_capture_manifest()
    except FileNotFoundError:
        return latest_manifest()


def is_verbatim_replay_capture(run_id: str | None, trace_paths: list[Path] | None) -> bool:
    """Return True iff the resolved source manifest is a capture with no explicit ``-i`` input.

    Such captures have the prompt baked into ``manifest.command`` argv, so
    generated scenarios must leave ``scenario.input`` empty to avoid a
    double-append on replay.
    """
    manifest: RunManifest | None = None
    try:
        if run_id:
            manifest = RunManifest.model_validate_json(manifest_path(run_id).read_text())
        elif trace_paths:
            manifest = _find_manifest_for_traces(trace_paths)
        else:
            manifest = RunManifest.model_validate_json(_resolve_generate_manifest(None).read_text())
    except (FileNotFoundError, ValueError):
        return False
    if manifest is None:
        return False
    return manifest.kind == RunKind.CAPTURE and manifest.captured_input is None


def _id_to_run_command(scenario_dir: Path) -> dict[str, list[str]]:
    """Load every scenario in ``scenario_dir`` and return ``{scenario.id: run_command}``.

    Matches on the internal ``scenario.id`` (not the filename), since kensa
    accepts any ``.yaml`` filename for a scenario.
    """
    from kensa.runner import load_scenario

    mapping: dict[str, list[str]] = {}
    if not scenario_dir.is_dir():
        return mapping
    for path in sorted(scenario_dir.iterdir()):
        if path.suffix not in (".yaml", ".yml"):
            continue
        try:
            scenario = load_scenario(path)
        except Exception:
            continue
        if scenario.run_command:
            mapping[scenario.id] = list(scenario.run_command)
    return mapping


def _manifest_scenario_ids(run_id: str | None) -> set[str]:
    """Return the set of scenario ids recorded in a manifest. Empty on failure."""
    try:
        manifest_file = _resolve_generate_manifest(run_id)
        manifest = RunManifest.model_validate_json(manifest_file.read_text())
    except (FileNotFoundError, ValueError):
        return set()
    if manifest.kind != RunKind.EVAL:
        return set()
    return set(manifest.scenarios.keys())


def _find_manifest_for_traces(trace_paths: list[Path]) -> RunManifest | None:
    """Find the most recent manifest whose ScenarioRuns reference any of ``trace_paths``."""
    from kensa.paths import RUN_DIR

    if not RUN_DIR.is_dir():
        return None
    wanted = {p.resolve() for p in trace_paths}
    for manifest_file in sorted(RUN_DIR.glob("*.json"), reverse=True):
        try:
            manifest = RunManifest.model_validate_json(manifest_file.read_text())
        except (ValueError, OSError):
            continue
        if manifest.kind == RunKind.CAPTURE:
            if not manifest.trace_path:
                continue
            try:
                if Path(manifest.trace_path).resolve() in wanted:
                    return manifest
            except OSError:
                continue
        for runs in manifest.scenarios.values():
            for sr in runs:
                if not sr.trace_path:
                    continue
                try:
                    if Path(sr.trace_path).resolve() in wanted:
                        return manifest
                except OSError:
                    continue
    return None


def collect_run_commands(
    run_id: str | None,
    scenario_dir: Path,
    *,
    trace_paths: list[Path] | None = None,
) -> list[list[str]]:
    """Return the unique ``run_command``s referenced in a run's scenarios.

    Used to ground the LLM prompt so generated scenarios reuse the agent's
    real entry point instead of hallucinating ``python agent.py``.

    Scenario lookup goes by ``scenario.id`` (scans the directory), not by
    filename, since filenames don't have to match ids. When ``run_id`` is
    ``None`` and ``trace_paths`` is given, we search ``.kensa/runs/`` for a
    manifest that references any of those traces.
    Returns ``[]`` when nothing can be resolved.
    """
    manifest: RunManifest | None = None
    if run_id:
        try:
            manifest = RunManifest.model_validate_json(manifest_path(run_id).read_text())
        except (FileNotFoundError, ValueError):
            return []
    elif trace_paths:
        manifest = _find_manifest_for_traces(trace_paths)
    else:
        try:
            manifest = RunManifest.model_validate_json(_resolve_generate_manifest(None).read_text())
        except (FileNotFoundError, ValueError):
            return []
    if manifest is None:
        return []
    if manifest.kind == RunKind.CAPTURE:
        return [list(manifest.command)] if manifest.command else []

    ids = set(manifest.scenarios.keys())
    if not ids:
        return []

    id_map = _id_to_run_command(scenario_dir)
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for sid in ids:
        cmd = id_map.get(sid)
        if cmd is None:
            continue
        key = tuple(cmd)
        if key not in seen:
            seen.add(key)
            unique.append(cmd)
    return unique


def _first_user_input(span: Span) -> str:
    """Extract the last user message from an LLM span's input, best effort.

    Handles two OTel attribute shapes: ``input.messages`` (OpenInference) and
    ``input.value`` (JSON-serialized full request, used by some instrumentors).
    """
    if not span.input:
        return ""
    messages = _coerce_messages(span.input)
    for raw in reversed(messages):
        if not isinstance(raw, dict):
            continue
        msg = cast(dict[str, Any], raw)
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in cast(list[Any], content):
                if isinstance(block, dict):
                    text = cast(dict[str, Any], block).get("text")
                    if isinstance(text, str):
                        parts.append(text)
                else:
                    parts.append(str(block))
            return " ".join(parts)
    return ""


def _coerce_messages(payload: dict[str, Any]) -> list[object]:
    """Return a list of raw messages from either ``messages`` or ``value`` shape."""
    messages = payload.get("messages")
    if isinstance(messages, list):
        return cast(list[object], messages)
    value = payload.get("value")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, dict):
            inner = parsed.get("messages")
            if isinstance(inner, list):
                return cast(list[object], inner)
    elif isinstance(value, dict):
        inner = value.get("messages")
        if isinstance(inner, list):
            return cast(list[object], inner)
    return []


def _summarize_trace(spans: list[Span], trace_path: Path) -> dict[str, object]:
    """Compact summary of one trace for the LLM prompt."""
    from kensa.utils import count_tool_calls, get_agent_output, get_tool_names

    llm_spans = [s for s in spans if s.kind.value == "llm"]
    input_text = ""
    if llm_spans:
        first = min(llm_spans, key=lambda s: s.start_time)
        input_text = _first_user_input(first)

    output_text = get_agent_output(spans)
    if len(output_text) > MAX_OUTPUT_CHARS:
        output_text = output_text[:MAX_OUTPUT_CHARS] + "..."

    total_cost = sum(s.cost.total for s in spans if s.cost)
    total_tokens = sum(s.tokens.total for s in spans if s.tokens)
    duration = 0.0
    if spans:
        duration = (
            max(s.end_time for s in spans) - min(s.start_time for s in spans)
        ).total_seconds()

    return {
        "path": str(trace_path),
        "input": input_text[:MAX_OUTPUT_CHARS],
        "output": output_text,
        "tool_names": sorted(set(get_tool_names(spans))),
        "tool_call_count": count_tool_calls(spans),
        "llm_call_count": len(llm_spans),
        "total_tokens": total_tokens,
        "cost_usd": round(total_cost, 6),
        "duration_seconds": round(duration, 2),
        "status": "error" if any(s.status == "error" for s in spans) else "ok",
    }


def _build_prompt(
    summaries: list[dict[str, object]],
    count: int,
    run_commands: list[list[str]] | None = None,
) -> str:
    traces_json = json.dumps(summaries, indent=2)
    rc_block = ""
    if run_commands:
        rc_json = json.dumps(run_commands)
        rc_block = (
            f"\nObserved run_commands (use one of these verbatim, do NOT invent a path):\n"
            f"{rc_json}\n"
        )
    return (
        f"{_SYSTEM_PROMPT}\n"
        f"{_SCHEMA_BLOCK}\n"
        f"Produce exactly {count} scenarios grounded in the traces below.\n"
        f"{rc_block}"
        f"\nTraces ({len(summaries)}):\n{traces_json}\n"
    )


def _parse_response(text: str) -> list[dict[str, object]]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        stripped = "\n".join(lines).strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as err:
        raise ValueError(f"LLM did not return valid JSON: {stripped[:200]}") from err

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "scenarios" in data:
        items = data["scenarios"]
    else:
        raise ValueError(f"Expected JSON array or {{scenarios: [...]}}: {stripped[:200]}")

    if not isinstance(items, list):
        raise ValueError(f"'scenarios' must be a list, got {type(items).__name__}")
    return items


def generate_from_traces(
    trace_paths: list[Path],
    count: int = 3,
    model: str | None = None,
    *,
    run_commands: list[list[str]] | None = None,
    verbatim_replay: bool = False,
) -> list[Scenario]:
    """Read traces, ask an LLM for scenarios, return validated Scenario objects.

    When ``verbatim_replay`` is true, the source run_command already has the
    agent input baked into its argv (e.g. a capture without ``-i``). In that
    case the runner must not append ``scenario.input`` on replay, so any
    ``input`` the LLM emits is discarded before validation.
    """
    from kensa.llm import get_completer
    from kensa.runner import read_trace

    if not trace_paths:
        raise ValueError("generate_from_traces requires at least one trace path")

    selected = trace_paths[:MAX_TRACES_IN_PROMPT]
    summaries = [_summarize_trace(read_trace(str(p)), p) for p in selected]

    prompt = _build_prompt(summaries, count, run_commands=run_commands)
    completer = get_completer(model)
    raw = completer.complete(prompt, response_format="json")

    scenario_dicts = _parse_response(raw)
    if not scenario_dicts:
        raise ValueError("LLM returned zero scenarios")

    single_run_command = run_commands[0] if run_commands and len(run_commands) == 1 else None
    allowlist = run_commands if run_commands and len(run_commands) > 1 else None

    scenarios: list[Scenario] = []
    rejections: list[str] = []
    seen_ids: set[str] = set()
    for i, sd in enumerate(scenario_dicts):
        if not isinstance(sd, dict):
            rejections.append(f"#{i}: not a JSON object")
            continue
        sd.setdefault("source", "traces")
        if single_run_command is not None:
            sd["run_command"] = list(single_run_command)
        if verbatim_replay:
            sd["input"] = None
        try:
            candidate = Scenario.model_validate(sd)
            _validate_scenario_id(candidate.id)
            _validate_generated_scenario(candidate, allowed_run_commands=allowlist)
        except Exception as err:
            rejections.append(f"#{i} ({sd.get('id', '?')}): {err}")
            continue
        if candidate.id in seen_ids:
            rejections.append(f"#{i} ({candidate.id}): duplicate id")
            continue
        seen_ids.add(candidate.id)
        scenarios.append(candidate)

    if not scenarios:
        joined = "\n  - ".join(rejections) or "(no diagnostic)"
        raise ValueError(
            f"All {len(scenario_dicts)} LLM scenarios failed validation:\n  - {joined}"
        )

    import warnings

    if len(scenarios) > count:
        warnings.warn(
            f"LLM returned {len(scenarios)} valid scenarios; capping to requested count={count}.",
            stacklevel=2,
        )
        scenarios = scenarios[:count]
    elif len(scenarios) < count:
        msg = f"Requested {count} scenarios but only {len(scenarios)} returned"
        if rejections:
            joined = "\n  - ".join(rejections)
            msg += f"; {len(rejections)} rejected:\n  - {joined}"
        else:
            msg += " (LLM returned fewer than requested; no rejections)"
        warnings.warn(msg, stacklevel=2)
    return scenarios


def write_scenarios(
    scenarios: list[Scenario],
    *,
    force: bool = False,
    scenario_dir: Path | None = None,
) -> tuple[list[Path], list[Path]]:
    """Write scenarios as YAML files. Returns (written, skipped)."""
    target_dir = scenario_dir or SCENARIO_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    skipped: list[Path] = []
    target_resolved = target_dir.resolve()
    for scenario in scenarios:
        _validate_scenario_id(scenario.id)
        path = (target_dir / f"{scenario.id}.yaml").resolve()
        if not path.is_relative_to(target_resolved):
            raise InvalidScenarioIdError(
                f"Scenario id {scenario.id!r} resolves outside {target_resolved}"
            )
        if path.exists() and not force:
            skipped.append(path)
            continue
        path.write_text(_scenario_to_yaml(scenario))
        written.append(path)
    return written, skipped


def _scenario_to_yaml(scenario: Scenario) -> str:
    data = scenario.model_dump(mode="json", exclude_none=True, exclude_defaults=False)
    for optional_key in ("dataset", "input_field", "judge", "failure_pattern"):
        if data.get(optional_key) in (None, "", [], {}):
            data.pop(optional_key, None)
    if not data.get("trace_refs"):
        data.pop("trace_refs", None)
    if not data.get("env_overrides"):
        data.pop("env_overrides", None)
    return yaml.safe_dump(data, sort_keys=False, width=100, allow_unicode=True)
