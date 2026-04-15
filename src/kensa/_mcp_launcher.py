"""Install-hint wrapper around ``kensa.mcp_server.main``.

Kept separate from ``kensa.mcp_server`` so a missing ``fastmcp`` install
prints a single-line hint instead of a multi-frame import traceback. Consumed
by the ``kensa-mcp`` PyPI shim package (``packages/kensa-mcp/``), whose
console script points at :func:`main` here.

The launcher only discriminates the specific failure mode that warrants the
friendly hint — ``fastmcp`` itself being missing. Any other ImportError from
``kensa.mcp_server`` is a real bug and propagates untouched.
"""

from __future__ import annotations

import sys


def main() -> None:
    """Launch the MCP server, with a clean hint when the extra is absent."""
    try:
        import fastmcp  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "The kensa MCP server requires the 'mcp' extra.\n"
            "Install with: uv add 'kensa[mcp]'  (or pip install 'kensa[mcp]')\n"
        )
        sys.exit(1)

    from kensa.mcp_server import main as _main

    _main()
