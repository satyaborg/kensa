"""Shared Rich styles and table builders for terminal output."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from rich.console import Console, ConsoleOptions, RenderResult
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from kensa.models import Analysis, Result, ResultStatus, RunManifest, ScenarioRun

COLOR_PASS = "green"
COLOR_FAIL = "red"
COLOR_ERROR = "yellow"
COLOR_UNCERTAIN = "blue"
COLOR_DIM = "dim"
HEADER_STYLE = "bold"

_STATUS_COLOR: dict[ResultStatus, str] = {
    ResultStatus.PASS: COLOR_PASS,
    ResultStatus.FAIL: COLOR_FAIL,
    ResultStatus.ERROR: COLOR_ERROR,
    ResultStatus.UNCERTAIN: COLOR_UNCERTAIN,
}


def status_badge(status: ResultStatus) -> Text:
    """Styled status badge for eval results."""
    color = _STATUS_COLOR[status]
    return Text(status.value.upper(), style=f"{color} bold")


def run_status_badge(sr: ScenarioRun) -> Text:
    """Styled status badge for subprocess run outcomes."""
    if sr.exit_code == 0:
        return Text("OK", style=f"{COLOR_PASS} bold")
    if sr.exit_code == -1 and not sr.trace_path:
        return Text("ERROR", style=f"{COLOR_FAIL} bold")
    return Text(f"EXIT {sr.exit_code}", style=f"{COLOR_FAIL} bold")


def checks_cell(r: Result) -> str:
    """Format the checks column: '3/5' or '-'."""
    if r.check_results:
        passed = sum(1 for c in r.check_results if c.passed)
        return f"{passed}/{len(r.check_results)}"
    return "-"


def judge_cell(r: Result) -> str:
    """Format the judge column: 'pass', 'fail', 'uncertain', or '-'."""
    if r.judge_result:
        if r.judge_result.verdict:
            return r.judge_result.verdict.value
        return "pass" if r.judge_result.passed else "fail"
    return "-"


def metrics_cell(r: Result) -> str:
    """Format top-level numeric metrics for compact tabular display."""
    parts: list[str] = []
    if "trajectory_accuracy" in r.metrics:
        parts.append(f"traj {r.metrics['trajectory_accuracy']:.2f}")
    if "step_efficiency" in r.metrics:
        parts.append(f"eff {r.metrics['step_efficiency']:.2f}")
    return " · ".join(parts) if parts else "-"


def detail_cell(r: Result, max_len: int = 80) -> str:
    """First failed check, or truncated judge reasoning, or error."""
    for c in r.check_results:
        if not c.passed:
            return f"[{c.check}] {c.detail}"
    if r.judge_result and not r.judge_result.passed:
        reasoning = r.judge_result.reasoning
        return reasoning[:max_len] + "..." if len(reasoning) > max_len else reasoning
    if r.error:
        # Show the last non-empty line — the actionable part (e.g. the exception).
        # Full error is available in report output.
        return next((line for line in reversed(r.error.splitlines()) if line.strip()), r.error)
    return ""


def summary_counts(results: list[Result]) -> tuple[int, int, int, int, int]:
    """Return (passed, failed, errors, uncertain, total)."""
    passed = sum(1 for r in results if r.status == ResultStatus.PASS)
    failed = sum(1 for r in results if r.status == ResultStatus.FAIL)
    errors = sum(1 for r in results if r.status == ResultStatus.ERROR)
    uncertain = sum(1 for r in results if r.status == ResultStatus.UNCERTAIN)
    return passed, failed, errors, uncertain, len(results)


def summary_line(results: list[Result]) -> str:
    """Rich markup summary: '3/5 passed  1 failed  1 uncertain  1 errors'."""
    passed, failed, errors, uncertain, total = summary_counts(results)
    parts = [f"[bold]{passed}/{total} passed[/bold]"]
    if failed:
        parts.append(f"[{COLOR_FAIL}]{failed} failed[/{COLOR_FAIL}]")
    if uncertain:
        parts.append(f"[{COLOR_UNCERTAIN}]{uncertain} uncertain[/{COLOR_UNCERTAIN}]")
    if errors:
        parts.append(f"[{COLOR_ERROR}]{errors} errors[/{COLOR_ERROR}]")
    return "  ".join(parts)


def cost_cell(r: Result) -> str:
    """Format the cost column: '$0.0012' or '-'."""
    if r.trace and r.trace.cost_usd > 0:
        return f"${r.trace.cost_usd:.4f}"
    return "-"


def build_results_table(results: list[Result], *, title: str | None = None) -> Table:
    """Build a Rich table for eval results."""
    table = Table(show_header=True, header_style=HEADER_STYLE, title=title)
    table.add_column("Scenario", style=COLOR_DIM)
    table.add_column("Status")
    table.add_column("Checks")
    table.add_column("Judge")
    table.add_column("Metrics")
    table.add_column("Cost", justify="right")
    table.add_column("Details")

    for r in results:
        # Use Text() for detail to prevent Rich interpreting [check_name] as markup
        table.add_row(
            r.scenario_id,
            status_badge(r.status),
            checks_cell(r),
            judge_cell(r),
            metrics_cell(r),
            cost_cell(r),
            Text(detail_cell(r)),
        )
    return table


def build_run_table(manifest: RunManifest) -> Table:
    """Build a Rich table for run outcomes."""
    table = Table(
        show_header=True,
        header_style=HEADER_STYLE,
        title=f"kensa run {manifest.run_id}",
    )
    table.add_column("Scenario", style=COLOR_DIM)
    table.add_column("Runs")
    table.add_column("Status")
    table.add_column("Duration")
    table.add_column("Details")

    for sid, runs in manifest.scenarios.items():
        num_runs = len(runs)
        if num_runs == 1:
            sr = runs[0]
            detail = ""
            if sr.stderr:
                detail = sr.stderr.splitlines()[-1][:80]
            table.add_row(sid, "1", run_status_badge(sr), f"{sr.duration_seconds}s", detail)
        else:
            ok = sum(1 for sr in runs if sr.exit_code == 0)
            avg_dur = sum(sr.duration_seconds for sr in runs) / num_runs
            color = COLOR_PASS if ok == num_runs else COLOR_ERROR
            status_text = Text(f"{ok}/{num_runs} OK", style=f"{color} bold")
            last_err = ""
            for sr in runs:
                if sr.stderr:
                    last_err = sr.stderr.splitlines()[-1][:80]
            table.add_row(sid, str(num_runs), status_text, f"{avg_dur:.1f}s avg", last_err)

    return table


def print_results(results: list[Result]) -> None:
    """Print summary + results table to terminal."""
    console = Console()
    console.print()
    console.print(f"[bold]kensa[/bold]  {summary_line(results)}", highlight=False)
    console.print()
    console.print(build_results_table(results))
    console.print()


def print_run(manifest: RunManifest) -> None:
    """Print run outcomes table to terminal."""
    console = Console()
    console.print(build_run_table(manifest))


def build_analysis_table(analysis: Analysis) -> Table:
    """Build a Rich table summarising trace analysis."""
    table = Table(show_header=True, header_style=HEADER_STYLE, title="Trace Analysis")
    table.add_column("Metric", style=COLOR_DIM)
    table.add_column("p50")
    table.add_column("p90")
    table.add_column("p99")
    table.add_column("Max")

    cd = analysis.cost_distribution
    table.add_row("Cost ($)", f"{cd.p50:.4f}", f"{cd.p90:.4f}", f"{cd.p99:.4f}", f"{cd.max:.4f}")
    ld = analysis.latency_distribution
    table.add_row("Latency (s)", f"{ld.p50:.2f}", f"{ld.p90:.2f}", f"{ld.p99:.2f}", f"{ld.max:.2f}")
    return table


def build_tool_usage_table(analysis: Analysis) -> Table:
    """Build a Rich table for tool usage stats."""
    table = Table(show_header=True, header_style=HEADER_STYLE, title="Tool Usage")
    table.add_column("Tool", style=COLOR_DIM)
    table.add_column("Calls")
    table.add_column("Avg Latency (ms)")
    table.add_column("Error Rate")

    for tu in analysis.tool_usage:
        latency = f"{tu.avg_latency_ms:.0f}" if tu.metrics_available else "n/a"
        error = f"{tu.error_rate:.1%}" if tu.metrics_available else "n/a"
        table.add_row(tu.tool, str(tu.call_count), latency, error)
    return table


def _render_analysis(analysis: Analysis, console: Console) -> None:
    """Render analysis output to the given console."""
    console.print()
    console.print(
        f"[bold]kensa analyze[/bold]  {analysis.trace_count} traces, "
        f"{analysis.success_rate:.0%} success rate",
        highlight=False,
    )
    console.print()
    console.print(build_analysis_table(analysis))

    if analysis.tool_usage:
        console.print()
        console.print(build_tool_usage_table(analysis))

    if analysis.flagged_traces:
        console.print()
        console.print(f"[bold]Flagged traces[/bold] ({len(analysis.flagged_traces)})")
        for ft in analysis.flagged_traces:
            console.print(
                f"  [{COLOR_ERROR}]{ft.flag.value}[/{COLOR_ERROR}] {ft.trace_id}: {ft.detail}"
            )

    console.print()


def format_analysis(analysis: Analysis) -> str:
    """Render trace analysis as plain text (for file output)."""
    console = Console(record=True, width=100)
    _render_analysis(analysis, console)
    return console.export_text()


def print_analysis(analysis: Analysis) -> None:
    """Print trace analysis summary to terminal with Rich formatting."""
    _render_analysis(analysis, Console())


class _PipeSpinner:
    """Renderable: │  ⠋ label — keeps the pipe at column 0."""

    def __init__(self, label: str) -> None:
        self._spinner = Spinner("dots", style="dim")
        self._label = label

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        frame: Text = self._spinner.render(console.get_time())  # type: ignore[assignment]
        yield Text.assemble("│  ", frame, f" {self._label}", style="dim")


class Steps:
    """Clack-inspired step output for polished terminal UX."""

    def __init__(self, *, quiet: bool = False) -> None:
        self._c = Console()
        self._quiet = quiet

    def start(self, label: str) -> None:
        if self._quiet:
            return
        self._c.print()
        self._c.print(f"┌  {label}", highlight=False)
        self._c.print("│", highlight=False)

    def step(self, label: str) -> None:
        if self._quiet:
            return
        self._c.print(f"◇  {label}", highlight=False)

    def item(self, text: str, *, ok: bool = True) -> None:
        if self._quiet:
            return
        color = COLOR_PASS if ok else COLOR_FAIL
        marker = "✓" if ok else "✗"
        self._c.print(f"│  [{color}]{marker}[/{color}] {text}", highlight=False)

    def text(self, label: str) -> None:
        if self._quiet:
            return
        self._c.print(f"│  {label}", highlight=False)

    def line(self) -> None:
        if self._quiet:
            return
        self._c.print("│", highlight=False)

    def result(self, text: str) -> None:
        if self._quiet:
            return
        self._c.print(f"◆  {text}", highlight=False)

    @contextmanager
    def spinner(self, label: str) -> Generator[Live | None, None, None]:
        """Show a spinner with a label while a block runs. No-ops in quiet mode."""
        if self._quiet:
            yield None
            return
        live = Live(
            _PipeSpinner(label),
            console=self._c,
            refresh_per_second=10,
            transient=True,
        )
        live.start()
        try:
            yield live
        finally:
            live.stop()

    def end(self, text: str = "Done!") -> None:
        if self._quiet:
            return
        self._c.print("│", highlight=False)
        self._c.print(f"└  {text}", highlight=False)
        self._c.print()
