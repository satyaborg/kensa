#!/usr/bin/env python3
"""Check whether kensa is installed as a project dependency.

Usage:
    python scripts/check_library.py [--install]

Checks:
    1. kensa is importable in the current Python
    2. Version meets minimum requirement (0.1.0)

Flags:
    --install   Attempt automatic installation if missing

Exit codes:
    0 — kensa is installed and meets minimum version
    1 — kensa is missing or outdated (details printed)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

MIN_VERSION = "0.1.0"


def parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple."""
    return tuple(int(x) for x in v.strip().split("."))


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


def check_importable() -> tuple[str | None, str | None]:
    """Check if kensa is importable. Returns (version, error_message)."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", "from kensa import __version__; print(__version__)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip(), None
        return None, result.stderr.strip()[:200]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return None, str(e)


def install_kensa() -> bool:
    """Install kensa as a project dependency. Returns True on success."""
    mgr = _detect_pkg_manager()
    if mgr == "uv":
        cmd = ["uv", "add", "kensa"]
        label = "uv add kensa"
    else:
        cmd = [sys.executable, "-m", "pip", "install", "kensa"]
        label = "pip install kensa"

    print(f"  Installing: {label}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            print(f"  Installed via: {label}")
            return True
        print(f"  Failed ({result.returncode}): {result.stderr.strip()[:200]}")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"  Failed: {e}")

    return False


def main() -> None:
    auto_install = "--install" in sys.argv

    version, _err = check_importable()

    if version:
        if parse_version(version) >= parse_version(MIN_VERSION):
            print(f"OK: kensa {version}")
            sys.exit(0)
        else:
            install = _install_cmd('"kensa>=' + MIN_VERSION + '"')
            print(f"OUTDATED: kensa {version} < {MIN_VERSION}")
            print(f"  Upgrade: {install}")
            sys.exit(1)

    # Not importable
    install = _install_cmd("kensa")
    print("NOT FOUND: kensa is not installed as a project dependency")

    if not auto_install:
        print(f"\n  Install with: {install}")
        print("  Or run this script with --install to auto-install")
        sys.exit(1)

    print("\n  Attempting auto-install...")
    if install_kensa():
        version, _ = check_importable()
        if version:
            print(f"\n  OK: kensa {version}")
            sys.exit(0)
        else:
            print("\n  Installed but not importable. Check your Python environment.")
            sys.exit(1)
    else:
        print("\n  Could not install kensa automatically.")
        print(f"  Install manually: {install}")
        sys.exit(1)


if __name__ == "__main__":
    main()
