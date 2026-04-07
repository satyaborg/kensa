"""Incident triage agent — classifies severity, routes alerts, recommends remediation.

Multi-tool agent that queries runbooks, checks service health, inspects recent deploys,
pulls metrics, and pages on-call. Handles correlated alerts, false positives, and
compound reasoning (e.g., 5xx spike + recent deploy → rollback).

Built with LangGraph (ReAct agent pattern) and OpenAI as the LLM.
"""

from __future__ import annotations

import json
import random
import sys

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

RUNBOOK: list[dict] = [
    {
        "id": "RB-001",
        "pattern": "OOMKilled",
        "service": "api-gateway",
        "severity": "P1",
        "route": "platform-team",
        "action": "Restart pod, check for memory leak in recent deploys. Page if > 3 restarts in 1h.",
    },
    {
        "id": "RB-002",
        "pattern": "connection refused",
        "service": "postgres-primary",
        "severity": "P0",
        "route": "database-team",
        "action": "Check primary health. If replica lag > 30s, initiate failover. Always page.",
    },
    {
        "id": "RB-003",
        "pattern": "certificate expired",
        "service": "*",
        "severity": "P1",
        "route": "security-team",
        "action": "Rotate certificate immediately. Check cert-manager logs for renewal failure.",
    },
    {
        "id": "RB-004",
        "pattern": "rate limit exceeded",
        "service": "billing-service",
        "severity": "P2",
        "route": "backend-team",
        "action": "Check for retry storms. Increase rate limit if legitimate traffic spike.",
    },
    {
        "id": "RB-005",
        "pattern": "disk usage > 90%",
        "service": "*",
        "severity": "P1",
        "route": "platform-team",
        "action": "Identify largest consumers. Clear old logs. Expand volume if persistent.",
    },
    {
        "id": "RB-006",
        "pattern": "5xx spike",
        "service": "api-gateway",
        "severity": "P0",
        "route": "backend-team",
        "action": "Check recent deploys first. If deploy in last 30m, rollback immediately. If no recent deploy, check downstream dependencies.",
    },
    {
        "id": "RB-007",
        "pattern": "flaky test",
        "service": "ci-pipeline",
        "severity": "P3",
        "route": "dev-experience",
        "action": "Quarantine test. Do NOT page. Create ticket for owning team.",
    },
    {
        "id": "RB-008",
        "pattern": "payment processing timeout",
        "service": "billing-service",
        "severity": "P0",
        "route": "payments-team",
        "action": "Check Stripe status page first. If Stripe healthy, check billing-service health. Always page.",
    },
    {
        "id": "RB-009",
        "pattern": "unauthorized access",
        "service": "auth-service",
        "severity": "P0",
        "route": "security-team",
        "action": "Check for credential stuffing. If > 100 failed attempts from one IP, block and page.",
    },
    {
        "id": "RB-010",
        "pattern": "cron job failed",
        "service": "data-pipeline",
        "severity": "P2",
        "route": "data-team",
        "action": "Check if idempotent (safe to retry). If daily aggregation, re-run. Ticket if recurring.",
    },
    {
        "id": "RB-011",
        "pattern": "high latency",
        "service": "api-gateway",
        "severity": "P1",
        "route": "backend-team",
        "action": "Check p99 latency. If > 5s, investigate slow queries or downstream timeouts. Page if customer-facing SLA breach.",
    },
    {
        "id": "RB-012",
        "pattern": "replica lag",
        "service": "postgres-replica",
        "severity": "P1",
        "route": "database-team",
        "action": "If lag > 30s AND reads are served from replica, this is customer-impacting. Promote replica or reroute reads.",
    },
]


SERVICE_STATUS: dict[str, dict] = {
    "api-gateway": {
        "status": "degraded",
        "uptime_24h": "97.2%",
        "error_rate": "4.8%",
        "active_incidents": ["INC-2341: elevated 5xx rate since 14:32 UTC"],
        "last_deploy": "2025-04-04T14:15:00Z",
    },
    "postgres-primary": {
        "status": "healthy",
        "uptime_24h": "100%",
        "connections": "142/300",
        "replica_lag_seconds": 2.1,
        "active_incidents": [],
        "last_deploy": None,
    },
    "postgres-replica": {
        "status": "healthy",
        "uptime_24h": "100%",
        "replica_lag_seconds": 2.1,
        "active_incidents": [],
        "last_deploy": None,
    },
    "billing-service": {
        "status": "healthy",
        "uptime_24h": "99.95%",
        "error_rate": "0.02%",
        "active_incidents": [],
        "last_deploy": "2025-04-03T09:00:00Z",
    },
    "auth-service": {
        "status": "healthy",
        "uptime_24h": "99.99%",
        "error_rate": "0.01%",
        "failed_auth_attempts_1h": 23,
        "active_incidents": [],
        "last_deploy": "2025-04-02T16:00:00Z",
    },
    "data-pipeline": {
        "status": "healthy",
        "uptime_24h": "99.8%",
        "last_successful_run": "2025-04-04T06:00:00Z",
        "active_incidents": [],
        "last_deploy": "2025-04-01T11:00:00Z",
    },
    "ci-pipeline": {
        "status": "healthy",
        "uptime_24h": "99.9%",
        "active_incidents": [],
        "last_deploy": None,
    },
}


