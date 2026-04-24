# AGENTS.md

This file provides guidance to Coding agents (Claude code, Codex, Cursor etc.) when working with code in this repository.

## Commands

```bash
# Install
uv sync --extra dev
pre-commit install

# Test
pytest                                                    # all tests
pytest tests/test_checks.py                               # single file
pytest tests/test_checks.py::test_output_contains_passes  # single test
pytest --cov=kensa --cov-report=term-missing --cov-fail-under=90  # with coverage (CI threshold: 90%)
pytest -m "not integration"                               # skip integration tests

# Lint & format
ruff check src/ tests/
ruff check --fix src/ tests/
ruff format src/ tests/
ruff format --check src/ tests/                           # CI format check (no changes)

# Type check
uv run ty check

# CLI
kensa init                                               # scaffold .kensa/ (bare; no example)
kensa init --example                                     # scaffold .kensa/ with a demo agent + scenario
kensa capture -- <cmd> [args...]                         # capture one real agent invocation as a trace
kensa capture -i "<input>" -- <cmd> [args...]            # capture with an explicit input string (recommended)
kensa doctor                                             # pre-flight environment checks
kensa run                                                # run all scenarios
kensa run --scenario-id <name>                           # run specific scenario
kensa judge                                              # run checks + LLM judge on latest run
kensa judge --model <model>                              # override judge model
kensa report                                             # terminal report for latest run
kensa report --format markdown                           # CI-friendly markdown
kensa report --format json                               # machine-readable
kensa report --format html                               # standalone HTML file
kensa eval                                               # run + judge + report in one shot
kensa eval -s <name>                                     # eval specific scenario
kensa generate                                           # synthesize scenarios from latest run's traces
kensa generate --run-id <id>                             # synthesize from a specific run
kensa generate --trace path/to/trace.jsonl -n 5          # synthesize N scenarios from a trace file
kensa generate --dry-run                                 # print generated YAML without writing
kensa analyze                                            # cost/latency stats + anomaly flags
kensa mcp                                                # serve kensa over MCP (stdio transport)
kensa mcp --http --port 8765                             # MCP over HTTP on localhost

# Build (both packages)
uv build                                                 # kensa sdist + wheel → dist/
cd packages/kensa-mcp && uv build                        # kensa-mcp shim sdist + wheel → packages/kensa-mcp/dist/
```

## Architecture

Kensa is the open source agent evals harness. It runs agent code in subprocesses, captures OpenTelemetry traces, evaluates results via deterministic checks + LLM-as-judge, and reports.

### Data flow

```
.kensa/scenarios/*.yaml → load scenarios → subprocess execution
  → OTel spans captured via KENSA_TRACE_DIR → JSONL trace files
  → deterministic checks → LLM judge (if criteria set)
  → Result objects → terminal / markdown / JSON / HTML report
```

### Dependency graph

```
models.py          ← foundation (pydantic only, dependency root)
paths.py           ← stdlib only (centralized .kensa/ path resolution)
pricing.py         ← models only (model price lookup, OpenRouter fetch)
trace_semantics.py ← models only (canonical tool-call dedup/ordering)
trajectory.py      ← models + trace_semantics
utils.py           ← models + trace_semantics
translate.py       ← models + pricing
checks.py          ← models + trace_semantics + utils + trajectory
report.py          ← models only
styles.py          ← models only
aggregate.py       ← models only (multi-run variance/flaky detection)
analyzer.py        ← models + runner + trace_semantics + utils
judge.py           ← models + checks + utils + paths (lazy: runner, llm)
runner.py          ← models + paths + translate
doctor.py          ← paths + utils (lazy: runner, styles)
exporter.py        ← stdlib + opentelemetry only (JSONL span exporter, no kensa imports)
scaffold.py        ← paths only (idempotent .kensa/ scaffolding, shared by CLI and MCP)
llm.py             ← stdlib only (Completer protocol + Anthropic/OpenAI adapters; shared client + provider resolution; lazy: utils, runner)
generate.py        ← models + paths (lazy: runner, utils, llm; scenario synthesis from traces)
mcp_server.py      ← models + paths (lazy: runner, judge, report, analyzer, doctor, scaffold)
_mcp_launcher.py   ← stdlib only (clean install-hint wrapper around mcp_server.main; consumed by the kensa-mcp shim package)
cli.py             ← models + paths + styles + judge (lazy: runner, report, analyzer, doctor, scaffold, mcp_server, generate)
```

No circular deps. `models.py` is imported by everything. `utils.py` is the most shared utility (checks, judge, analyzer, doctor).

### Skills

