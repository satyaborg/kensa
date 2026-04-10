"""LLM-as-judge evaluation + check orchestration.

Judge flow: run all checks → if any fail, skip LLM judge → if all pass,
run LLM judge → combine into Result.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any, Protocol

import yaml

from kensa.checks import CHECK_REGISTRY
from kensa.models import (
    CheckResult,
    CheckType,
    JudgePromptSpec,
    JudgeResult,
    Result,
    ResultStatus,
    RunManifest,
    Scenario,
    ScenarioRun,
    Span,
    SpanKind,
    TraceSummary,
    _is_bare_placeholder,
    validate_runtime_list_params,
)
from kensa.paths import judge_prompt_path
from kensa.utils import count_tool_calls, extract_output_text, get_tool_names


class JudgeProvider(Protocol):
    """Protocol for LLM judge implementations."""

    def judge(self, prompt: str) -> JudgeResult: ...


@functools.lru_cache(maxsize=32)
def load_judge_prompt_spec(name: str) -> JudgePromptSpec:
    """Load a structured judge prompt from .kensa/judges/{name}.yaml."""
    path = judge_prompt_path(name)
    with open(path) as f:
        data = yaml.safe_load(f)
    return JudgePromptSpec(**data)


def _format_structured_criteria(spec: JudgePromptSpec) -> str:
    """Format a JudgePromptSpec into the evaluation criteria section."""
    parts = [
        f"Criterion: {spec.criterion}",
        f"\nPASS: {spec.pass_definition}",
        f"\nFAIL: {spec.fail_definition}",
    ]
    if spec.examples:
        parts.append("\nExamples:")
        for i, ex in enumerate(spec.examples, 1):
            parts.append(f"\n  Example {i} [{ex.label.upper()}]:")
            parts.append(f"  Output: {ex.output}")
            parts.append(f"  Critique: {ex.critique}")
    return "\n".join(parts)


def build_judge_prompt(
    scenario: Scenario,
    spans: list[Span],
    stdout: str = "",
    scenario_input: str | dict[str, Any] | None = None,
    expected_output: str | None = None,
) -> str:
    """Construct the judge evaluation prompt.

    Includes both the trace summary and subprocess stdout so the judge
    evaluates the same data the deterministic checks see.
    """
    llm_spans = [s for s in spans if s.kind == SpanKind.LLM]
    tool_names = get_tool_names(spans)
    tool_call_count = count_tool_calls(spans)

    trace_summary: list[str] = []
    for i, s in enumerate(spans):
        line = f"[{i}] {s.kind.value}: {s.name}"
        if s.model:
            line += f" (model={s.model})"
        if s.tools:
            line += f" [tools: {', '.join(t.name for t in s.tools)}]"
        if s.status == "error":
            line += " [ERROR]"
        output_text = extract_output_text(s)
        if output_text:
            if len(output_text) > 500:
                output_text = output_text[:500] + "..."
            line += f"\n    Output: {output_text}"
        trace_summary.append(line)

    tools_line = f", {tool_call_count} tool calls"
    if tool_names:
        unique = sorted(set(tool_names))
        tools_line += f": {', '.join(unique)}"

    input_section = ""
    if scenario_input is not None:
        if isinstance(scenario_input, dict):
            input_str = json.dumps(scenario_input)
        else:
            input_str = str(scenario_input)
        if len(input_str) > 2000:
            input_str = input_str[:2000] + "\n... (truncated)"
        input_section = f"\n## Scenario Input\n{input_str}\n"

    expected_section = ""
    if expected_output is not None:
        expected_section = f"\n## Expected Output\n{expected_output}\n"

    stdout_section = ""
    stripped_stdout = stdout.strip()
    if stripped_stdout:
        truncated = stripped_stdout[:2000]
        if len(stripped_stdout) > 2000:
            truncated += "\n... (truncated)"
        stdout_section = f"""