DEPLOY_HISTORY: dict[str, list[dict]] = {
    "api-gateway": [
        {
            "deploy_id": "DEP-891",
            "timestamp": "2025-04-04T14:15:00Z",
            "author": "jchen",
            "change": "feat: add new /v2/search endpoint",
            "status": "live",
        },
        {
            "deploy_id": "DEP-890",
            "timestamp": "2025-04-04T10:30:00Z",
            "author": "asmith",
            "change": "fix: correct pagination offset",
            "status": "live",
        },
        {
            "deploy_id": "DEP-889",
            "timestamp": "2025-04-03T16:00:00Z",
            "author": "mwebb",
            "change": "chore: bump dependencies",
            "status": "live",
        },
    ],
    "billing-service": [
        {
            "deploy_id": "DEP-887",
            "timestamp": "2025-04-03T09:00:00Z",
            "author": "lpatel",
            "change": "feat: add invoice PDF generation",
            "status": "live",
        },
    ],
    "auth-service": [
        {
            "deploy_id": "DEP-885",
            "timestamp": "2025-04-02T16:00:00Z",
            "author": "jchen",
            "change": "fix: rate limit on login endpoint",
            "status": "live",
        },
    ],
    "data-pipeline": [
        {
            "deploy_id": "DEP-883",
            "timestamp": "2025-04-01T11:00:00Z",
            "author": "dpark",
            "change": "feat: add customer cohort aggregation",
            "status": "live",
        },
    ],
}


METRICS_DATA: dict[str, dict[str, dict]] = {
    "api-gateway": {
        "error_rate": {"current": 4.8, "1h_ago": 0.3, "baseline": 0.2, "unit": "%"},
        "p99_latency": {"current": 3200, "1h_ago": 450, "baseline": 400, "unit": "ms"},
        "requests_per_second": {"current": 1250, "1h_ago": 1180, "baseline": 1200, "unit": "rps"},
        "pod_restarts_1h": {"current": 0, "1h_ago": 0, "baseline": 0, "unit": "count"},
    },
    "postgres-primary": {
        "connections": {"current": 142, "1h_ago": 138, "baseline": 130, "unit": "count"},
        "query_time_p99": {"current": 45, "1h_ago": 42, "baseline": 40, "unit": "ms"},
        "disk_usage": {"current": 72, "1h_ago": 72, "baseline": 70, "unit": "%"},
        "replica_lag": {"current": 2.1, "1h_ago": 1.8, "baseline": 1.5, "unit": "seconds"},
    },
    "billing-service": {
        "error_rate": {"current": 0.02, "1h_ago": 0.01, "baseline": 0.01, "unit": "%"},
        "p99_latency": {"current": 180, "1h_ago": 175, "baseline": 170, "unit": "ms"},
        "payment_success_rate": {"current": 99.8, "1h_ago": 99.9, "baseline": 99.9, "unit": "%"},
    },
    "auth-service": {
        "error_rate": {"current": 0.01, "1h_ago": 0.01, "baseline": 0.01, "unit": "%"},
        "failed_auth_attempts": {"current": 23, "1h_ago": 18, "baseline": 15, "unit": "count/h"},
        "p99_latency": {"current": 95, "1h_ago": 90, "baseline": 85, "unit": "ms"},
    },
}


@tool
def query_runbook(keyword: str) -> str:
    """Search the incident runbook by keyword. Returns matching entries with
    severity, routing, and remediation steps. Use this to look up known
    patterns before making triage decisions."""
    keyword_lower = keyword.lower()
    matches = [
        entry
        for entry in RUNBOOK
        if keyword_lower in entry["pattern"].lower()
        or keyword_lower in entry["service"].lower()
        or keyword_lower in entry["action"].lower()
    ]
    if not matches:
        return json.dumps(
            {
                "matches": [],
                "note": "No runbook entry found. Use your judgment but escalate if unsure.",
            }
        )
    return json.dumps({"matches": matches})


