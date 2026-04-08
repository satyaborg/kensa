"""Pre-flight diagnostics for kensa environments."""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

from kensa.paths import AGENT_DIR, SCENARIO_DIR, TRACE_DIR
from kensa.utils import detect_package_manager, install_hint

# SDK name -> (kensa extra name, instrumentor module path)
_SDK_EXTRAS: dict[str, tuple[str, str]] = {
    "openai": ("openai", "openinference.instrumentation.openai"),
    "anthropic": ("anthropic", "openinference.instrumentation.anthropic"),
    "langchain": ("langchain", "openinference.instrumentation.langchain"),
}

_SDK_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(openai|anthropic|langchain)\b",
    re.MULTILINE,
)


def _script_paths_from_scenarios() -> set[Path]:
    """Extract Python script paths from scenario run_commands."""
    from kensa.runner import load_scenarios

    paths: set[Path] = set()
    try:
        scenarios = load_scenarios()
    except (FileNotFoundError, ValueError):
        return paths
    for scenario in scenarios:
        for tok in scenario.run_command:
            if tok.endswith(".py"):
                candidate = Path(tok)
                if candidate.is_file():
                    paths.add(candidate)
                break
    return paths


def _detect_sdks() -> set[str]:
    """Scan agent scripts for SDK imports. Returns e.g. {"openai", "anthropic"}."""
    scripts: set[Path] = _script_paths_from_scenarios()
    # Also check .kensa/agents/ as a fallback.
    if AGENT_DIR.exists():
        scripts.update(AGENT_DIR.glob("*.py"))

    sdks: set[str] = set()
    for path in scripts:
        try:
            text = path.read_text()
        except OSError:
            continue
        sdks.update(_SDK_IMPORT_RE.findall(text))
    return sdks


def _is_importable(module: str) -> bool:
    """Check whether a module is importable without actually importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def _check_sdk(sdk: str) -> tuple[bool, str]:
    """Check that an SDK and its kensa instrumentor are importable."""
    extra, instrumentor_mod = _SDK_EXTRAS[sdk]
    sdk_ok = _is_importable(sdk)
    instr_ok = _is_importable(instrumentor_mod)

    if sdk_ok and instr_ok:
        return True, f"{sdk} + instrumentor installed"
    hint = install_hint(extra)
    if sdk_ok and not instr_ok:
        return False, f"{sdk} found but instrumentor missing. Install: {hint}"
    return False, f"{sdk} not installed. Install: {hint}"


def _check_judge() -> tuple[bool, str]:
    """Check that a judge provider can be instantiated."""
    from kensa.judge import get_judge

    try:
        judge = get_judge()
        # "AnthropicJudge" → "anthropic", "OpenAIJudge" → "openai"
        return True, type(judge).__name__.removesuffix("Judge").lower()
    except ImportError as e:
        return False, str(e)
    except RuntimeError as e:
        # No API keys configured — not a hard fail, covered by API key checks.
        return False, str(e).split("\n")[0]


def _check_package_manager() -> tuple[bool, str]:
    """Report the detected package manager."""
    pm = detect_package_manager()
    return True, pm


def _check_python_version() -> tuple[bool, str]:
    v = sys.version_info
    ok = v >= (3, 10)
    detail = f"Python {v.major}.{v.minor}.{v.micro}"
    if not ok:
        detail += " (requires 3.10+)"
    return ok, detail


def _check_scenarios() -> tuple[bool, str]:
    if not SCENARIO_DIR.exists():
        return False, f"{SCENARIO_DIR}/ does not exist"
    yamls = list(SCENARIO_DIR.glob("*.yaml")) + list(SCENARIO_DIR.glob("*.yml"))
    if not yamls:
        return False, f"{SCENARIO_DIR}/ has no YAML files"
    n = len(yamls)
    label = "scenario" if n == 1 else "scenarios"
    return True, f"{n} {label} in {SCENARIO_DIR}/"


def _check_api_key(name: str) -> tuple[bool, str]:
    if os.environ.get(name):
        return True, "set"
    return False, "not set"


def _check_trace_dir_writable() -> tuple[bool, str]:
    try:
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        probe = TRACE_DIR / ".doctor_probe"
        probe.write_text("ok")
        probe.unlink()
        return True, f"{TRACE_DIR}/ writable"
    except OSError as e:
        return False, f"{TRACE_DIR}/ not writable: {e}"


def _check_dotenv() -> tuple[bool, str]:
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            try:
                return True, str(candidate.relative_to(current))
            except ValueError:
                return True, str(candidate)
    return False, "no .env found (walking up from cwd)"


def run_doctor() -> list[tuple[str, bool, str]]:
    """Run all diagnostic checks.

    Returns a list of (check_name, passed, detail) tuples.
    """
    from kensa.runner import ensure_dotenv_loaded

    ensure_dotenv_loaded()

    checks: list[tuple[str, bool, str]] = []

    name_fn_pairs: list[tuple[str, tuple[bool, str]]] = [
        ("python", _check_python_version()),
        ("pkg manager", _check_package_manager()),
        ("scenarios", _check_scenarios()),
        (".env file", _check_dotenv()),
        ("trace dir", _check_trace_dir_writable()),
        ("ANTHROPIC_API_KEY", _check_api_key("ANTHROPIC_API_KEY")),
        ("OPENAI_API_KEY", _check_api_key("OPENAI_API_KEY")),
    ]

    for name, (ok, detail) in name_fn_pairs:
        checks.append((name, ok, detail))

    # SDK checks: scan agent scripts for imports, verify they're installable.
    detected_sdks = _detect_sdks()
    for sdk in sorted(detected_sdks):
        ok, detail = _check_sdk(sdk)
        checks.append((f"{sdk} sdk", ok, detail))

    # Judge check: can we instantiate a judge provider?
    checks.append(("judge", *_check_judge()))

    return checks


_SECTION: dict[str, str] = {
    "python": "environment",
    "pkg manager": "environment",
    "scenarios": "config",
    ".env file": "config",
    "trace dir": "config",
    "ANTHROPIC_API_KEY": "keys",
    "OPENAI_API_KEY": "keys",
    "judge": "judge",
}


def format_doctor(checks: list[tuple[str, bool, str]]) -> None:
    """Print doctor results using the Steps UI."""
    from rich.markup import escape as rich_escape

    from kensa.styles import Steps

    s = Steps()
    s.start("kensa doctor")

    prev_section = ""
    for name, ok, detail in checks:
        section = _SECTION.get(name, "sdk")
        if section != prev_section:
            if prev_section:
                s.line()
            s.text(f"[dim]{section}[/dim]")
            prev_section = section
        s.item(f"{name}: {rich_escape(detail)}", ok=ok)

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    s.line()

    api_checks = [(n, ok) for n, ok, _ in checks if "API_KEY" in n]
    any_api = any(ok for _, ok in api_checks)
    soft = {"API_KEY", "sdk", "judge"}
    hard_fails = [n for n, ok, _ in checks if not ok and not any(s in n for s in soft)]

    summary = f"[bold]{passed}/{total} checks passed[/bold]"

    if hard_fails:
        s.result(summary)
        s.end(f"fix: {', '.join(hard_fails)}")
    elif not any_api:
        s.result(summary)
        s.end("fix: set at least one API key (ANTHROPIC_API_KEY or OPENAI_API_KEY)")
    else:
        s.result(summary)
        s.end()
