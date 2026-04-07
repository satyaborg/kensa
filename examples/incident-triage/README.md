# Incident Triage Agent

Multi-tool agent that triages production alerts by querying runbooks, checking service health, inspecting recent deploys, pulling metrics, and paging on-call. Handles correlated alerts and false positives.

Wrong severity = 3am page for nothing, or a missed P0 that takes down prod.

## Tools

| Tool | Purpose | Can fail? |
|------|---------|-----------|
| `query_runbook` | Search runbook by error pattern/service | No matches for unknown patterns |
| `get_service_status` | Check current service health, uptime, active incidents | Unknown service |
| `get_recent_deploys` | List recent deploys for a service | No deploys found |
| `get_metrics` | Pull specific metric (error_rate, latency, etc.) | Unknown service/metric |
| `page_oncall` | Page on-call engineer for a team | 5% delivery failure, rejects P2/P3 |

## What makes this hard

- Agent must **correlate signals**: 5xx spike + recent deploy 15 min ago → likely the deploy caused it.
- Service status shows api-gateway already **degraded** with an active incident: agent must check if the alert is a new problem or the known one.
- The `page_oncall` tool **rejects low-severity pages** (P2/P3): agent must classify correctly first.
- Metrics show current vs. baseline: agent must compare to determine if values are anomalous.
- Multiple alerts about the same root cause should be recognized as correlated, not paged separately.

## Data

12 runbook entries. 7 services with live health status. Deploy history for 4 services. Metrics with current/1h_ago/baseline for comparison. The api-gateway is pre-configured as degraded with an active incident and a recent deploy, creating a realistic investigation path.

## Eval it

```bash
cd examples/incident-triage
# then in Claude Code:
> evaluate this agent
```

Requires `OPENAI_API_KEY`. Built with LangGraph (ReAct agent) and `langchain-openai`.
