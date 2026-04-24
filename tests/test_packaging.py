"""Regression tests for packaging metadata that must stay in sync."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"


def _extract_pyproject_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text()
    match = re.search(r'^version = "([^"]+)"$', text, flags=re.MULTILINE)
    assert match is not None, f"Could not find version in {pyproject_path}"
    return match.group(1)


def _extract_editable_lock_version(lock_path: Path, package_name: str) -> str:
    text = lock_path.read_text()
    pattern = re.compile(
        rf'^\[\[package\]\]\n'
        rf'name = "{re.escape(package_name)}"\n'
        rf'version = "([^"]+)"\n'
        r'source = \{ editable = "\." \}',
        flags=re.MULTILINE,
    )
    match = pattern.search(text)
    assert match is not None, f"Could not find editable {package_name} entry in {lock_path}"
    return match.group(1)


def test_uv_lock_matches_root_package_version() -> None:
    assert _extract_editable_lock_version(UV_LOCK, "kensa") == _extract_pyproject_version(
        ROOT_PYPROJECT
    )
