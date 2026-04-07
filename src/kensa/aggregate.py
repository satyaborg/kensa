"""Multi-run aggregation: variance stats, per-assertion pass rates, flaky detection."""

from __future__ import annotations

import json
import math

from kensa.models import (
    AggregatedResult,
    AssertionStat,
    Result,
    ResultStatus,
    VarianceStats,
)

# A scenario is flagged as high-variance when its pass rate falls in the
# "neither reliably passing nor reliably failing" band.
_HIGH_VARIANCE_LOW = 0.2
_HIGH_VARIANCE_HIGH = 0.8


def compute_variance_stats(values: list[float]) -> VarianceStats:
    """Compute mean, stddev, min, max for a list of floats."""
    if not values:
        return VarianceStats()
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        stddev = 0.0
    else:
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        stddev = math.sqrt(variance)
    return VarianceStats(
        mean=round(mean, 6),
        stddev=round(stddev, 6),
        min=round(min(values), 6),
        max=round(max(values), 6),
    )


def aggregate_results(scenario_id: str, results: list[Result]) -> AggregatedResult:
    """Aggregate per-run Results into a single AggregatedResult."""
    num_runs = len(results)
    if num_runs == 0:
        return AggregatedResult(scenario_id=scenario_id, num_runs=0)

    # Status counts + pass rate
    status_counts: dict[str, int] = {}
    for r in results:
        key = r.status.value
        status_counts[key] = status_counts.get(key, 0) + 1
    pass_count = status_counts.get(ResultStatus.PASS.value, 0)
    pass_rate = pass_count / num_runs

    # Cost + duration variance
    costs = [r.trace.cost_usd for r in results if r.trace]
    durations = [r.trace.duration_seconds for r in results if r.trace]
    cost_stats = compute_variance_stats(costs)
    duration_stats = compute_variance_stats(durations)

    # Per-assertion stats
    assertion_map: dict[str, list[bool]] = {}
    for r in results:
        for cr in r.check_results:
            assertion_map.setdefault(cr.check, []).append(cr.passed)
        if r.judge_result:
            assertion_map.setdefault("llm_judge", []).append(r.judge_result.passed)

    assertion_stats: list[AssertionStat] = []
    for name, outcomes in sorted(assertion_map.items()):
        pc = sum(outcomes)
        tc = len(outcomes)
        assertion_stats.append(
            AssertionStat(
                name=name,
                pass_count=pc,
                total_count=tc,
                pass_rate=round(pc / tc, 4) if tc else 0.0,
            )
        )

    high_variance = _HIGH_VARIANCE_LOW < pass_rate < _HIGH_VARIANCE_HIGH

    return AggregatedResult(
        scenario_id=scenario_id,
        num_runs=num_runs,
        pass_rate=round(pass_rate, 4),
        status_counts=status_counts,
        cost=cost_stats,
        duration=duration_stats,
        assertion_stats=assertion_stats,
        high_variance=high_variance,
        per_run_results=results,
    )


def aggregate_all(
    results_by_scenario: dict[str, list[Result]],
) -> list[AggregatedResult]:
    """Aggregate results grouped by scenario ID."""
    return [aggregate_results(sid, runs) for sid, runs in sorted(results_by_scenario.items())]


def format_aggregate_terminal(results: list[AggregatedResult], verbose: bool = False) -> str:
    """Format aggregated results as a Rich terminal table."""
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        return format_aggregate_json(results)

    console = Console(record=True, width=120 if verbose else 100)

    total = len(results)
    reliable_pass = sum(1 for r in results if r.pass_rate == 1.0)
    flaky = sum(1 for r in results if r.high_variance)

    console.print()
    console.print(
        f"[bold]kensa[/bold]  {reliable_pass}/{total} reliable  "
        f"{flaky} flaky  "
        f"({results[0].num_runs if results else 0} runs each)",
        highlight=False,
    )
    console.print()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Scenario", style="dim")
    table.add_column("Pass Rate")
    table.add_column("Runs")
    table.add_column("Cost (mean±std)")
    table.add_column("Duration (mean±std)")
    table.add_column("Flags")

    for r in results:
        # Color pass rate
        if r.pass_rate == 1.0:
            pr_str = f"[green]{r.pass_rate:.0%}[/green]"
        elif r.pass_rate == 0.0:
            pr_str = f"[red]{r.pass_rate:.0%}[/red]"
        else:
            pr_str = f"[yellow]{r.pass_rate:.0%}[/yellow]"

        cost_str = f"${r.cost.mean:.4f}±{r.cost.stddev:.4f}"
        dur_str = f"{r.duration.mean:.1f}s±{r.duration.stddev:.1f}s"
        flags = "FLAKY" if r.high_variance else ""

        table.add_row(r.scenario_id, pr_str, str(r.num_runs), cost_str, dur_str, flags)

    console.print(table)

    if verbose:
        for r in results:
            if r.assertion_stats:
                console.print(f"\n[bold]{r.scenario_id}[/bold] assertions:", highlight=False)
                for a in r.assertion_stats:
                    if a.pass_rate == 1.0:
                        tag = "[green]"
                    elif a.pass_rate > 0:
                        tag = "[yellow]"
                    else:
                        tag = "[red]"
                    close = tag.replace("[", "[/")
                    flaky_flag = "  FLAKY" if 0 < a.pass_rate < 1 else ""
                    console.print(
                        f"  {tag}{a.pass_rate:.0%}{close}  {a.name} "
                        f"({a.pass_count}/{a.total_count}){flaky_flag}",
                        highlight=False,
                    )

    console.print()
    return console.export_text()


def format_aggregate_json(results: list[AggregatedResult]) -> str:
    """Format aggregated results as JSON."""
    return json.dumps(
        [r.model_dump(mode="json") for r in results],
        indent=2,
    )