## Agent Output (stdout)
{truncated}
"""

    if scenario.judge:
        spec = load_judge_prompt_spec(scenario.judge)
        criteria_text = _format_structured_criteria(spec)
    elif scenario.criteria:
        criteria_text = scenario.criteria
    else:
        criteria_text = "No specific criteria provided."

    return f"""You are evaluating an AI agent's execution against specific criteria.

## Scenario
Name: {scenario.name}
Description: {scenario.description}
Expected Outcome: {scenario.expected_outcome}
{input_section}{expected_section}
## Evaluation Criteria
{criteria_text}

## Execution Trace ({len(llm_spans)} LLM calls{tools_line})
{chr(10).join(trace_summary)}
{stdout_section}
## Task
Evaluate whether the agent's execution meets the criteria above.
Focus on whether the agent accomplished its goal correctly, not on implementation details.

Respond with ONLY a JSON object (no markdown, no backticks):
{{"verdict": "pass" or "fail" or "uncertain",
  "reasoning": "your detailed reasoning",
  "evidence": ["key observation 1", "key observation 2"]}}

Use "uncertain" when evidence is ambiguous or insufficient to determine pass/fail."""


class AnthropicJudge:
    """Judge using Anthropic's Claude API."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        try:
            import anthropic
        except ImportError as err:
            from kensa.utils import install_hint

            raise ImportError(
                "anthropic package required for Anthropic judge. "
                f"Install with: {install_hint('anthropic')}"
            ) from err
        self.client = anthropic.Anthropic()
        self.model = model

    def judge(self, prompt: str) -> JudgeResult:

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text = block.text
                break
        return _parse_judge_response(text)


class OpenAIJudge:
    """Judge using OpenAI's API."""

    def __init__(self, model: str = "gpt-5.4-mini") -> None:
        try:
            import openai
        except ImportError as err:
            from kensa.utils import install_hint

            raise ImportError(
                f"openai package required for OpenAI judge. Install with: {install_hint('openai')}"
            ) from err
        self.client = openai.OpenAI()
        self.model = model

    def judge(self, prompt: str) -> JudgeResult:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        text = response.choices[0].message.content or ""
        return _parse_judge_response(text)


def _parse_verdict(verdict_str: str) -> ResultStatus | None:
    """Parse a verdict string to ResultStatus, returning None for unknown values."""
    try:
        return ResultStatus(verdict_str.lower())
    except ValueError:
        return None


