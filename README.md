<div align="center">

<br>
<img src="https://raw.githubusercontent.com/satyaborg/kensa/main/assets/banner.png" alt="kensa - the open source agent evals harness" width="800">
<br><br>

<p>Tell your coding agent to evaluate an agent. Get a working eval suite in minutes.</p>

<p>
<a href="https://github.com/satyaborg/kensa/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/satyaborg/kensa/ci.yml?label=CI" alt="CI"></a>
<a href="https://pypi.org/project/kensa/"><img src="https://img.shields.io/pypi/v/kensa" alt="PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fsatyaborg%2Fkensa%2Fmain%2Fpyproject.toml" alt="Python"></a>
<a href="LICENSE"><img src="https://img.shields.io/github/license/satyaborg/kensa" alt="License"></a>
</p>

</div>

---

`kensa` is an open source eval harness for agent codebases. It gives coding agents an opinionated CLI and bundled skills to generate scenarios, run them in subprocesses, judge results, and report failures.

## Installation

### Skills + CLI (recommended)

```bash
npx skills add satyaborg/kensa
uv add kensa
```

Works for Claude Code, Codex, Cursor, OpenCode, Gemini CLI, and similar coding agents.

### Claude Code plugin

If you primarily use Claude Code, you can install it as a plugin:

```text
/plugin marketplace add satyaborg/kensa
/plugin install kensa
```

## Quickstart

Tell your coding agent:

```text
evaluate this agent
```

That gives you the basic loop:

- your coding agent inspects the repo, sets up instrumentation and writes evals
- it runs `kensa` to execute scenarios and capture traces
- deterministic checks run first
- the LLM judge only runs when those pass
- reports show what failed and why
- you review changes, approve fixes and iterate

## If instrumentation is missing

Add `instrument()` before importing your LLM SDK:

```python
from kensa import instrument

instrument()
```

If you use the bundled skills, your coding agent will usually add this for you.

<details>
<summary>Provider extras</summary>

```bash
uv add "kensa[anthropic]"
uv add "kensa[openai]"
uv add "kensa[langchain]"
uv add "kensa[all]"
```

</details>

## Core commands

| Command | What it does |
| --- | --- |
| `kensa init --blank` | Scaffold `.kensa/` without example content |
| `kensa doctor` | Check instrumentation, config, and environment readiness |
| `kensa eval` | Run + judge + report in one command |
| `kensa report` | Show the latest results in terminal, Markdown, JSON, or HTML |
| `kensa analyze` | Flag slow, expensive, anomalous, or error-prone traces |
| `kensa mcp` | Serve kensa over MCP for LLM clients (stdio or HTTP) |

## MCP server

Kensa ships an MCP server that exposes the eval workflow to any MCP-aware
client — Claude Code, Cursor, Codex, OpenCode, Gemini CLI, Claude Desktop,
anything that speaks MCP.

```bash
uv add "kensa[mcp]"
kensa-mcp                       # stdio transport (default)
kensa-mcp --http --port 8765    # streamable HTTP, localhost-only
kensa mcp                       # alias for kensa-mcp
```

**Tools (7):** `init`, `doctor`, `run`, `judge`, `eval`, `report`, `analyze`.

**Resources (8):** read-only data under the `kensa://` namespace.

```
kensa://runs                          # list of recent runs
kensa://runs/{id}                     # manifest + summary for one run
kensa://runs/{id}/results             # full judged results
kensa://runs/{id}/trace/{scenario}    # spans for one scenario execution
kensa://scenarios                     # list of scenarios
kensa://scenarios/{id}                # full scenario YAML
kensa://judges                        # list of judge prompt names
kensa://judges/{name}                 # judge prompt spec
```

Long-running tools (`run`, `judge`, `eval`) return a compact summary plus
a `results_uri` — fetch detail via the resource only when you need it.
Errors come back as a typed `MCPError` envelope (`{error, code, hint}`) with
stable `code` values so clients can branch on failure type.

<details>
<summary>Example Claude Code config</summary>

Add to `~/.claude.json` (or your project's `.mcp.json`):

```json
{
  "mcpServers": {
    "kensa": {
      "command": "kensa-mcp",
      "cwd": "/absolute/path/to/your/project"
    }
  }
}
```

</details>

## Manual workflow

If you want to author evals yourself:

```bash
kensa init --blank
kensa doctor
```

Scenarios live in `.kensa/scenarios/*.yaml` and point at your agent entrypoint with `run_command`.

```yaml
id: classify_ticket
input: "Our entire team can't log in. SSO has returned 502 since 7am."
run_command: [python, agent.py]   # input is appended as the final argv element

checks:
  - type: trajectory
    params:
      steps:
        - tool: classify_ticket
      max_steps: 1
      max_tokens: 2000
  - type: output_matches
    params: { pattern: "^P[123]$" }

criteria: |
  P1 is for outages or data loss affecting multiple users.
```

For complete examples, see [`examples/`](examples/).

`trajectory` is the deterministic path check for tool-call correctness. V1 supports:

- `ordering: exact | any_order`
- `args: exact | ignore`
- `min_accuracy`
- inline budgets: `max_steps`, `max_tokens`, `max_duration_seconds`

When present, reports surface `trajectory_accuracy` and `step_efficiency` alongside pass/fail.

When you run the same scenario multiple times, aggregate reports also surface estimated 3-run
and 5-run pass rates assuming independent runs.

If you need custom deterministic assertions beyond the built-ins, add a Python check via
`CHECK_REGISTRY` rather than embedding logic in scenario YAML.

## CI

```yaml
- name: Run evals
  run: uv run kensa eval --format markdown
```

If you only use deterministic checks, you do not need API keys. If you use `criteria` or `judge`, add judge provider secrets in CI.

## Need more?

- [Docs](https://kensa.sh/docs)
- [`examples/`](examples/) has sample agents and scenarios
- [`CONTRIBUTING.md`](CONTRIBUTING.md) covers local development
- [Homepage](https://kensa.sh)
- [Issues](https://github.com/satyaborg/kensa/issues)
- [MIT License](LICENSE)
