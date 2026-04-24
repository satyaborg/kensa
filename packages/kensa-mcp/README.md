# kensa-mcp

MCP (Model Context Protocol) server for [kensa](https://github.com/satyaborg/kensa), the open source agent evals harness.

This is a thin shim around `kensa[mcp]` so you can run the server with a single command:

```bash
uvx kensa-mcp
```

Or register it with Claude Code in one line:

```bash
claude mcp add kensa -- uvx kensa-mcp
```

The server exposes kensa's eval workflow (`init`, `doctor`, `run`, `judge`, `eval`, `report`, `analyze`) as MCP tools, and run artefacts as resources under `kensa://`. See the [MCP server docs](https://kensa.sh/docs/mcp-server) for the full reference.

## Relationship to kensa

`kensa-mcp` depends on `kensa[mcp]` pinned to the same version. Installing `kensa-mcp` pulls in all of kensa. If you already have kensa in your project, you can also run the server via `kensa mcp` (the CLI subcommand) without installing this package.

## License

MIT.