def _parse_judge_response(text: str) -> JudgeResult:
    """Parse the LLM judge's JSON response.

    Supports both new format (verdict/evidence) and legacy format (passed bool).
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        text = text.strip()

    try:
        data = json.loads(text)
        reasoning = str(data.get("reasoning", ""))
        evidence = data.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = []
        evidence = [str(e) for e in evidence]

        verdict_str = data.get("verdict")
        if verdict_str and isinstance(verdict_str, str):
            verdict = _parse_verdict(verdict_str)
            passed = verdict == ResultStatus.PASS if verdict else False
            return JudgeResult(
                passed=passed,
                reasoning=reasoning,
                verdict=verdict,
                evidence=evidence,
            )

        # Legacy fallback: "passed" bool
        passed = bool(data.get("passed", False))
        return JudgeResult(
            passed=passed,
            reasoning=reasoning,
            verdict=ResultStatus.PASS if passed else ResultStatus.FAIL,
            evidence=evidence,
        )
    except (json.JSONDecodeError, KeyError):
        return JudgeResult(
            passed=False,
            reasoning=f"Failed to parse judge response: {text[:200]}",
            verdict=ResultStatus.FAIL,
        )


def get_judge(model: str | None = None) -> JudgeProvider:
    """Resolve which judge provider to use.

    Loads .env (walking up from cwd) so the judge process has the same
    env vars as the runner subprocess.

    Priority: KENSA_JUDGE_MODEL env var → ANTHROPIC_API_KEY → OPENAI_API_KEY → error.
    """
    from kensa.runner import ensure_dotenv_loaded

    ensure_dotenv_loaded()

    model_override = model or os.environ.get("KENSA_JUDGE_MODEL")

    if model_override:
        if "claude" in model_override or "anthropic" in model_override.lower():
            return AnthropicJudge(model=model_override)
        return OpenAIJudge(model=model_override)

    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicJudge()

    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIJudge()

    raise RuntimeError(
        "No judge model available. Set one of:\n"
        "  KENSA_JUDGE_MODEL=<model>  (explicit model)\n"
        "  ANTHROPIC_API_KEY=<key>     (uses claude-sonnet-4-6)\n"
        "  OPENAI_API_KEY=<key>        (uses gpt-5.4-mini)\n"
        "Keys can be in a .env file (searched up from cwd) or exported."
    )


def _build_trace_summary(spans: list[Span], trace_path: str) -> TraceSummary:
    """Build a TraceSummary from spans."""
    llm_calls = sum(1 for s in spans if s.kind == SpanKind.LLM)
    tool_calls = count_tool_calls(spans)
    total_tokens = sum(s.tokens.total for s in spans if s.tokens)
    cost_usd = sum(s.cost.total for s in spans if s.cost)
    duration = 0.0
    if spans:
        earliest = min(s.start_time for s in spans)
        latest = max(s.end_time for s in spans)
        duration = (latest - earliest).total_seconds()

    return TraceSummary(
        path=trace_path,
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
        duration_seconds=round(duration, 2),
    )


def _load_manifest_scenario(dir_path: Path, scenario_id: str) -> Scenario | None:
    """Load a scenario file by ID from a scenario directory."""
    from kensa.runner import load_scenario

    for ext in (".yaml", ".yml"):
        path = dir_path / f"{scenario_id}{ext}"
        if path.is_file():
            return load_scenario(path)
    return None


def manifest_requires_judge(
    manifest: RunManifest,
    scenario_dir: str | Path,
) -> bool:
    """Return True when any traced scenario in the manifest needs an LLM judge."""
    dir_path = Path(scenario_dir)
    for sid, runs in manifest.scenarios.items():
        if not any(sr.trace_path for sr in runs):
            continue
        scenario = _load_manifest_scenario(dir_path, sid)
        if scenario and (scenario.criteria or scenario.judge):
            return True
    return False


def _extract_expected(row: dict[str, Any] | None) -> str | None:
    """Extract the ``expected`` field from a dataset row."""
    return str(row["expected"]) if row and "expected" in row else None


def _error_result_for_run(
    scenario_id: str,
    run: ScenarioRun,
    *,
    message: str,
    spans: list[Span] | None = None,
) -> Result:
    """Build an ERROR result for a failed subprocess run."""
    trace_summary = None
    if spans is not None and run.trace_path:
        trace_summary = _build_trace_summary(spans, run.trace_path)

    return Result(
        scenario_id=scenario_id,
        status=ResultStatus.ERROR,
        input=run.input,
        expected=_extract_expected(run.dataset_row),
        check_results=[],
        judge_result=None,
        trace=trace_summary,
        error=message,
    )


def _substitute_value(v: Any, row: dict[str, Any]) -> Any:
    """Recursively replace ``{{field}}`` placeholders with dataset row values.

    A bare ``{{field}}`` returns the dataset value as-is (preserving numeric
    types for check thresholds). An embedded placeholder coerces to ``str``.
    """
    if isinstance(v, str) and "{{" in v:
        if _is_bare_placeholder(v):
            field = v[2:-2]
            if field in row:
                return row[field]
        for field, val in row.items():
            v = v.replace(f"{{{{{field}}}}}", str(val))
        return v
    if isinstance(v, dict):
        return {k: _substitute_value(val, row) for k, val in v.items()}
    if isinstance(v, list):
        return [_substitute_value(item, row) for item in v]
    return v


def _substitute_params(
    params: dict[str, Any],
    row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Replace ``{{field}}`` placeholders in check params with dataset row values."""
    if not row:
        return params
    return {k: _substitute_value(v, row) for k, v in params.items()}


