<div align="center">

<br>
<img src="https://raw.githubusercontent.com/satyaborg/kensa/main/assets/banner.png" alt="kensa - the open source agent evals harness" width="800">
<br><br>

<p>Kensa is the open source harness for evaluating agents.</p>

<p>
<a href="https://github.com/satyaborg/kensa/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/satyaborg/kensa/ci.yml?label=CI&style=flat-square" alt="CI"></a>
<a href="https://pypi.org/project/kensa/"><img src="https://img.shields.io/pypi/v/kensa?style=flat-square" alt="PyPI"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fsatyaborg%2Fkensa%2Fmain%2Fpyproject.toml&style=flat-square" alt="Python"></a>
<a href="LICENSE"><img src="https://img.shields.io/github/license/satyaborg/kensa?style=flat-square" alt="License"></a>
<a href="https://pepy.tech/projects/kensa"><img src="https://img.shields.io/pepy/dt/kensa?style=flat-square" alt="Downloads"></a>
</p>

</div>

---

Agents are non-deterministic. Prompts drift. Tools change. Models behave differently.
Any change can make them slower, more expensive, or just plain unreliable.

`kensa` gives coding agents, like Claude Code, a repeatable loop to eval your
agents, and catch regressions every time you make a change.

## Installation

### Paste this into your coding agent

Open your coding agent and paste:

```text
Install Kensa for agent-driven evals with `uvx kensa init --cli --agent all`,
then evaluate this agent using Kensa's skills. Start with audit-evals, let it
route to the right next step, and follow the eval lifecycle: generate scenarios,
calibrate judges if needed, run `kensa eval`, diagnose failures, and recommend
whether to fix the agent, the scenarios, or the judge.
```

Your agent does the setup, writes or updates evals, runs them, and reports what
to fix.

### Or run it yourself

```bash
uvx kensa init
```

Adds `kensa` to your dev deps, scaffolds `.kensa/`, and adds 5 skills for the
complete evals workflow. Works with Claude Code, Codex, Cursor, and other coding
agents. For non-interactive setup or CI: `uvx kensa init --cli --agent all`.

## Quickstart

Tell your coding agent what you want:

| You say | Kensa does |
| --- | --- |
| "Evaluate this agent" | Audit setup, create or reuse scenarios, and run evals. |
| "Why are evals failing?" | Inspect results and traces, then diagnose the root cause. |
| "Add coverage for tool use" | Write scenario YAML with tool or trajectory checks. |
| "The judge seems wrong" | Create or validate structured judge prompts. |

## How it works

- **Zero to eval:** your coding agent drafts scenarios; you review them.
- **Runs become traces:** each scenario runs in a subprocess with LLM calls, tool
  use, tokens, cost, and latency captured.
- **Checks gate judges:** deterministic checks run before any LLM judge call.
- **Ship with evidence:** reports show verdicts, traces, cost, latency, and failure details.

## Instrumentation

Zero code changes. kensa captures LLM calls, tool use, tokens, cost, and latency
without modifying your agent. OpenTelemetry (OTel) compatible.

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
| `kensa init` | Scaffold `.kensa/`, add kensa as a dev dep, install skills (use `--example` for a demo agent) |
| `kensa doctor` | Check instrumentation, config, and environment readiness |
| `kensa capture -- <cmd>` | Run your agent once with tracing, then feed it into `kensa generate` |
| `kensa generate` | Synthesize scenario YAMLs from captured traces via an LLM |
| `kensa eval` | Run + judge + report in one command |
| `kensa report` | Show the latest results in terminal, Markdown, JSON, or HTML |

See the [CLI docs](https://kensa.sh/docs/cli) for `run`, `judge`, `analyze`,
`mcp`, `skills install`, and the full command reference.

## MCP server

One-liner for Claude Code (run from your project root):

```bash
claude mcp add kensa -- uvx kensa-mcp
```

For other JSON-based MCP clients, add to your project's `.mcp.json` or
`.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "kensa": {
      "command": "uvx",
      "args": ["kensa-mcp"]
    }
  }
}
```

For Codex, add to your project-scoped `.codex/config.toml`:

```toml
[mcp_servers.kensa]
command = "uvx"
args = ["kensa-mcp"]
```

See the [MCP server docs](https://kensa.sh/docs/mcp-server) for tools,
resources, and manual config.

## Manual workflow

If you want to author evals yourself:

```bash
kensa init                    # scaffolds a bare .kensa/ — pass --example for a demo
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
See the [scenario docs](https://kensa.sh/docs/scenarios) and
[checks docs](https://kensa.sh/docs/checks) for the full field and check
reference.

## CI

```yaml
- name: Run evals
  run: uv run kensa eval --format markdown
```

If you only use deterministic checks, you do not need API keys. If you use
`criteria` or `judge`, add judge LLM provider secrets in CI.

## Need more?

- [Docs](https://kensa.sh/docs)
- [`examples/`](examples/) has sample agents and scenarios
- [`CONTRIBUTING.md`](CONTRIBUTING.md) covers local development
- [Homepage](https://kensa.sh)

## License

- [MIT License](LICENSE)
