# Code Review Agent

Multi-tool agent that reviews diffs by looking up security/bug rules, fetching surrounding file context, and checking test coverage. Handles false-positive-prone patterns and makes evidence-based BLOCK/APPROVE decisions.

A missed vuln ships a CVE. A false positive wastes review cycles and erodes trust.

## Tools

| Tool | Purpose |
|------|---------|
| `lookup_rule` | Search rules by category (injection, auth, crypto, concurrency, etc.) |
| `get_file_context` | Get surrounding code for a file to understand what the diff changes |
| `check_test_coverage` | Check coverage %, uncovered lines, test count for a file |

## What makes this hard

- **False positive traps**: `src/api/search.py` uses an f-string in SQL: but the value comes from a hardcoded whitelist, so it's safe. Rule SEC-001 has a `false_positive_note` the agent should read.
- **Context matters**: a new route without `@require_auth` might be a bug (SEC-004) or might be a public health check. The agent needs `get_file_context` to tell the difference.
- **Untested code**: `src/workers/export.py` has 0% coverage and multiple issues (blocking I/O in async). The agent should flag the coverage gap alongside the bugs.
- **16 rules** across 8 categories with severity levels and false-positive guidance.
- The agent must distinguish what CHANGED in the diff from pre-existing code.

## Data

16 rules across injection, secrets, auth, crypto, concurrency, error-handling, data, and performance. 5 files with full source context and test coverage data. Coverage ranges from 0% (untested worker) to 91% (billing). Rules include `false_positive_note` fields that the agent must consult before flagging.

## Eval it

```bash
cd examples/code-reviewer
# then in Claude Code:
> evaluate this agent
```

Requires `ANTHROPIC_API_KEY`.
