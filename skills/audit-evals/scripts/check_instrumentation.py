#!/usr/bin/env python3
"""Check whether an agent entry point is ready for kensa tracing.

Usage:
    python scripts/check_instrumentation.py <agent_file>

Checks for:
    1. An LLM SDK import (anthropic, openai, langchain)
    2. If manual `instrument()` is present, correct ordering before SDK imports

Note: manual `instrument()` is no longer required. The runner injects
sitecustomize.py via PYTHONPATH to auto-instrument. This script only
verifies that the SDK extras are importable and any existing manual
instrumentation is correctly ordered.

Exit codes:
    0 — agent is ready for tracing
    1 — issues found (details printed)
"""

from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

SDK_PACKAGES = {
    "anthropic",
    "openai",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langgraph",
}

INSTALL_EXTRAS = {
    "anthropic": "anthropic",
    "openai": "openai",
    "langchain": "langchain",
    "langchain_core": "langchain",
    "langchain_openai": "langchain",
    "langgraph": "langchain",
}


def _detect_pkg_manager() -> str:
    """Detect the project's package manager. Returns 'uv' or 'pip'."""
    if Path("uv.lock").exists():
        return "uv"
    if shutil.which("uv"):
        return "uv"
    return "pip"


def _install_cmd(package: str) -> str:
    """Return the install command string for a package."""
    mgr = _detect_pkg_manager()
    if mgr == "uv":
        return f"uv add {package}"
    return f"pip install {package}"


def check_file(path: Path) -> list[str]:
    """Return a list of issues found (empty = all good)."""
    source = path.read_text()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    issues: list[str] = []
    has_instrument = False
    instrument_line = -1
    detected_sdks: set[str] = set()
    sdk_first_line: dict[str, int] = {}

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            names = [alias.name for alias in node.names]

            # Check for `from kensa import instrument`
            if module == "kensa" and "instrument" in names:
                has_instrument = True
                instrument_line = node.lineno

            # Detect SDK imports
            for sdk in SDK_PACKAGES:
                if module == sdk or module.startswith(f"{sdk}.") or sdk in names:
                    detected_sdks.add(sdk)
                    if sdk not in sdk_first_line:
                        sdk_first_line[sdk] = node.lineno

    if not detected_sdks:
        issues.append("No LLM SDK imports detected (anthropic, openai, langchain).")
        return issues

    sdks_str = ", ".join(sorted(detected_sdks))

    if not has_instrument:
        extras = sorted({INSTALL_EXTRAS.get(s, s) for s in detected_sdks})
        extras_str = ", ".join(f'"kensa[{e}]"' for e in extras)
        install = _install_cmd(extras_str)
        issues.append(
            f"Detected SDK(s): {sdks_str}\n"
            f"  Ensure extras are installed: {install}\n"
            f"  (Manual instrument() is not required. The runner handles it automatically.)"
        )

    # Check ordering: if manual instrumentation is present, it should come before SDK imports
    if has_instrument:
        for sdk, line in sdk_first_line.items():
            if line < instrument_line:
                issues.append(
                    f"Ordering issue: `{sdk}` imported on line {line}, "
                    f"but instrumentation on line {instrument_line}.\n"
                    f"  Move instrumentation ABOVE the {sdk} import."
                )

    return issues


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <agent_entry_point.py>")
        sys.exit(2)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(2)

    issues = check_file(path)

    if not issues:
        print(f"OK: {path} is ready for kensa tracing.")
        sys.exit(0)

    print(f"ISSUES in {path}:\n")
    for i, issue in enumerate(issues, 1):
        print(f"  {i}. {issue}\n")
    sys.exit(1)


if __name__ == "__main__":
    main()
