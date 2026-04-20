"""Focused regression tests for the ``kensa-mcp`` shim and launcher."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import re
import sys
from pathlib import Path

import pytest

from kensa import _mcp_launcher

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_PYPROJECT = REPO_ROOT / "pyproject.toml"
SHIM_PYPROJECT = REPO_ROOT / "packages" / "kensa-mcp" / "pyproject.toml"
SHIM_SRC = REPO_ROOT / "packages" / "kensa-mcp" / "src"
ROOT_SRC = REPO_ROOT / "src"


def _extract_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text()
    match = re.search(r'^version = "([^"]+)"$', text, flags=re.MULTILINE)
    assert match is not None, f"Could not find version in {pyproject_path}"
    return match.group(1)


def _write_shim_entrypoint(script_path: Path) -> None:
    script_path.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(SHIM_SRC)!r})\n"
        f"sys.path.insert(0, {str(ROOT_SRC)!r})\n"
        "from kensa_mcp import main\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


class TestLauncher:
    def test_missing_fastmcp_prints_clean_install_hint(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setitem(sys.modules, "fastmcp", None)
        monkeypatch.delitem(sys.modules, "kensa.mcp_server", raising=False)

        with pytest.raises(SystemExit) as exc:
            _mcp_launcher.main()

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "requires the 'mcp' extra" in err
        assert "uv add 'kensa[mcp]'" in err
        assert "pip install 'kensa[mcp]'" in err
        assert "Traceback" not in err
        assert "ModuleNotFoundError" not in err
        assert "kensa.mcp_server" not in sys.modules


class TestShimPackage:
    def test_import_is_safe_without_fastmcp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.syspath_prepend(str(SHIM_SRC))
        monkeypatch.setitem(sys.modules, "fastmcp", None)
        monkeypatch.delitem(sys.modules, "kensa.mcp_server", raising=False)
        monkeypatch.delitem(sys.modules, "kensa_mcp", raising=False)

        module = importlib.import_module("kensa_mcp")

        assert module.main is _mcp_launcher.main
        assert "kensa.mcp_server" not in sys.modules

    def test_pyproject_pins_same_kensa_version_and_console_script(self) -> None:
        root_version = _extract_version(ROOT_PYPROJECT)
        shim_text = SHIM_PYPROJECT.read_text()

        assert _extract_version(SHIM_PYPROJECT) == root_version
        assert f'"kensa[mcp]=={root_version}"' in shim_text
        assert 'kensa-mcp = "kensa_mcp:main"' in shim_text

    @pytest.mark.client_process
    def test_stdio_roundtrip_through_shim_entrypoint(self, tmp_path: Path) -> None:
        Client = pytest.importorskip("fastmcp").Client

        script_path = tmp_path / "shim_server.py"
        _write_shim_entrypoint(script_path)

        async def scenario() -> None:
            async with Client(str(script_path)) as client:
                assert await client.ping() is True

                tools = await client.list_tools()
                assert {tool.name for tool in tools} == {
                    "analyze",
                    "doctor",
                    "eval",
                    "init",
                    "judge",
                    "report",
                    "run",
                }

                resources = await client.list_resources()
                assert {str(resource.uri) for resource in resources} == {
                    "kensa://judges",
                    "kensa://runs",
                    "kensa://scenarios",
                }

                templates = await client.list_resource_templates()
                assert {template.uriTemplate for template in templates} == {
                    "kensa://judges/{name}",
                    "kensa://runs/{run_id}",
                    "kensa://runs/{run_id}/results",
                    "kensa://runs/{run_id}/trace/{scenario}/{index}",
                    "kensa://scenarios/{scenario_id}",
                }

                result = await client.call_tool("doctor", {})
                dumped = dataclasses.asdict(result.data)
                assert "ready" in dumped
                assert "checks" in dumped

                err = await client.call_tool("run", {"scenario_dir": "/does/not/exist"})
                err_data = dataclasses.asdict(err.data)
                assert err_data.get("code") == "scenarios_missing"

        asyncio.run(scenario())
