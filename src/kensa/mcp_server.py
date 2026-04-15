"""MCP server exposing kensa to any MCP-speaking client.

Seven tools (``init``, ``doctor``, ``run``, ``judge``, ``eval``, ``report``,
``analyze``) and eight resources under the ``kensa://`` URI namespace.

The module requires the ``mcp`` extra (``uv add kensa[mcp]``). Import raises a
helpful ``ImportError`` when ``fastmcp`` is missing so the CLI can surface a
clean install hint.

Design notes:
  * Tools are thin adapters over ``runner``, ``judge``, ``report``, ``analyzer``,
    and ``scaffold``. The MCP layer never duplicates business logic.
  * Failures flow back as ``MCPError`` with a stable ``code`` field; no raises
    cross the MCP boundary from a tool.
  * Long-running tools (``run``, ``judge``, ``eval``) return a compact summary
    plus a resource URI pointing at the full detail (see ``kensa://runs/...``).
  * Progress emission on the slow tools uses ``ctx.report_progress`` by
    bridging kensa's sync callbacks with the event loop via
    ``asyncio.run_coroutine_threadsafe``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

try:
    from fastmcp import Context, FastMCP
    from fastmcp.exceptions import ResourceError, ToolError
except ImportError as exc:  # pragma: no cover - surfaced via CLI hint
    raise ImportError(
        "The kensa MCP server requires the 'mcp' extra.\n"
        "Install with: uv add kensa[mcp]  (or pip install 'kensa[mcp]')"
    ) from exc

from kensa import __version__
from kensa.models import (
    Analysis,
    JudgePromptSpec,
    Result,
    ResultStatus,
    RunManifest,
    Scenario,
    Span,
)
from kensa.paths import (
    JUDGE_DIR,
    REPORT_DIR,
    RESULT_DIR,
    RUN_DIR,
    SCENARIO_DIR,
    TRACE_DIR,
    latest_manifest,
    manifest_path,
    report_path,
    results_path,
)

# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------

ErrorCode = Literal[
    "scenarios_missing",
    "run_not_found",
    "no_judge_key",
    "invalid_run_id",
    "path_escape",
    "unknown_format",
    "subprocess_failed",
    "internal",
]


class MCPError(BaseModel):
    """Stable error envelope returned from every tool that can fail."""

    error: str
    code: ErrorCode
    hint: str | None = None


# ---------------------------------------------------------------------------
# Tool response models
# ---------------------------------------------------------------------------


class InitResponse(BaseModel):
    directories_created: list[str]
    files_written: list[str]
    provider: str | None
    example_already_existed: bool


class DoctorCheck(BaseModel):
    name: str
    ok: bool
    detail: str


class DoctorResponse(BaseModel):
    ready: bool
    passed: int
    total: int
    checks: list[DoctorCheck]
    failures: list[DoctorCheck]


class RunSummary(BaseModel):
    run_id: str
    total: int
    completed: int
    failed: int
    duration_seconds: float
    manifest_uri: str


class JudgeSummary(BaseModel):
    run_id: str
    total: int
    passed: int
    failed: int
    errored: int
    uncertain: int
    results_uri: str
    failed_scenarios: list[str]
    skipped: list[str] = Field(default_factory=list)


class EvalSummary(BaseModel):
    run_id: str
    total: int
    passed: int
    failed: int
    errored: int
    uncertain: int
    duration_seconds: float
    cost_usd: float
    html_report: str
    results_uri: str
    failed_scenarios: list[str]
    skipped: list[str] = Field(default_factory=list)


class ReportResponse(BaseModel):
    run_id: str
    format: Literal["terminal", "markdown", "json", "html"]
    content: str
    html_report: str
    total: int
    passed: int


# ---------------------------------------------------------------------------
# Resource response models
# ---------------------------------------------------------------------------


class ScenarioListItem(BaseModel):
    id: str
    name: str
    description: str
    needs_judge: bool
    check_count: int
    has_dataset: bool


class RunListItem(BaseModel):
    run_id: str
    timestamp: str
    scenario_count: int
    total_runs: int
    completed: int
    has_results: bool


class RunDetail(BaseModel):
    run_id: str
    timestamp: str
    manifest: RunManifest
    summary: JudgeSummary | None = None


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

_INSTRUCTIONS = """\
kensa is an open-source agent evals harness. Tools run the workflow (init,
doctor, run, judge, eval, report, analyze); resources expose run artefacts
under kensa://runs, kensa://scenarios, kensa://judges. Launch from the project
root containing the .kensa/ directory.
"""

mcp: FastMCP = FastMCP(name="kensa", instructions=_INSTRUCTIONS, version=__version__)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _validate_run_id(run_id: str) -> bool:
    """Reject run_id values that could escape the .kensa/ directory."""
    import re

    return bool(re.match(r"^[\w.-]+$", run_id))


def _load_manifest_or_latest(run_id: str | None) -> RunManifest:
    path = latest_manifest() if run_id is None else manifest_path(run_id)
    return RunManifest.model_validate_json(path.read_text())


def _load_results(run_id: str) -> list[Result]:
    data = json.loads(results_path(run_id).read_text())
    return [Result.model_validate(r) for r in data]


class _StatusCounts(BaseModel):
    total: int
    passed: int
    failed: int
    errored: int
    uncertain: int


def _summarise_results(results: list[Result]) -> _StatusCounts:
    counts = {s.value: 0 for s in ResultStatus}
    for r in results:
        counts[r.status.value] += 1
    return _StatusCounts(
        total=len(results),
        passed=counts[ResultStatus.PASS.value],
        failed=counts[ResultStatus.FAIL.value],
        errored=counts[ResultStatus.ERROR.value],
        uncertain=counts[ResultStatus.UNCERTAIN.value],
    )


def _failed_scenarios(results: list[Result]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for r in results:
        if r.status in (ResultStatus.FAIL, ResultStatus.ERROR) and r.scenario_id not in seen:
            seen.add(r.scenario_id)
            out.append(r.scenario_id)
    return out


def _save_results(run_id: str, results: list[Result]) -> Path:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULT_DIR / f"{run_id}.json"
    path.write_text(json.dumps([r.model_dump(mode="json") for r in results], indent=2))
    return path


def _save_html_report(run_id: str, results: list[Result]) -> Path:
    from kensa.report import format_html

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = report_path(run_id)
    html_path.write_text(format_html(results))
    return html_path


def _run_duration(manifest: RunManifest) -> float:
    return sum(sr.duration_seconds for runs in manifest.scenarios.values() for sr in runs)


def _run_cost(results: list[Result]) -> float:
    return sum(r.trace.cost_usd for r in results if r.trace)


def _progress_bridge(ctx: Context | None, loop: asyncio.AbstractEventLoop) -> tuple[Any, Any]:
    """Build sync callbacks that schedule ``ctx`` progress on ``loop``.

    Returns ``(on_run, on_judge)``. Each callback is a no-op when ``ctx`` is
    ``None``, which keeps unit tests that skip Context cheap.
    """
    if ctx is None:
        return None, None

    def on_run(current: int, total: int, scenario_id: str) -> None:
        asyncio.run_coroutine_threadsafe(
            ctx.report_progress(progress=current, total=total, message=scenario_id),
            loop,
        )

    def on_judge(completed: int, total: int) -> None:
        asyncio.run_coroutine_threadsafe(
            ctx.report_progress(progress=completed, total=total, message="judging"),
            loop,
        )

    return on_run, on_judge


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool
def init(blank: bool = False, force: bool = False) -> InitResponse | MCPError:
    """Scaffold ``.kensa/`` (idempotent).

    Creates ``scenarios/``, ``traces/``, ``judges/``, ``agents/``. Unless
    ``blank`` is true, writes an example agent and scenario chosen from
    available API keys. ``force`` overwrites an existing example.
    """
    from kensa.scaffold import init_kensa

    try:
        result = init_kensa(blank=blank, force=force)
    except OSError as e:
        return MCPError(error=str(e), code="internal")
    return InitResponse(**result.model_dump())


@mcp.tool
def doctor() -> DoctorResponse:
    """Run pre-flight diagnostics (Python, SDKs, API keys, scenarios, judge).

    Returns the complete checklist plus a ``ready`` flag. Never fails; a
    misconfigured environment is reported via individual check entries.
    """
    from kensa.doctor import run_doctor

    raw = run_doctor()
    checks = [DoctorCheck(name=n, ok=ok, detail=d) for n, ok, d in raw]
    failures = [c for c in checks if not c.ok]
    api_ok = any(c.ok for c in checks if "API_KEY" in c.name)
    return DoctorResponse(
        ready=not failures and api_ok,
        passed=sum(1 for c in checks if c.ok),
        total=len(checks),
        checks=checks,
        failures=failures,
    )


@mcp.tool
async def run(
    scenario_ids: list[str] | None = None,
    scenario_dir: str = str(SCENARIO_DIR),
    timeout: int = 300,
    ctx: Context | None = None,
) -> RunSummary | MCPError:
    """Execute scenarios in subprocesses and capture OpenTelemetry traces.

    Writes a RunManifest under ``.kensa/runs/<run_id>.json``. Returns a
    summary plus ``manifest_uri`` for full detail.
    """
    from kensa.runner import run_scenarios

    loop = asyncio.get_running_loop()
    on_run, _ = _progress_bridge(ctx, loop)
    if ctx:
        await ctx.info("Starting scenario execution")

    try:
        manifest = await asyncio.to_thread(
            run_scenarios,
            scenario_dir=scenario_dir,
            scenario_ids=scenario_ids,
            timeout=timeout,
            on_progress=on_run,
        )
    except FileNotFoundError as e:
        return MCPError(
            error=str(e), code="scenarios_missing", hint=f"Create scenarios in: {scenario_dir}/"
        )
    except ValueError as e:
        return MCPError(error=str(e), code="invalid_run_id")
    except Exception as e:
        return MCPError(error=str(e), code="internal")

    completed = sum(1 for runs in manifest.scenarios.values() for sr in runs if sr.exit_code == 0)
    total = sum(len(runs) for runs in manifest.scenarios.values())
    return RunSummary(
        run_id=manifest.run_id,
        total=total,
        completed=completed,
        failed=total - completed,
        duration_seconds=round(_run_duration(manifest), 2),
        manifest_uri=f"kensa://runs/{manifest.run_id}",
    )


@mcp.tool
async def judge(
    run_id: str | None = None,
    model: str | None = None,
    scenario_dir: str = str(SCENARIO_DIR),
    ctx: Context | None = None,
) -> JudgeSummary | MCPError:
    """Score a run with deterministic checks plus the LLM judge.

    Defaults to the latest run. Writes results to
    ``.kensa/results/<run_id>.json``. Returns a summary plus ``results_uri``.
    """
    from kensa.judge import get_judge, judge_manifest, manifest_requires_judge

    if run_id is not None and not _validate_run_id(run_id):
        return MCPError(error=f"Invalid run ID: {run_id!r}", code="invalid_run_id")

    try:
        manifest = _load_manifest_or_latest(run_id)
    except FileNotFoundError as e:
        return MCPError(error=str(e), code="run_not_found", hint="Call run() first.")

    try:
        provider = get_judge(model) if manifest_requires_judge(manifest, scenario_dir) else None
    except RuntimeError as e:
        return MCPError(
            error=str(e),
            code="no_judge_key",
            hint="Set ANTHROPIC_API_KEY or OPENAI_API_KEY.",
        )
    except ImportError as e:
        return MCPError(
            error=str(e),
            code="internal",
            hint="Install provider extras: uv add 'kensa[anthropic]' or 'kensa[openai]'.",
        )

    loop = asyncio.get_running_loop()
    _, on_judge = _progress_bridge(ctx, loop)
    if ctx:
        await ctx.info(f"Judging run {manifest.run_id}")

    results, skipped = await asyncio.to_thread(
        judge_manifest,
        manifest,
        provider,
        Path(scenario_dir),
        on_judge,
    )
    _save_results(manifest.run_id, results)

    counts = _summarise_results(results)
    return JudgeSummary(
        run_id=manifest.run_id,
        total=counts.total,
        passed=counts.passed,
        failed=counts.failed,
        errored=counts.errored,
        uncertain=counts.uncertain,
        results_uri=f"kensa://runs/{manifest.run_id}/results",
        failed_scenarios=_failed_scenarios(results),
        skipped=skipped,
    )


@mcp.tool
async def eval(
    scenario_ids: list[str] | None = None,
    scenario_dir: str = str(SCENARIO_DIR),
    timeout: int = 300,
    model: str | None = None,
    ctx: Context | None = None,
) -> EvalSummary | MCPError:
    """Run scenarios and judge them in one call (the 90% workflow).

    Resolves the judge up-front so expensive runs don't happen without a
    working judge. Writes manifest, results, and an HTML report.
    """
    from kensa.judge import get_judge, judge_manifest
    from kensa.runner import load_scenarios, run_scenarios

    try:
        scenarios = load_scenarios(scenario_dir=scenario_dir, scenario_ids=scenario_ids)
    except FileNotFoundError as e:
        return MCPError(
            error=str(e), code="scenarios_missing", hint=f"Create scenarios in: {scenario_dir}/"
        )
    except ValueError as e:
        return MCPError(error=str(e), code="invalid_run_id")

    needs_judge = any(sc.criteria or sc.judge for sc in scenarios)
    try:
        provider = get_judge(model) if needs_judge else None
    except RuntimeError as e:
        return MCPError(
            error=str(e),
            code="no_judge_key",
            hint="Set ANTHROPIC_API_KEY or OPENAI_API_KEY.",
        )
    except ImportError as e:
        return MCPError(
            error=str(e),
            code="internal",
            hint="Install provider extras: uv add 'kensa[anthropic]' or 'kensa[openai]'.",
        )

    loop = asyncio.get_running_loop()
    on_run, on_judge = _progress_bridge(ctx, loop)
    if ctx:
        await ctx.info(f"Running {len(scenarios)} scenario(s)")

    manifest = await asyncio.to_thread(
        run_scenarios,
        scenario_dir=scenario_dir,
        timeout=timeout,
        scenarios=scenarios,
        on_progress=on_run,
    )

    if ctx:
        await ctx.info(f"Judging run {manifest.run_id}")

    results, skipped = await asyncio.to_thread(
        judge_manifest,
        manifest,
        provider,
        Path(scenario_dir),
        on_judge,
    )
    _save_results(manifest.run_id, results)
    html_path = _save_html_report(manifest.run_id, results)

    counts = _summarise_results(results)
    return EvalSummary(
        run_id=manifest.run_id,
        total=counts.total,
        passed=counts.passed,
        failed=counts.failed,
        errored=counts.errored,
        uncertain=counts.uncertain,
        duration_seconds=round(_run_duration(manifest), 2),
        cost_usd=round(_run_cost(results), 4),
        html_report=str(html_path),
        results_uri=f"kensa://runs/{manifest.run_id}/results",
        failed_scenarios=_failed_scenarios(results),
        skipped=skipped,
    )


@mcp.tool
def report(
    run_id: str | None = None,
    format: Literal["terminal", "markdown", "json", "html"] = "markdown",
) -> ReportResponse | MCPError:
    """Render a run's results (latest by default) in the requested format.

    Also writes the HTML artefact under ``.kensa/reports/<run_id>.html`` as a
    side effect, matching the CLI's behaviour.
    """
    from kensa.report import FORMATTERS

    if run_id is not None and not _validate_run_id(run_id):
        return MCPError(error=f"Invalid run ID: {run_id!r}", code="invalid_run_id")

    try:
        if run_id is None:
            manifest = _load_manifest_or_latest(None)
            run_id = manifest.run_id
        results = _load_results(run_id)
    except FileNotFoundError as e:
        return MCPError(error=str(e), code="run_not_found", hint="Call judge() or eval() first.")

    content = FORMATTERS[format](results)
    html_path = _save_html_report(run_id, results)
    counts = _summarise_results(results)
    return ReportResponse(
        run_id=run_id,
        format=format,
        content=content,
        html_report=str(html_path),
        total=counts.total,
        passed=counts.passed,
    )


@mcp.tool
def analyze(trace_dir: str = str(TRACE_DIR)) -> Analysis:
    """Compute cost / latency / anomaly statistics across all traces.

    Reads every JSONL under ``trace_dir`` and surfaces per-tool usage,
    outlier traces, and error rates.
    """
    from kensa.analyzer import analyze_traces

    return analyze_traces(trace_dir=trace_dir)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("kensa://runs")
def runs_list() -> list[RunListItem]:
    """List the most recent runs (newest first, up to 50)."""
    if not RUN_DIR.exists():
        return []
    paths = sorted(RUN_DIR.glob("*.json"), reverse=True)[:50]
    out: list[RunListItem] = []
    for p in paths:
        try:
            manifest = RunManifest.model_validate_json(p.read_text())
        except (OSError, ValueError):
            continue
        total_runs = sum(len(r) for r in manifest.scenarios.values())
        completed = sum(1 for r in manifest.scenarios.values() for sr in r if sr.exit_code == 0)
        out.append(
            RunListItem(
                run_id=manifest.run_id,
                timestamp=manifest.timestamp.isoformat(),
                scenario_count=len(manifest.scenarios),
                total_runs=total_runs,
                completed=completed,
                has_results=results_path(manifest.run_id).exists(),
            )
        )
    return out


@mcp.resource("kensa://runs/{run_id}")
def run_detail(run_id: str) -> RunDetail:
    """Manifest plus summary for one run."""
    if not _validate_run_id(run_id):
        raise ResourceError(f"Invalid run ID: {run_id!r}")
    try:
        manifest = RunManifest.model_validate_json(manifest_path(run_id).read_text())
    except FileNotFoundError as e:
        raise ResourceError(f"Run not found: {run_id}") from e

    summary: JudgeSummary | None = None
    try:
        results = _load_results(run_id)
    except FileNotFoundError:
        results = []
    if results:
        counts = _summarise_results(results)
        summary = JudgeSummary(
            run_id=run_id,
            total=counts.total,
            passed=counts.passed,
            failed=counts.failed,
            errored=counts.errored,
            uncertain=counts.uncertain,
            results_uri=f"kensa://runs/{run_id}/results",
            failed_scenarios=_failed_scenarios(results),
        )

    return RunDetail(
        run_id=run_id,
        timestamp=manifest.timestamp.isoformat(),
        manifest=manifest,
        summary=summary,
    )


@mcp.resource("kensa://runs/{run_id}/results")
def run_results(run_id: str) -> list[Result]:
    """Full judged results for one run."""
    if not _validate_run_id(run_id):
        raise ResourceError(f"Invalid run ID: {run_id!r}")
    try:
        return _load_results(run_id)
    except FileNotFoundError as e:
        raise ResourceError(f"No results for run {run_id}. Call judge() first.") from e


@mcp.resource("kensa://runs/{run_id}/trace/{scenario}")
def run_trace(run_id: str, scenario: str) -> list[Span]:
    """Spans for one scenario execution inside a run."""
    from kensa.runner import read_trace

    if not _validate_run_id(run_id) or not _validate_run_id(scenario):
        raise ResourceError("Invalid run_id or scenario id")
    try:
        manifest = RunManifest.model_validate_json(manifest_path(run_id).read_text())
    except FileNotFoundError as e:
        raise ResourceError(f"Run not found: {run_id}") from e

    runs = manifest.scenarios.get(scenario)
    if not runs:
        raise ResourceError(f"Scenario {scenario!r} not in run {run_id}")

    # Use the first run's trace (dataset scenarios may have many; v1 returns the first).
    sr = runs[0]
    if not sr.trace_path:
        raise ResourceError(f"No trace captured for {scenario} in run {run_id}")

    # Path escape guard: the trace_path must live under TRACE_DIR.
    resolved = Path(sr.trace_path).resolve()
    trace_root = TRACE_DIR.resolve()
    if not resolved.is_relative_to(trace_root):
        raise ResourceError(f"Trace path escapes {TRACE_DIR}/")
    try:
        return read_trace(str(resolved))
    except FileNotFoundError as e:
        raise ResourceError(str(e)) from e


@mcp.resource("kensa://scenarios")
def scenarios_list() -> list[ScenarioListItem]:
    """List every scenario defined in ``.kensa/scenarios/``."""
    from kensa.runner import load_scenarios

    try:
        scenarios = load_scenarios()
    except FileNotFoundError:
        return []
    return [
        ScenarioListItem(
            id=s.id,
            name=s.name,
            description=s.description,
            needs_judge=bool(s.criteria or s.judge),
            check_count=len(s.checks),
            has_dataset=bool(s.dataset),
        )
        for s in scenarios
    ]


@mcp.resource("kensa://scenarios/{scenario_id}")
def scenario_detail(scenario_id: str) -> Scenario:
    """Full scenario definition."""
    from kensa.runner import load_scenarios

    try:
        scenarios = load_scenarios(scenario_ids=[scenario_id])
    except FileNotFoundError as e:
        raise ResourceError(str(e)) from e
    except ValueError as e:
        raise ResourceError(str(e)) from e
    if not scenarios:
        raise ResourceError(f"Scenario not found: {scenario_id}")
    return scenarios[0]


@mcp.resource("kensa://judges")
def judges_list() -> list[str]:
    """List structured judge prompt names."""
    if not JUDGE_DIR.exists():
        return []
    return sorted(p.stem for p in JUDGE_DIR.glob("*.yaml"))


@mcp.resource("kensa://judges/{name}")
def judge_detail(name: str) -> JudgePromptSpec:
    """Load a structured judge prompt spec."""
    from kensa.judge import load_judge_prompt_spec

    if not _validate_run_id(name):
        raise ResourceError(f"Invalid judge name: {name!r}")
    try:
        return load_judge_prompt_spec(name)
    except FileNotFoundError as e:
        raise ResourceError(f"Judge not found: {name}") from e


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def run_server(
    transport: Literal["stdio", "http"] = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Launch the MCP server.

    HTTP transport binds to ``127.0.0.1`` by default. Exposing it on a public
    interface exposes subprocess-executing tools (``run``, ``eval``) with no
    auth; do not do this on a shared host without a bearer token in front.
    """
    if transport == "stdio":
        mcp.run()
    elif transport == "http":
        mcp.run(transport="http", host=host, port=port)
    else:
        raise ToolError(f"Unknown transport: {transport!r}")


def main() -> None:
    """Entry point for the ``kensa-mcp`` console script."""
    parser = argparse.ArgumentParser(description="Run the kensa MCP server.")
    parser.add_argument("--http", action="store_true", help="Use HTTP transport instead of stdio.")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (with --http).")
    parser.add_argument("--port", default=8765, type=int, help="HTTP port (with --http).")
    args = parser.parse_args()
    run_server(
        transport="http" if args.http else "stdio",
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
