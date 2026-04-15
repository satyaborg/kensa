"""Install-hint wrapper around ``kensa.mcp_server.main``."""

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
