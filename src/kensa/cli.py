"""CLI: Click group with run, judge, report commands."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import sys
from pathlib import Path

import click
from rich.markup import escape as rich_escape

from kensa.judge import JudgeProvider
from kensa.models import Result, ResultStatus, RunManifest
from kensa.paths import (
    AGENT_DIR,
    JUDGE_DIR,
    REPORT_DIR,
    RESULT_DIR,
    SCENARIO_DIR,
    TRACE_DIR,
    latest_manifest,
)
from kensa.styles import Steps, detail_cell, summary_line

# fmt: off
LOGO = (
    "┌────────────────────────────────────────────────────────┐\n"
    "│ ░██    ░██ ░███████  ░████████   ░███████   ░██████    │\n"
    "│ ░██   ░██ ░██    ░██ ░██    ░██ ░██              ░██   │\n"
    "│ ░███████  ░█████████ ░██    ░██  ░███████   ░███████   │\n"
    "│ ░██   ░██ ░██        ░██    ░██        ░██ ░██   ░██   │\n"
    "│ ░██    ░██ ░███████  ░██    ░██  ░███████   ░█████░██  │\n"
    "└────────────────────────────────────────────────────────┘"
)
# fmt: on

CONTEXT_SETTINGS = {"max_content_width": 120}

_SAFE_RUN_ID = re.compile(r"^[\w.-]+$")


def _get_version() -> str:
    try:
        return importlib.metadata.version("kensa")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


_COMMAND_ORDER = ["init", "doctor", "run", "judge", "report", "eval", "analyze"]


class KensaGroup(click.Group):
    """Custom group that displays the kensa banner in help."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        commands = super().list_commands(ctx)
        ordered = [c for c in _COMMAND_ORDER if c in commands]
        remaining = [c for c in commands if c not in ordered]
        return ordered + remaining

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write("\n")
        formatter.write(click.style(LOGO, bold=True))
        formatter.write(click.style(f"\n\n  v{_get_version()}\n\n", dim=True))
        super().format_help(ctx, formatter)


