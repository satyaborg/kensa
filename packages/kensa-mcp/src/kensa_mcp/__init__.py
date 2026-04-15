"""Thin shim that re-exports the kensa MCP launcher so ``uvx kensa-mcp`` works.

The actual server lives in :mod:`kensa.mcp_server`; the launcher in
:mod:`kensa._mcp_launcher` handles the missing-``[mcp]``-extra case with a
clean install hint before importing fastmcp.
"""

from __future__ import annotations

from kensa._mcp_launcher import main

__all__ = ["main"]
