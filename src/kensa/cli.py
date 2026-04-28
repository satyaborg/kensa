"""CLI: Click group with run, judge, report commands."""

from __future__ import annotations

import importlib.metadata
import json
import re
import sys
from pathlib import Path

import click
from rich.markup import escape as rich_escape

from kensa.judge import JudgeProvider
from kensa.models import Result, ResultStatus, RunManifest
from kensa.paths import (
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


_COMMAND_ORDER = [
    "init",
    "generate",
    "doctor",
    "run",
    "judge",
    "report",
    "eval",
    "analyze",
    "mcp",
    "skills",
]


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


def _running_in_project_venv() -> bool:
    """True when the active interpreter lives in the CWD's .venv/."""
    try:
        return Path(sys.prefix).resolve() == (Path.cwd() / ".venv").resolve()
    except OSError:
        return False


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _detect_agent_default(base: Path) -> str:
    """Pick the picker default by inspecting existing agent dirs and project markers."""
    claude_skills = (base / ".claude" / "skills").is_dir()
    agents_skills = (base / ".agents" / "skills").is_dir()
    if claude_skills and agents_skills:
        return "all"
    if claude_skills:
        return "claude"
    if agents_skills:
        return "codex"
    if (base / ".claude").is_dir() or (base / "CLAUDE.md").is_file():
        return "claude"
    if (base / ".agents").is_dir() or (base / "AGENTS.md").is_file():
        return "codex"
    return "all"


def _agent_install_targets(agent_choice: str) -> tuple[bool, bool]:
    """Return (claude_target, agents_target) for a user-facing coding agent choice."""
    if agent_choice == "none":
        return False, False
    if agent_choice == "claude":
        return True, False
    if agent_choice == "all":
        return True, True
    return False, True


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


AGENT_CHOICES = ["claude", "codex", "cursor", "opencode", "gemini", "other", "all", "none"]
INSTALL_AGENT_CHOICES = [c for c in AGENT_CHOICES if c != "none"]


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite existing example scenario.")
@click.option(
    "--blank", is_flag=True, help="Scaffold directories only, skip example agent and scenario."
)
@click.option(
    "-a",
    "--agent",
    "agent_choice",
    type=click.Choice(AGENT_CHOICES),
    default=None,
    help="Coding agent to install skills for. Prompts in interactive mode.",
)
@click.option(
    "--cli/--no-cli",
    "install_cli_flag",
    default=None,
    help="Add kensa to project dev deps via uv add --dev (default: install).",
)
def init(
    force: bool,
    blank: bool,
    agent_choice: str | None,
    install_cli_flag: bool | None,
) -> None:
    """Set up .kensa/ dir with example agent."""
    from kensa.doctor import run_doctor
    from kensa.scaffold import init_kensa
    from kensa.skills_install import ensure_cli_in_project, install_skills

    s = Steps()
    s.start("kensa init")

    result = init_kensa(blank=blank, force=force)

    if result.directories_created:
        s.item("created .kensa/")
    else:
        s.item(".kensa/ already scaffolded")
    for f in result.files_written:
        s.item(f"wrote {f}")
    if not blank and result.example_already_existed:
        s.item("example scenario ready (--force to regenerate)")

    interactive = _is_interactive()

    should_install_cli = True if install_cli_flag is None else install_cli_flag
    project_env_mutated = False
    if should_install_cli:
        cli_result = ensure_cli_in_project()
        s.item(cli_result.detail, ok=cli_result.status == "added")
        project_env_mutated = cli_result.status == "added"

    if agent_choice is None:
        if interactive:
            default_agent = _detect_agent_default(Path.cwd())
            agent_choice = click.prompt(
                "Install skills for which coding agent?",
                type=click.Choice(AGENT_CHOICES),
                default=default_agent,
                show_choices=True,
            )
        else:
            agent_choice = "none"

    claude_target, agents_target = _agent_install_targets(agent_choice)
    if claude_target or agents_target:
        skills_result = install_skills(
            project=True,
            claude=claude_target,
            agents=agents_target,
            force=False,
        )
        for path in skills_result.written:
            s.item(f"wrote {path}")
        for path in skills_result.skipped:
            s.item(f"skipped {path} (exists; run `kensa skills install --force` to overwrite)")

    if project_env_mutated and not _running_in_project_venv():
        s.line()
        s.item(
            "Environment checks below run in the current process, not your project venv. "
            "For accurate results, run: uv run kensa doctor"
        )

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


@cli.command(
    epilog="""\b
Examples:
  kensa generate                       # from latest run's traces
  kensa generate --run-id abc123
  kensa generate --trace path/to/trace.jsonl -n 5
  kensa generate --dry-run""",
)
@click.option("--run-id", default=None, help="Run ID to source traces from (default: latest).")
@click.option(
    "--trace",
    "traces",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="Specific trace file(s); repeatable. Overrides --run-id.",
)
@click.option(
    "-n",
    "--count",
    default=3,
    show_default=True,
    type=click.IntRange(1, 20),
    help="Number of scenarios to generate.",
)
@click.option("--model", default=None, help="LLM model override (e.g. claude-sonnet-4-6).")
@click.option("--dry-run", is_flag=True, help="Print YAML to stdout; do not write files.")
@click.option("--force", is_flag=True, help="Overwrite existing scenario files.")
@click.option(
    "--scenario-dir",
    default=str(SCENARIO_DIR),
    help="Where to write generated scenarios.",
)
@click.option(
    "--source-scenario-dir",
    default=None,
    help="Directory to scan for existing scenarios when recovering the observed "
    "run_command (default: --scenario-dir if it already contains scenarios, else "
    f"{SCENARIO_DIR}).",
)
@click.option(
    "--run-command",
    "run_command_overrides",
    multiple=True,
    help="Entrypoint argv to hint to the LLM (repeatable; quote to pass multiple args: "
    "--run-command 'python .kensa/agents/agent.py').",
)
def generate(
    run_id: str | None,
    traces: tuple[Path, ...],
    count: int,
    model: str | None,
    dry_run: bool,
    force: bool,
    scenario_dir: str,
    source_scenario_dir: str | None,
    run_command_overrides: tuple[str, ...],
) -> None:
    """Generate eval scenarios from existing traces."""
    import shlex

    from kensa.generate import (
        collect_run_commands,
        generate_from_traces,
        resolve_trace_paths,
        write_scenarios,
    )

    if run_id:
        _validate_run_id(run_id)

    s = Steps()
    s.start("kensa generate")
    try:
        trace_paths = resolve_trace_paths(run_id, traces)
        s.item(f"traces: {len(trace_paths)}")

        if run_command_overrides:
            run_commands: list[list[str]] | None = [
                shlex.split(rc) for rc in run_command_overrides if rc.strip()
            ]
        else:
            if source_scenario_dir is not None:
                source_dir = Path(source_scenario_dir)
            elif Path(scenario_dir).is_dir() and any(Path(scenario_dir).glob("*.y*ml")):
                source_dir = Path(scenario_dir)
            else:
                source_dir = SCENARIO_DIR
            run_commands = (
                collect_run_commands(
                    run_id,
                    source_dir,
                    trace_paths=list(trace_paths) if traces else None,
                )
                or None
            )

        if run_commands:
            s.item(f"entrypoints: {len(run_commands)}")
        else:
            s.item("entrypoints: (none found; LLM may hallucinate — pass --run-command)", ok=False)

        import warnings

        with (
            s.spinner(f"Generating {count} scenarios..."),
            warnings.catch_warnings(record=True) as caught,
        ):
            warnings.simplefilter("always")
            scenarios = generate_from_traces(
                trace_paths,
                count=count,
                model=model,
                run_commands=run_commands,
            )
        for w in caught:
            s.item(str(w.message), ok=False)

        if dry_run:
            from kensa.generate import _scenario_to_yaml

            s.line()
            for scenario in scenarios:
                click.echo("---")
                click.echo(_scenario_to_yaml(scenario), nl=False)
            click.echo("---")
            s.line()
            s.result(f"[bold]{len(scenarios)} scenario(s) generated (dry run)[/bold]")
            s.end()
            return

        written, skipped = write_scenarios(
            scenarios,
            force=force,
            scenario_dir=Path(scenario_dir),
        )
        for path in written:
            s.item(f"wrote {path}")
        for path in skipped:
            s.item(f"skipped {path} (exists; use --force to overwrite)", ok=False)
        s.line()
        s.result(f"[bold]{len(written)} written, {len(skipped)} skipped[/bold]")
        s.end()
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


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


@cli.command(
    epilog="""\b
Examples:
  kensa mcp
  kensa mcp --http --port 8765""",
)
@click.option("--http", "use_http", is_flag=True, help="Use HTTP transport instead of stdio.")
@click.option("--host", default="127.0.0.1", show_default=True, help="HTTP host (with --http).")
@click.option("--port", default=8765, show_default=True, type=int, help="HTTP port (with --http).")
def mcp(use_http: bool, host: str, port: int) -> None:
    """Run the kensa MCP server for LLM clients."""
    try:
        from kensa.mcp_server import run_server
    except ImportError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    transport = "http" if use_http else "stdio"
    if use_http:
        click.echo(f"kensa MCP server → http://{host}:{port}", err=True)
    run_server(transport=transport, host=host, port=port)


@cli.group()
def skills() -> None:
    """Manage kensa skills for coding agents."""


@skills.command("install")
@click.option(
    "--global",
    "global_install",
    is_flag=True,
    help="Install to ~/.claude/skills and ~/.agents/skills (default: project-scoped).",
)
@click.option(
    "-a",
    "--agent",
    "agent_choice",
    type=click.Choice(INSTALL_AGENT_CHOICES),
    default="all",
    show_default=True,
    help="Coding agent to install skills for.",
)
@click.option("--force", is_flag=True, help="Overwrite existing skill directories.")
def skills_install(global_install: bool, agent_choice: str, force: bool) -> None:
    """Install bundled skills into Claude Code and open-standard agent skill directories."""
    from kensa.skills_install import install_skills

    claude, agents = _agent_install_targets(agent_choice)

    s = Steps()
    scope = "global" if global_install else "project"
    s.start(f"kensa skills install ({scope})")

    result = install_skills(
        project=not global_install,
        claude=claude,
        agents=agents,
        force=force,
    )

    for path in result.written:
        s.item(f"wrote {path}")
    for path in result.skipped:
        s.item(f"skipped {path} (exists; use --force)")
    if not result.written and not result.skipped:
        s.item("no skills bundled")

    s.line()
    s.end()


if __name__ == "__main__":
    cli()