`skills/` contains five Claude Code skills that orchestrate the eval workflow. Each skill has its own `SKILL.md` with instructions and references. The skills are: `generate-scenarios`, `generate-judges`, `diagnose-errors`, `audit-evals`, and `validate-judge`. These are what run when a user says "evaluate my agent", they use the CLI commands above under the hood.

Read `skills/evals-directive.md` before creating or modifying any skill.

### MCP server

`mcp_server.py` exposes the eval workflow over the Model Context Protocol as 7 tools (`init`, `doctor`, `run`, `judge`, `eval`, `report`, `analyze`) and 8 resources under `kensa://`. Tools are thin adapters over `runner`, `judge`, `report`, `analyzer`, and `scaffold` — no business logic lives here. Failures come back as a stable `MCPError(error, code, hint)` envelope instead of raising across the protocol boundary. The base package exposes the server via the `kensa mcp` CLI subcommand (requires the `mcp` extra: `uv add kensa[mcp]`). A separate `kensa-mcp` PyPI shim at `packages/kensa-mcp/` depends on `kensa[mcp]` pinned to the same version and registers a `kensa-mcp` console script pointing at `_mcp_launcher.py` — this is what makes `uvx kensa-mcp` work. The launcher prints a clean install hint instead of a two-level import traceback when `fastmcp` is missing.

### Key design patterns

- **Registry pattern**: `CHECK_REGISTRY` in `checks.py` and `FORMATTERS` in `report.py`. Add a new check or format by registering a function; no call-site changes needed.
- **Protocol-based judges**: `JudgeProvider` protocol in `judge.py`. AnthropicJudge and OpenAIJudge are implementations. Check-fail short-circuits the LLM call to save cost.
- **`exporter.py`**: OTel JSONL span exporter. `instrument()` is idempotent and no-ops when `KENSA_TRACE_DIR` is unset.

### Subprocess isolation model

Each scenario runs in its own subprocess with `KENSA_TRACE_DIR` set. The runner injects a `sitecustomize.py` via `PYTHONPATH` that calls `instrument()` before the agent's code runs. This configures OTel, writes spans as JSONL, and auto-instruments any detected SDK (Anthropic, OpenAI, LangChain). No code changes needed in the agent. Runner reads spans post-execution and translates to kensa format. The injected directory is stripped from `PYTHONPATH` after instrumentation to prevent child subprocess re-instrumentation.

### Judge model resolution

1. `KENSA_JUDGE_MODEL` env var (explicit override)
2. `ANTHROPIC_API_KEY` present → AnthropicJudge (claude-sonnet-4-6)
3. `OPENAI_API_KEY` present → OpenAIJudge (gpt-5.4-mini)
4. Neither → error with setup instructions

### Public API

`instrument()` is the only public export (`__init__.py`). It remains available as an opt-in escape hatch for non-Python commands or environments where sitecustomize cannot run (e.g. `python -S`). Everything else is internal.

## CI

- **Pre-commit hooks**: ruff check (with `--fix --exit-non-zero-on-fix`), ruff format, and `ty check`. Commits will be rejected if any fail.
- **GitHub Actions** (`ci.yml`): test job runs `pytest -m "not integration"` with 90% coverage gate across a Python 3.10/3.11/3.12/3.13 matrix. Lint job runs ruff check, ruff format check, and ty check on Python 3.10.

## Conventions

- Python 3.10+. Line length 100. Ruff rules: E, F, I, UP, B, SIM, RUF, PT, PIE, C4, RET, PERF.
- ty strict mode (`all = "error"`). Type hints everywhere.
- Flat module structure: all modules in `src/kensa/`, no nested packages.
- models.py is the dependency root.
- Pydantic for all domain objects. Serialize with `.model_dump(mode="json")`.
- Registry pattern for extensibility (CHECK_REGISTRY, FORMATTERS).
- Protocol-based abstraction for judge providers.
- Environment-driven config (KENSA_TRACE_DIR, KENSA_JUDGE_MODEL, API keys).
- Let exceptions propagate. Catch only when you can meaningfully handle or enrich.
- TOCTOU: use `try/except FileNotFoundError` instead of `if path.exists()` then read.
- Conventional commits with three prefixes only: `feat:`, `fix:`, `chore:`. Append `!` for breaking changes (e.g. `feat!:`). No scopes. Message after prefix is imperative, lowercase, <72 chars.
- Branch names: `type/short-description` (e.g. `feat/auth-flow`, `fix/null-check`, `chore/readme-badges`). When working in a git worktree, the auto-generated `worktree-*` branch must be renamed to follow this convention before the first commit: `git branch -m worktree-<slug> type/short-description`.
