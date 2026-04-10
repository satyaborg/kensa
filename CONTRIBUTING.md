# Contributing to kensa

Thanks for your interest in contributing. This guide covers setup, workflow, and conventions.

Please follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) in all project spaces.

## Setup

```bash
git clone https://github.com/satyaborg/kensa.git
cd kensa
uv sync --extra dev
pre-commit install
```

## Development workflow

1. Create a branch: `git checkout -b type/short-description`
   - Types: `feat/`, `fix/`, `chore/`
2. Make your changes
3. Run checks:

```bash
pytest                                      # tests
pytest --cov=kensa --cov-fail-under=90     # with coverage
ruff check src/ tests/                      # lint
ruff format src/ tests/                     # format
uv run ty check                                     # type check
```

4. Commit with a clear, imperative message under 72 chars
5. Open a PR against `main`

## Code conventions

- **Python 3.10+**. Line length 100.
- **Type hints everywhere**. No `Any` unless forced by a library boundary. Strict ty.
- **Double quotes** for strings (ruff default). Trailing commas always.
- **Imports**: stdlib, third-party, local: separated by blank lines. Sorted by ruff/isort.
- **File access**: use `try/except FileNotFoundError` instead of `if path.exists()` then read (TOCTOU).

## Testing

- Write tests for any function with branching logic or >10 lines.
- Prefer real objects over mocks. Mock only at system boundaries (network, disk, time).
- Test names: `test_<what>_<condition>_<expected>`.
- CI enforces 90% coverage.

## Pull requests

- Keep PRs small and focused. If a PR touches >300 lines, consider splitting it.
- One logical change per commit.
- PRs run CI automatically (lint + test).

## Lint rules

Ruff is configured with: `E`, `F`, `I`, `UP`, `B`, `SIM`, `RUF`, `PT`, `PIE`, `C4`, `RET`, `PERF`.

Pre-commit hooks run ruff and ty automatically on commit.

## Releases

Releases are maintainer-only. Run `./scripts/release-prep.sh <patch|minor|major>` from a clean `main` branch — it bumps the version, generates the changelog, and opens a PR. After merging, run `./scripts/release-tag.sh <tag>` to tag and push. GitHub Actions handles PyPI publishing via trusted OIDC.