def _version_callback(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo()
    click.echo(click.style(LOGO, bold=True))
    click.echo(click.style(f"\n  kensa v{_get_version()}\n", dim=True))
    ctx.exit()


def _validate_run_id(run_id: str) -> str:
    """Reject run_id values that could escape the .kensa/ directory."""
    if not _SAFE_RUN_ID.match(run_id):
        raise click.BadParameter(f"Invalid run ID: {run_id!r}")
    return run_id


# --- Shared helpers ---


def _run_judge_manifest(
    manifest: RunManifest,
    judge_provider: JudgeProvider | None,
    scenario_dir: Path,
) -> list[Result]:
    """Judge all scenarios, printing skip messages to stderr."""
    from kensa.judge import judge_manifest

    results, skipped = judge_manifest(manifest, judge_provider, scenario_dir)
    for msg in skipped:
        click.echo(f"  {msg}, skipping", err=True)
    return results


def _save_results(run_id: str, results: list[Result]) -> None:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULT_DIR / f"{run_id}.json"
    with open(results_path, "w") as f:
        json.dump([r.model_dump(mode="json") for r in results], f, indent=2)


def _save_html_report(run_id: str, results: list[Result]) -> Path:
    """Generate HTML report, update latest symlink, return path."""
    from kensa.paths import latest_report_link, report_path
    from kensa.report import format_html

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = report_path(run_id)
    html_path.write_text(format_html(results))
    link = latest_report_link()
    link.unlink(missing_ok=True)
    link.symlink_to(html_path.name)
    return html_path


@click.group(cls=KensaGroup, context_settings=CONTEXT_SETTINGS)
@click.option(
    "--version",
    is_flag=True,
    callback=_version_callback,
    expose_value=False,
    is_eager=True,
    help="Show version.",
)
def cli() -> None:
    """The open source agent evals harness."""


@cli.command(
    epilog="""\b
Examples:
  kensa run
  kensa run -s my_scenario
  kensa run --scenario-dir ./custom/scenarios --timeout 600
  kensa run --dry-run
  kensa run --format json""",
)
@click.option(
    "--scenario-dir",
    default=str(SCENARIO_DIR),
    help="Directory containing scenario YAML files.",
)
@click.option("--scenario-id", "-s", multiple=True, help="Specific scenario IDs to run.")
@click.option("--timeout", default=300, help="Timeout per scenario in seconds.")
@click.option("--dry-run", is_flag=True, help="List scenarios that would run, without executing.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def run(
    scenario_dir: str,
    scenario_id: tuple[str, ...],
    timeout: int,
    dry_run: bool,
    fmt: str,
) -> None:
    """Run scenarios and capture traces."""
    from kensa.runner import load_scenarios, run_scenarios

    ids = list(scenario_id) if scenario_id else None
    try:
        if dry_run:
            scenarios = load_scenarios(scenario_dir=scenario_dir, scenario_ids=ids)
            if fmt == "json":
                click.echo(
                    json.dumps(
                        {"dry_run": True, "scenarios": [s.id for s in scenarios]},
                        indent=2,
                    )
                )
            else:
                click.echo(f"Would run {len(scenarios)} scenario(s):")
                for s in scenarios:
                    click.echo(f"  {s.id}")
            return

        s = Steps(quiet=fmt == "json")
        s.start("kensa run")
        with s.spinner("Running scenarios..."):
            manifest = run_scenarios(
                scenario_dir=scenario_dir,
                scenario_ids=ids,
                timeout=timeout,
            )
        if fmt == "json":
            click.echo(json.dumps(manifest.model_dump(mode="json"), indent=2))
        else:
            for sid, runs in manifest.scenarios.items():
                ok = sum(1 for sr in runs if sr.exit_code == 0)
                total = len(runs)
                avg_dur = sum(sr.duration_seconds for sr in runs) / total
                if total == 1:
                    sr = runs[0]
                    detail = ""
                    if sr.exit_code != 0 and sr.stderr:
                        detail = f" — {rich_escape(sr.stderr.splitlines()[-1])}"
                    s.item(
                        f"{sid} ({sr.duration_seconds:.1f}s){detail}",
                        ok=sr.exit_code == 0,
                    )
                else:
                    detail = ""
                    if ok == 0:
                        first_err = next(
                            (sr.stderr.splitlines()[-1] for sr in runs if sr.stderr), ""
                        )
                        if first_err:
                            detail = f"\n│    {rich_escape(first_err)}"
                    s.item(
                        f"{sid} x{total} ({avg_dur:.1f}s avg, {ok}/{total} ok){detail}",
                        ok=ok == total,
                    )
            completed = sum(
                1 for runs in manifest.scenarios.values() for sr in runs if sr.exit_code == 0
            )
            total_runs = sum(len(runs) for runs in manifest.scenarios.values())
            s.line()
            s.result(f"[bold]{completed}/{total_runs} completed[/bold]")
            s.end()
    except FileNotFoundError as e:
        click.echo(f"Error: {e}\n  Create scenarios in: {scenario_dir}/", err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(f"Error: {e}\n  List available: ls {scenario_dir}/", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _latest_manifest() -> RunManifest:
    """Find the most recent run manifest."""
    with open(latest_manifest()) as f:
        return RunManifest.model_validate_json(f.read())


@cli.command(
    epilog="""\b
Examples:
  kensa judge
  kensa judge --run-id abc123
  kensa judge --model claude-sonnet-4-6
  kensa judge --format json""",
)
@click.option("--run-id", default=None, help="Specific run ID to judge.")
@click.option("--model", default=None, help="Judge model override.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def judge(run_id: str | None, model: str | None, fmt: str) -> None:
    """Score the latest run with checks + LLM judge."""
    from kensa.judge import get_judge, manifest_requires_judge

    try:
        if run_id:
            _validate_run_id(run_id)
            from kensa.paths import manifest_path

            with open(manifest_path(run_id)) as f:
                manifest = RunManifest.model_validate_json(f.read())
        else:
            manifest = _latest_manifest()

        s = Steps(quiet=fmt == "json")
        s.start(f"kensa judge [dim]{manifest.run_id}[/dim]")
        judge_provider = (
            get_judge(model) if manifest_requires_judge(manifest, SCENARIO_DIR) else None
        )
        with s.spinner("Judging results..."):
            results = _run_judge_manifest(manifest, judge_provider, SCENARIO_DIR)
        _save_results(manifest.run_id, results)

        if fmt == "json":
            passed = sum(1 for r in results if r.status == ResultStatus.PASS)
            click.echo(
                json.dumps(
                    {
                        "run_id": manifest.run_id,
                        "total": len(results),
                        "passed": passed,
                        "results": [r.model_dump(mode="json") for r in results],
                    },
                    indent=2,
                )
            )
        else:
            for r in results:
                detail = ""
                if r.status != ResultStatus.PASS:
                    d = detail_cell(r)
                    if d:
                        detail = f" — {rich_escape(d)}"
                s.item(f"{r.scenario_id}{detail}", ok=r.status == ResultStatus.PASS)
            s.line()
            s.result(summary_line(results))
            s.end()

    except FileNotFoundError as e:
        click.echo(f"Error: {e}\n  Run: kensa run", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command(
    epilog="""\b
Examples:
  kensa report
  kensa report --format markdown
  kensa report --format json
  kensa report --run-id abc123 -o results.md --format markdown""",
)
@click.option("--run-id", default=None, help="Specific run ID to report.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["terminal", "markdown", "json", "html"]),
    default="terminal",
    help="Output format.",
)
@click.option("--output", "-o", default=None, help="Write to file instead of stdout.")
@click.option("--verbose", "-v", is_flag=True, help="Show full check details and judge reasoning.")
def report(run_id: str | None, fmt: str, output: str | None, verbose: bool) -> None:
    """Generate a report from the latest run."""
    from kensa.report import FORMATTERS, format_terminal

    try:
        if not run_id:
            run_id = _latest_manifest().run_id
        _validate_run_id(run_id)
        from kensa.paths import results_path

        rpath = results_path(run_id)
        if not rpath.exists():
            raise FileNotFoundError(
                f"No results for run '{run_id}'.\n  Run: kensa judge --run-id {run_id}"
            )

        with open(rpath) as f:
            data = json.load(f)
        results = [Result.model_validate(r) for r in data]

        if verbose and fmt == "terminal":
            text = format_terminal(results, verbose=True)
        else:
            formatter = FORMATTERS[fmt]
            text = formatter(results)

        if output:
            Path(output).write_text(text)
            click.echo(f"Report written to {output}")
        else:
            click.echo(text)

        html_path = _save_html_report(run_id, results)
        if fmt not in {"json", "html"} or output:
            click.echo(click.style(f"HTML report: {html_path}", dim=True))

    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command(
    name="eval",
    epilog="""\b
Examples:
  kensa eval
  kensa eval -s my_scenario
  kensa eval --format json""",
)
@click.option(
    "--scenario-dir",
    default=str(SCENARIO_DIR),
    help="Directory containing scenario YAML files.",
)
@click.option("--scenario-id", "-s", multiple=True, help="Specific scenario IDs to run.")
@click.option("--timeout", default=300, help="Timeout per scenario in seconds.")
@click.option("--model", default=None, help="Judge model override.")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["terminal", "markdown", "json"]),
    default="terminal",
    help="Report format.",
)
def eval_cmd(
    scenario_dir: str,
    scenario_id: tuple[str, ...],
    timeout: int,
    model: str | None,
    fmt: str,
) -> None:
    """Run + judge + report in one shot."""
    from kensa.judge import get_judge
    from kensa.report import FORMATTERS
    from kensa.runner import load_scenarios, run_scenarios

    ids = list(scenario_id) if scenario_id else None
    try:
        # Pre-flight: if any scenario needs a judge, resolve it now before
        # spending time (and money) running scenarios.
        scenarios = load_scenarios(scenario_dir=scenario_dir, scenario_ids=ids)
        needs_judge = any(sc.criteria or sc.judge for sc in scenarios)
        judge_provider = get_judge(model) if needs_judge else None

        s = Steps(quiet=fmt != "terminal")
        s.start("kensa eval")
        with s.spinner("Running scenarios..."):
            manifest = run_scenarios(
                scenario_dir=scenario_dir,
                timeout=timeout,
                scenarios=scenarios,
            )
        with s.spinner("Judging results..."):
            results = _run_judge_manifest(manifest, judge_provider, Path(scenario_dir))
        _save_results(manifest.run_id, results)

        html_path = _save_html_report(manifest.run_id, results)

        if fmt == "terminal":
            # Single combined list: one line per result with status, cost, detail
            for r in results:
                parts = [r.scenario_id]
                if r.trace:
                    stats = []
                    stats.append(f"{r.trace.duration_seconds:.1f}s")
                    if r.trace.cost_usd > 0:
                        stats.append(f"${r.trace.cost_usd:.4f}")
                    parts.append(f"({', '.join(stats)})")
                if r.status != ResultStatus.PASS:
                    d = detail_cell(r)
                    if d:
                        parts.append(f"— {rich_escape(d)}")
                s.item(" ".join(parts), ok=r.status == ResultStatus.PASS)
            s.line()
            s.result(summary_line(results))
            s.end(f"[dim]HTML report: {html_path}[/dim]")
        else:
            click.echo(FORMATTERS[fmt](results))

    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command(
    epilog="""\b
Examples:
  kensa analyze
  kensa analyze --trace-dir custom/traces
  kensa analyze --format json
  kensa analyze --format json -o analysis.json""",
)
@click.option(
    "--trace-dir",
    default=str(TRACE_DIR),
    help="Directory containing trace JSONL files.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option("--output", "-o", default=None, help="Write to file instead of stdout.")
def analyze(trace_dir: str, fmt: str, output: str | None) -> None:
    """Surface cost, latency, and anomalies across runs."""
    from kensa.analyzer import analyze_traces
    from kensa.styles import format_analysis

    try:
        analysis = analyze_traces(trace_dir)

        if fmt == "json":
            text = json.dumps(analysis.model_dump(mode="json"), indent=2)
        else:
            text = format_analysis(analysis)

        if output:
            Path(output).write_text(text)
            click.echo(f"Analysis written to {output}")
        else:
            click.echo(text)

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite existing example scenario.")
@click.option(
    "--blank", is_flag=True, help="Scaffold directories only, skip example agent and scenario."
)
def init(force: bool, blank: bool) -> None:
    """Set up .kensa/ dir with example agent."""
    from kensa.doctor import run_doctor

    s = Steps()
    s.start("kensa init")

    dirs = [SCENARIO_DIR, TRACE_DIR, JUDGE_DIR, AGENT_DIR]
    any_created = False
    for d in dirs:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            any_created = True
    if any_created:
        s.item("created .kensa/")
    else:
        s.item(".kensa/ already scaffolded")

    if not blank:
        agent_file = AGENT_DIR / "example.py"
        dataset_file = SCENARIO_DIR / "example.jsonl"
        example = SCENARIO_DIR / "example.yaml"
        if example.exists() and not force:
            s.item("example scenario ready (--force to regenerate)")
        else:
            agent_tpl, scenario_tpl, dataset_tpl, _ = _pick_templates()
            agent_file.write_text(agent_tpl)
            s.item(f"wrote {agent_file}")
            if dataset_tpl:
                dataset_file.write_text(dataset_tpl)
                s.item(f"wrote {dataset_file}")
            example.write_text(scenario_tpl)
            s.item(f"wrote {example}")

    checks = run_doctor()
    if blank:
        checks = [(n, ok, d) for n, ok, d in checks if n != "scenarios"]
    passed = sum(1 for _, ok, _ in checks if ok)
    failures = [(name, detail) for name, ok, detail in checks if not ok]
    s.line()
    if failures:
        s.step(f"Environment — {passed}/{len(checks)} checks passed")
        for name, detail in failures:
            s.item(f"{name}: {rich_escape(detail)}", ok=False)
    else:
        s.step(f"Environment — all {len(checks)} checks passed")

    next_steps: list[str] = []
    if failures:
        next_steps.append("Fix issues above (see kensa doctor for details)")
    if blank:
        next_steps.append(".kensa/scenarios/   ← add your scenarios here")
    else:
        next_steps += [
            "kensa eval          ← run the example",
            ".kensa/scenarios/   ← add your own scenarios",
        ]
    if not failures:
        next_steps.append("kensa doctor        ← full environment details")

    s.line()
    s.text("Next steps")
    s.line()
    for i, text in enumerate(next_steps, 1):
        s.text(f"{i}. {text}")

    s.line()
    s.end()


def _pick_templates() -> tuple[str, str, str, str]:
    """Return (agent, scenario, dataset, provider) based on available API keys."""
    from kensa.runner import ensure_dotenv_loaded

    ensure_dotenv_loaded()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _ANTHROPIC_AGENT, _LIVE_SCENARIO, _LIVE_DATASET, "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return _OPENAI_AGENT, _LIVE_SCENARIO, _LIVE_DATASET, "openai"
    return _STUB_AGENT, _STUB_SCENARIO, "", ""


_ANTHROPIC_AGENT = """\
from kensa import instrument

instrument()

import sys

import anthropic

SYSTEM = (
    "You are a support ticket triage agent. Given a customer message, classify "
    "its priority as exactly one of: P1, P2, or P3.\\n\\n"
    "P1 = service outage or data loss affecting multiple users\\n"
    "P2 = degraded functionality or bug blocking a single user's workflow\\n"
    "P3 = cosmetic issue, feature request, or general question\\n\\n"
    "Classify based on actual business impact, not the customer's tone or "
    "self-declared urgency. Output only the label (P1, P2, or P3), nothing else."
)

client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=16,
    system=SYSTEM,
    messages=[{"role": "user", "content": sys.argv[1]}],
)
print(response.content[0].text)
"""

_OPENAI_AGENT = """\
from kensa import instrument

instrument()

import sys

import openai

SYSTEM = (
    "You are a support ticket triage agent. Given a customer message, classify "
    "its priority as exactly one of: P1, P2, or P3.\\n\\n"
    "P1 = service outage or data loss affecting multiple users\\n"
    "P2 = degraded functionality or bug blocking a single user's workflow\\n"
    "P3 = cosmetic issue, feature request, or general question\\n\\n"
    "Classify based on actual business impact, not the customer's tone or "
    "self-declared urgency. Output only the label (P1, P2, or P3), nothing else."
)

client = openai.OpenAI()
response = client.chat.completions.create(
    model="gpt-5.4-mini",
    max_tokens=16,
    messages=[
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": sys.argv[1]},
    ],
)
print(response.choices[0].message.content)
"""

_STUB_AGENT = """\
# Add these two lines to your real agent (before SDK imports):
# from kensa import instrument
# instrument()

import sys

message = sys.argv[1] if len(sys.argv) > 1 else ""
# Stub: always outputs P2. Replace with your real agent logic.
print("P2")
"""

_LIVE_DATASET = (
    '{"ticket": "Our entire team can\'t log in — SSO returns'
    " 502. We're completely blocked since 7am.\""
    ', "expected": "P1"}\n'
    '{"ticket": "Would be great if the dashboard had dark'
    ' mode. Not urgent, just a nice-to-have."'
    ', "expected": "P3"}\n'
    '{"ticket": "PDF exports render charts without axis'
    " labels since last Tuesday's update.\""
    ', "expected": "P2"}\n'
    '{"ticket": "URGENT!!! CRITICAL!!! Change my invoice'
    ' font to Arial. BLOCKING my entire business!!!"'
    ', "expected": "P3"}\n'
    '{"ticket": "A few users are seeing stale numbers on'
    " the dashboard — totals don't match the API."
    " Started after this morning's deploy.\""
    ', "expected": "P1"}\n'
    '{"ticket": "The export button is broken (just spins),'
    " the logo on reports looks pixelated, and we're"
    ' being billed for 50 seats but only have 30."'
    ', "expected": "P2"}\n'
)

_LIVE_SCENARIO = """\
# Example scenario — edit this for your agent.
# Full reference: https://github.com/satyaborg/kensa/blob/main/README.md

id: example
name: Support ticket triage
description: Classify support tickets by priority based on business impact.
source: user

dataset: example.jsonl
input_field: ticket

run_command: [python, .kensa/agents/example.py]

expected_outcome: Agent assigns the correct priority label for each ticket.

checks:
  - type: output_matches
    params: { pattern: "^P[123]$" }
    description: Output must be exactly P1, P2, or P3.
  - type: max_cost
    params: { max_usd: 0.05 }
    description: Each classification should cost less than $0.05.

criteria: |
  The agent must assign priority based on actual business impact:
  - P1 for outages or data loss affecting multiple users
  - P2 for bugs blocking a single user's workflow
  - P3 for cosmetic issues, feature requests, or general questions
  Ignore the customer's tone or self-declared urgency.
"""

_STUB_SCENARIO = """\
# Example scenario — edit this for your agent.
# Full reference: https://github.com/satyaborg/kensa/blob/main/README.md
#
# NOTE: No API key detected. This is a stub that won't produce traces.
# Set ANTHROPIC_API_KEY or OPENAI_API_KEY, then re-run: kensa init --force

id: example
name: Support ticket triage
description: Classify a support ticket by priority.
source: user

input: "When I export a report to PDF the charts render without axis labels."

run_command: [python, .kensa/agents/example.py]

expected_outcome: Agent outputs the correct priority label (P2).

checks:
  - type: output_matches
    params: { pattern: "^P[123]$" }
    description: Output must be exactly P1, P2, or P3.
"""


@cli.command()
def doctor() -> None:
    """Verify your setup is ready to run."""
    from kensa.doctor import format_doctor, run_doctor

    checks = run_doctor()
    format_doctor(checks)

    hard_fails = [n for n, ok, _ in checks if not ok and "API_KEY" not in n]
    api_checks = [(n, ok) for n, ok, _ in checks if "API_KEY" in n]
    any_api = any(ok for _, ok in api_checks)
    if hard_fails or not any_api:
        sys.exit(1)


if __name__ == "__main__":
    cli()