@tool
def get_service_status(service_name: str) -> str:
    """Get the current health status of a service. Returns uptime, error rate,
    active incidents, and last deploy time. Use this to check if a service is
    already in a known-degraded state before paging."""
    service = SERVICE_STATUS.get(service_name)
    if service is None:
        # Fuzzy match
        for name, data in SERVICE_STATUS.items():
            if service_name.lower() in name.lower():
                return json.dumps({"service": name, **data})
        return json.dumps(
            {
                "error": "service_not_found",
                "message": f"Unknown service '{service_name}'. Known: {list(SERVICE_STATUS.keys())}",
            }
        )
    return json.dumps({"service": service_name, **service})


@tool
def get_recent_deploys(service_name: str) -> str:
    """Get recent deploys for a service. Critical for diagnosing whether a
    deploy caused the issue. Returns deploy ID, author, change description,
    and timestamp."""
    deploys = DEPLOY_HISTORY.get(service_name, [])
    if not deploys:
        return json.dumps(
            {"service": service_name, "deploys": [], "note": "No recent deploys found."}
        )
    return json.dumps({"service": service_name, "deploys": deploys, "total": len(deploys)})


@tool
def get_metrics(service_name: str, metric_name: str) -> str:
    """Get a specific metric for a service. Returns current value, value from
    1 hour ago, and baseline. Use to quantify the severity of an issue.
    Available metrics vary by service — try: error_rate, p99_latency,
    connections, replica_lag, disk_usage, payment_success_rate."""
    service_metrics = METRICS_DATA.get(service_name)
    if service_metrics is None:
        return json.dumps(
            {"error": "service_not_found", "message": f"No metrics for '{service_name}'."}
        )
    metric = service_metrics.get(metric_name)
    if metric is None:
        available = list(service_metrics.keys())
        return json.dumps(
            {
                "error": "metric_not_found",
                "message": f"No metric '{metric_name}' for {service_name}. Available: {available}",
            }
        )
    return json.dumps({"service": service_name, "metric": metric_name, **metric})


@tool
def page_oncall(team: str, severity: str, summary: str) -> str:
    """Page the on-call engineer for a team. Use only for P0/P1. Do NOT page
    for P2/P3 — create a ticket instead. Returns confirmation or rejection."""
    if severity not in ("P0", "P1"):
        return json.dumps(
            {
                "error": "severity_too_low",
                "message": f"Pages are for P0/P1 only. Severity '{severity}' should use a ticket, not a page.",
            }
        )
    # Simulate page delivery failure (rare)
    if random.random() < 0.05:
        return json.dumps(
            {
                "error": "page_failed",
                "message": f"Failed to reach on-call for {team}. Retry or escalate to incident commander.",
            }
        )
    return json.dumps(
        {
            "status": "page_sent",
            "team": team,
            "severity": severity,
            "oncall": f"{team}-oncall@company.pagerduty.com",
            "message": f"Paged {team} on-call. Expect acknowledgment within 5 minutes.",
        }
    )


SYSTEM_PROMPT = """\
You are an incident triage agent for an SRE team. You receive alerts and must \
classify, route, and recommend actions.

WORKFLOW — follow this order:
1. Query the runbook for the error pattern/service.
2. Check the service status — is it already in a known-degraded state?
3. Check recent deploys — could a deploy have caused this?
4. Pull specific metrics to quantify severity (error rate, latency, etc.).
5. Based on ALL the above, decide severity and action.

SEVERITY GUIDE:
- P0: Customer-facing outage or data loss risk. PAGE immediately.
- P1: Degraded performance, needs fix within 1h. PAGE.
- P2: Non-urgent degradation. TICKET (async).
- P3: Cosmetic, flaky tests, non-impacting. TICKET only.

RULES:
- Check for correlated signals: 5xx spike + recent deploy → likely the deploy.
- Check for false positives: if the service is healthy and metrics are normal, \
  the alert may be transient. Say so.
- Never page for P2/P3. Never downplay database or payment outages.
- If multiple alerts fire together, recognize they may be the same root cause.
- When in doubt, escalate — a false page is better than a missed outage.

Output format:
- Severity: P0/P1/P2/P3
- Route: <team>
- Action: PAGE / TICKET / MONITOR (with explanation)
- Root cause hypothesis: <what you think happened>
- Steps: <specific remediation from runbook + your analysis>
- Correlated signals: <any related issues you found>
"""

llm = ChatOpenAI(model="gpt-5.4-mini", max_tokens=1024)
agent = create_react_agent(
    llm,
    [query_runbook, get_service_status, get_recent_deploys, get_metrics, page_oncall],
    prompt=SYSTEM_PROMPT,
)


def run(alert: str) -> str:
    """Run the triage agent on an alert."""
    result = agent.invoke({"messages": [{"role": "user", "content": alert}]})
    return result["messages"][-1].content


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent.py '<alert description>'")
        sys.exit(1)
    print(run(sys.argv[1]))
