"""Utility functions."""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from kensa.judge import JudgeProvider

from kensa.models import Result, RunManifest, Span, SpanKind
from kensa.trace_semantics import collect_tool_calls

_SAFE_RUN_ID = re.compile(r"^[\w.-]+$")


@functools.lru_cache(maxsize=1)
def detect_package_manager() -> str:
    """Detect the package manager used in the current project.

    Walks up from cwd looking for lock files and config. Returns
    ``"uv"``, ``"pipenv"``, or ``"pip"`` (default).
    """
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        if (parent / "uv.lock").is_file():
            return "uv"
        pyproject = parent / "pyproject.toml"
        if pyproject.is_file():
            try:
                text = pyproject.read_text()
                if "[tool.uv]" in text:
                    return "uv"
            except OSError:
                pass
        if (parent / "Pipfile").is_file() or (parent / "Pipfile.lock").is_file():
            return "pipenv"
        if (parent / "requirements.txt").is_file():
            return "pip"
    return "pip"


def install_hint(extra: str) -> str:
    """Return an install command for a kensa extra, matching the detected package manager."""
    pm = detect_package_manager()
    if pm == "uv":
        return f'uv add "kensa[{extra}]"'
    if pm == "pipenv":
        return f'pipenv install "kensa[{extra}]"'
    return f'pip install "kensa[{extra}]"'


def _extract_text_from_content_blocks(content: list[Any]) -> str:
    """Extract text from a list of content blocks (Anthropic-style)."""
    texts: list[str] = []
    for raw_block in content:
        if isinstance(raw_block, dict):
            block = cast(dict[str, Any], raw_block)
            if block.get("type") == "text":
                texts.append(str(block.get("text", "")))
    return "\n".join(texts)


def _extract_from_value(raw: Any) -> str:
    """Extract text from an output.value field (JSON-serialized API response)."""
    if not isinstance(raw, str):
        return str(raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(parsed, dict):
        return raw
    content = parsed.get("content")
    if isinstance(content, list):
        result = _extract_text_from_content_blocks(content)
        if result:
            return result
    choices = parsed.get("choices")
    if isinstance(choices, list):
        texts: list[str] = []
        for choice in choices:
            if isinstance(choice, dict):
                msg = choice.get("message", {})
                if isinstance(msg, dict):
                    c = msg.get("content", "")
                    if isinstance(c, str) and c:
                        texts.append(c)
        if texts:
            return "\n".join(texts)
    return raw


def extract_output_text(span: Span) -> str:
    """Extract human-readable text from a span's output."""
    if span.output is None:
        return ""
    if "messages" in span.output:
        texts: list[str] = []
        for raw_msg in cast(list[Any], span.output["messages"]):
            if isinstance(raw_msg, str):
                texts.append(raw_msg)
            elif isinstance(raw_msg, dict):
                msg_dict = cast(dict[str, Any], raw_msg)
                content = msg_dict.get("content", "")
                if isinstance(content, str):
                    texts.append(content)
                elif isinstance(content, list):
                    extracted = _extract_text_from_content_blocks(cast(list[Any], content))
                    if extracted:
                        texts.append(extracted)
        return "\n".join(texts)
    if "value" in span.output:
        return _extract_from_value(span.output["value"])
    return json.dumps(span.output)


def _collect_tool_names(spans: list[Span], *, ordered: bool = False) -> list[str]:
    """Extract deduplicated tool names from spans."""
    return [tool.name for tool in collect_tool_calls(spans, ordered=ordered)]


def count_tool_calls(spans: list[Span]) -> int:
    """Count tool calls across all spans.

    Deduplicates when both TOOL spans and LLM-embedded tool references
    exist for the same call (e.g. LangChain + OpenAI instrumentors active).
    """
    return len(collect_tool_calls(spans))


def get_tool_names(spans: list[Span]) -> list[str]:
    """Extract all tool names from spans.

    Deduplicates when both TOOL spans and LLM-embedded tool references
    exist for the same call.
    """
    return _collect_tool_names(spans)


def get_tool_names_ordered(spans: list[Span]) -> list[str]:
    """Extract all tool names from spans, sorted by span start_time.

    Like ``get_tool_names`` but preserves temporal order — needed for
    ``tool_order`` checks where the sequence matters.
    """
    return _collect_tool_names(spans, ordered=True)


def get_agent_output(spans: list[Span]) -> str:
    """Get the final agent output from a list of spans.

    Returns the output text from the last LLM span by end_time.
    """
    llm_spans = [s for s in spans if s.kind == SpanKind.LLM]
    if not llm_spans:
        return ""
    last_span = max(llm_spans, key=lambda s: s.end_time)
    return extract_output_text(last_span)


# --- CLI helpers (shared between cli.py commands) ---


def validate_run_id(run_id: str) -> str:
    """Reject run_id values that could escape the .kensa/ directory."""
    if not _SAFE_RUN_ID.match(run_id):
        raise ValueError(f"Invalid run ID: {run_id!r}")
    return run_id


def latest_manifest() -> RunManifest:
    """Find the most recent run manifest."""
    from kensa.paths import latest_manifest as latest_manifest_path

    with open(latest_manifest_path()) as f:
        return RunManifest.model_validate_json(f.read())


def save_results(run_id: str, results: list[Result]) -> None:
    """Persist judge results to .kensa/results/<run_id>.json."""
    results_dir = Path(".kensa/results")
    results_dir.mkdir(parents=True, exist_ok=True)
    results_path = results_dir / f"{run_id}.json"
    with open(results_path, "w") as f:
        json.dump([r.model_dump(mode="json") for r in results], f, indent=2)


def run_judge_manifest(
    manifest: RunManifest,
    judge_provider: JudgeProvider,
    scenario_dir: Path,
) -> tuple[list[Result], list[str]]:
    """Judge all scenarios in a manifest.

    Returns (results, skipped) — caller handles display of skipped messages.
    """
    from kensa.judge import judge_manifest

    return judge_manifest(manifest, judge_provider, scenario_dir)
