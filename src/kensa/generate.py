"""Generate eval scenarios from existing traces via LLM.

``kensa generate`` reads traces from a prior run, summarizes each trace into
a compact representation, and asks an LLM to propose scenario YAMLs covering
happy path, tool usage, edge cases, error handling, and cost/latency bounds.

The port of ``skills/generate-scenarios`` into a first-class CLI so non-Claude-
Code users can reproduce the ``kensa init && kensa generate && kensa run`` demo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import yaml

from kensa.models import RunManifest, Scenario, Span
from kensa.paths import SCENARIO_DIR, latest_manifest, manifest_path

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

    manifest_file = manifest_path(run_id) if run_id else latest_manifest()
    manifest = RunManifest.model_validate_json(manifest_file.read_text())

    paths: list[Path] = [
        Path(sr.trace_path) for runs in manifest.scenarios.values() for sr in runs if sr.trace_path
    ]
    if not paths:
        raise FileNotFoundError(
            f"Manifest {manifest.run_id} has no trace files. Run `kensa run` first."
        )
    return paths


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
        manifest_file = manifest_path(run_id) if run_id else latest_manifest()
        manifest = RunManifest.model_validate_json(manifest_file.read_text())
    except (FileNotFoundError, ValueError):
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
    ids: set[str] = _manifest_scenario_ids(run_id)
    if not ids and trace_paths:
        manifest = _find_manifest_for_traces(trace_paths)
        if manifest:
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
) -> list[Scenario]:
    """Read traces, ask an LLM for scenarios, return validated Scenario objects."""
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

    scenarios: list[Scenario] = []
    rejections: list[str] = []
    for i, sd in enumerate(scenario_dicts):
        if not isinstance(sd, dict):
            rejections.append(f"#{i}: not a JSON object")
            continue
        sd.setdefault("source", "traces")
        try:
            candidate = Scenario.model_validate(sd)
            _validate_scenario_id(candidate.id)
            scenarios.append(candidate)
        except Exception as err:
            rejections.append(f"#{i} ({sd.get('id', '?')}): {err}")

    if not scenarios:
        joined = "\n  - ".join(rejections) or "(no diagnostic)"
        raise ValueError(
            f"All {len(scenario_dicts)} LLM scenarios failed validation:\n  - {joined}"
        )
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
