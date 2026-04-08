<div align="center">

<br>
<img src="assets/banner.png" alt="kensa - the open source agent evals harness" width="800">
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
| `kensa analyze` | Flag slow, expensive, flaky, or error-prone traces |

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
  - type: output_matches
    params: { pattern: "^P[123]$" }

criteria: |
  P1 is for outages or data loss affecting multiple users.
```

For complete examples, see [`examples/`](examples/).

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