def judge_scenario(
    scenario: Scenario,
    spans: list[Span],
    trace_path: str,
    judge_provider: JudgeProvider | None = None,
    stdout: str = "",
    scenario_input: str | dict[str, Any] | None = None,
    dataset_row: dict[str, Any] | None = None,
) -> Result:
    """Evaluate a scenario: run checks, then LLM judge if checks pass."""
    expected = _extract_expected(dataset_row)

    check_results: list[CheckResult] = []
    for check in scenario.checks:
        check_type = CheckType(check.type)
        check_fn = CHECK_REGISTRY[check_type]
        params = _substitute_params(check.params, dataset_row)
        try:
            validate_runtime_list_params(check_type, params)
        except ValueError as e:
            check_results.append(CheckResult(check=check_type.value, passed=False, detail=str(e)))
            continue
        result = check_fn(spans, params)
        check_results.append(result)

    trace_summary = _build_trace_summary(spans, trace_path)

    if any(not cr.passed for cr in check_results):
        return Result(
            scenario_id=scenario.id,
            status=ResultStatus.FAIL,
            input=scenario_input,
            expected=expected,
            check_results=check_results,
            judge_result=None,
            trace=trace_summary,
        )

    judge_result = None
    if scenario.criteria or scenario.judge:
        try:
            provider = judge_provider or get_judge()
            prompt = build_judge_prompt(
                scenario,
                spans,
                stdout=stdout,
                scenario_input=scenario_input,
                expected_output=expected,
            )
            judge_result = provider.judge(prompt)
        except Exception as e:
            return Result(
                scenario_id=scenario.id,
                status=ResultStatus.ERROR,
                input=scenario_input,
                expected=expected,
                check_results=check_results,
                judge_result=None,
                trace=trace_summary,
                error=f"Judge error: {e}",
            )

    if judge_result and judge_result.verdict:
        status = judge_result.verdict
    elif judge_result:
        status = ResultStatus.FAIL if not judge_result.passed else ResultStatus.PASS
    else:
        status = ResultStatus.PASS

    return Result(
        scenario_id=scenario.id,
        status=status,
        input=scenario_input,
        expected=expected,
        check_results=check_results,
        judge_result=judge_result,
        trace=trace_summary,
    )


def judge_manifest(
    manifest: RunManifest,
    judge_provider: JudgeProvider | None,
    scenario_dir: str | Path,
) -> tuple[list[Result], list[str]]:
    """Judge all scenarios in a manifest.

    Each scenario may have multiple runs (list[ScenarioRun]). Each run is
    judged independently in parallel. Returns (results, skipped) where
    skipped contains scenario IDs that were skipped with a reason string.
    """
    from concurrent.futures import Future, ThreadPoolExecutor

    from kensa.runner import read_trace

    dir_path = Path(scenario_dir)
    futures: list[Future[Result]] = []
    skipped: list[str] = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        for sid, runs in manifest.scenarios.items():
            scenario = _load_manifest_scenario(dir_path, sid)
            if not scenario:
                skipped.append(f"{sid}: scenario file not found")
                continue

            for sr in runs:
                if not sr.trace_path:
                    error = sr.stderr or "Scenario failed before traces were captured."
                    futures.append(executor.submit(_error_result_for_run, sid, sr, message=error))
                    continue

                try:
                    spans = read_trace(sr.trace_path)
                except Exception as e:
                    futures.append(
                        executor.submit(
                            _error_result_for_run,
                            sid,
                            sr,
                            message=f"Failed to read trace '{sr.trace_path}': {e}",
                        )
                    )
                    continue

                if sr.exit_code != 0:
                    error = f"Scenario subprocess exited with code {sr.exit_code}."
                    if sr.stderr:
                        error += f"\nstderr:\n{sr.stderr}"
                    futures.append(
                        executor.submit(_error_result_for_run, sid, sr, message=error, spans=spans)
                    )
                    continue

                futures.append(
                    executor.submit(
                        judge_scenario,
                        scenario,
                        spans,
                        sr.trace_path,
                        judge_provider,
                        stdout=sr.stdout,
                        scenario_input=sr.input,
                        dataset_row=sr.dataset_row,
                    )
                )

    results = [f.result() for f in futures]

    return results, skipped
