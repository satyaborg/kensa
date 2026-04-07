"""Customer support agent — classifies tickets, drafts responses, routes escalations.

Multi-tool agent that looks up customers, checks order history, searches policies,
creates tickets, and issues refunds. Tools can fail (customer not found, refund
system down, order outside window) — the agent must handle failures gracefully.
"""

from __future__ import annotations

import itertools
import json
import random
import sys

from openai import OpenAI

CUSTOMERS: dict[str, dict] = {
    "C-1001": {
        "id": "C-1001",
        "name": "Sarah Chen",
        "email": "sarah@acmecorp.com",
        "plan": "enterprise",
        "company": "Acme Corp",
        "created_at": "2023-06-15",
        "tags": ["vip", "annual-contract"],
        "account_manager": "Dana Park",
    },
    "C-1002": {
        "id": "C-1002",
        "name": "Jake Morrison",
        "email": "jake@startupxyz.io",
        "plan": "pro",
        "company": "StartupXYZ",
        "created_at": "2024-11-22",
        "tags": ["monthly"],
        "account_manager": None,
    },
    "C-1003": {
        "id": "C-1003",
        "name": "Priya Patel",
        "email": "priya@solodev.io",
        "plan": "free",
        "company": None,
        "created_at": "2025-01-10",
        "tags": [],
        "account_manager": None,
    },
    "C-1004": {
        "id": "C-1004",
        "name": "Marcus Webb",
        "email": "marcus@megacorp.com",
        "plan": "enterprise",
        "company": "MegaCorp Industries",
        "created_at": "2022-09-01",
        "tags": ["vip", "annual-contract", "custom-sla"],
        "account_manager": "Dana Park",
    },
    "C-1005": {
        "id": "C-1005",
        "name": "Li Wei",
        "email": "wei@tinyteam.co",
        "plan": "pro",
        "company": "TinyTeam",
        "created_at": "2025-02-18",
        "tags": ["annual", "trial-convert"],
        "account_manager": None,
    },
}


ORDERS: dict[str, list[dict]] = {
    "C-1001": [
        {
            "order_id": "ORD-4001",
            "amount": 4999.00,
            "status": "completed",
            "date": "2024-06-15",
            "item": "Enterprise Annual",
        },
        {
            "order_id": "ORD-4002",
            "amount": 4999.00,
            "status": "completed",
            "date": "2025-06-15",
            "item": "Enterprise Annual Renewal",
        },
    ],
    "C-1002": [
        {
            "order_id": "ORD-4003",
            "amount": 49.00,
            "status": "completed",
            "date": "2025-02-22",
            "item": "Pro Monthly",
        },
        {
            "order_id": "ORD-4004",
            "amount": 49.00,
            "status": "completed",
            "date": "2025-03-22",
            "item": "Pro Monthly",
        },
        {
            "order_id": "ORD-4005",
            "amount": 49.00,
            "status": "refunded",
            "date": "2025-04-01",
            "item": "Pro Monthly — double charge",
        },
        {
            "order_id": "ORD-4006",
            "amount": 49.00,
            "status": "completed",
            "date": "2025-04-22",
            "item": "Pro Monthly",
        },
    ],
    "C-1003": [
        {
            "order_id": "ORD-4007",
            "amount": 0.00,
            "status": "completed",
            "date": "2025-01-10",
            "item": "Free Plan",
        },
    ],
    "C-1004": [
        {
            "order_id": "ORD-4008",
            "amount": 24999.00,
            "status": "completed",
            "date": "2023-09-01",
            "item": "Enterprise Annual + Custom SLA",
        },
        {
            "order_id": "ORD-4009",
            "amount": 24999.00,
            "status": "completed",
            "date": "2024-09-01",
            "item": "Enterprise Annual Renewal",
        },
        {
            "order_id": "ORD-4010",
            "amount": 24999.00,
            "status": "pending",
            "date": "2025-09-01",
            "item": "Enterprise Annual Renewal",
        },
    ],
    "C-1005": [
        {
            "order_id": "ORD-4011",
            "amount": 468.00,
            "status": "completed",
            "date": "2025-02-18",
            "item": "Pro Annual",
        },
    ],
}


_ticket_ids = itertools.count(5001)


KB_ARTICLES: list[dict] = [
    {
        "id": "KB-001",
        "topic": "refund policy",
        "content": (
            "Refunds are available within 14 days of purchase for annual plans. "
            "Monthly plans: refund for current billing period only, within 7 days of charge. "
            "No refunds after the window. Enterprise contracts follow custom terms — "
            "escalate to account manager. Free plan: nothing to refund. "
            "Never promise a refund without checking the plan type AND order date."
        ),
    },
    {
        "id": "KB-002",
        "topic": "password reset",
        "content": (
            "Users can reset via /forgot-password. If email not received: check spam, "
            "verify email address on file. If locked out after 5 attempts, account is "
            "frozen for 30 minutes. Support can manually unlock via admin panel after "
            "verifying identity (last 4 of card + account email)."
        ),
    },
    {
        "id": "KB-003",
        "topic": "plan upgrade downgrade",
        "content": (
            "Upgrades take effect immediately. Prorated billing for remainder of "
            "current period. Downgrade takes effect at next billing cycle. Feature "
            "access changes instantly on upgrade, removed at period end on downgrade. "
            "Enterprise downgrades require account manager approval."
        ),
    },
    {
        "id": "KB-004",
        "topic": "data export gdpr",
        "content": (
            "All plans include data export via Settings > Export. Formats: CSV, JSON. "
            "Exports are async — large datasets may take up to 1 hour. Enterprise "
            "plans have API access for programmatic export. GDPR data requests must "
            "be completed within 30 days — escalate to compliance team immediately."
        ),
    },
    {
        "id": "KB-005",
        "topic": "api rate limits throttling",
        "content": (
            "Free: 100 req/min. Pro: 1000 req/min. Enterprise: custom (check contract). "
            "Rate limit headers: X-RateLimit-Remaining, X-RateLimit-Reset. 429 responses "
            "include Retry-After header. Burst allowance: 2x limit for 10 seconds. "
            "Temporary increases require engineering approval — create a ticket."
        ),
    },
    {
        "id": "KB-006",
        "topic": "billing dispute charge",
        "content": (
            "If customer disputes a charge: FIRST look up the customer and their order history. "
            "If duplicate charge, refund immediately and apologize. If legitimate charge "
            "customer forgot about, explain with order details. Never admit fault for "
            "a charge that is correct. Escalate to billing team if amount > $500."
        ),
    },
    {
        "id": "KB-007",
        "topic": "outage incident status",
        "content": (
            "During active outages: acknowledge the issue, point to status.example.com, "
            "do NOT speculate on root cause or give ETAs unless engineering has provided one. "
            "After resolution: share post-mortem link within 48 hours. "
            "If customer reports an outage we haven't acknowledged, create an urgent ticket."
        ),
    },
    {
        "id": "KB-008",
        "topic": "account deletion cancellation",
        "content": (
            "Account deletion is permanent and irreversible. 30-day grace period after "
            "request — data is soft-deleted and can be recovered. After 30 days, data is "
            "purged. Requires email confirmation. Active subscriptions must be cancelled "
            "first. Enterprise accounts: require account manager sign-off."
        ),
    },
    {
        "id": "KB-009",
        "topic": "sso saml setup integration",
        "content": (
            "SSO available on Enterprise plans only. Supported: SAML 2.0, OIDC. "
            "Setup requires: metadata XML or discovery URL, admin console access. "
            "Typical setup time: 15-30 minutes. Escalate to integrations team if "
            "custom IdP or SCIM provisioning needed."
        ),
    },
    {
        "id": "KB-010",
        "topic": "escalation policy",
        "content": (
            "Escalate to tier 2 if: customer waiting > 24h, issue requires backend access, "
            "customer is enterprise, customer explicitly asks for manager. "
            "Escalate to legal if: customer threatens lawsuit, GDPR/CCPA request, "
            "data breach suspected. Never tell customer 'there is nothing I can do.' "
            "VIP customers (check tags): always acknowledge their status."
        ),
    },
    {
        "id": "KB-011",
        "topic": "free plan limitations",
        "content": (
            "Free plan: 1 user, 100 API calls/min, no SSO, no priority support, "
            "5 GB storage, community forum only. No SLA. Cannot create tickets — "
            "redirect to community forum or suggest upgrade to Pro."
        ),
    },
    {
        "id": "KB-012",
        "topic": "enterprise custom terms sla",
        "content": (
            "Enterprise customers may have custom SLAs. Always check customer tags "
            "for 'custom-sla' before quoting standard terms. Custom SLA customers: "
            "response time 1h (vs 24h standard), dedicated Slack channel, quarterly "
            "business reviews. Refunds and credits follow contract terms, not standard policy."
        ),
    },
]


def search_knowledge_base(query: str) -> str:
    """Search KB articles by keyword match."""
    query_lower = query.lower()
    matches = [
        article
        for article in KB_ARTICLES
        if query_lower in article["topic"].lower() or query_lower in article["content"].lower()
    ]
    if not matches:
        return json.dumps({"matches": [], "note": "No KB article found. Consider escalating."})
    return json.dumps({"matches": matches})


def lookup_customer(identifier: str) -> str:
    """Look up a customer by email or customer ID."""
    identifier = identifier.strip().lower()
    # Try direct ID match
    for cid, cust in CUSTOMERS.items():
        if identifier == cid.lower():
            return json.dumps({"customer": cust})
    # Try email match
    for cust in CUSTOMERS.values():
        if identifier == cust["email"].lower():
            return json.dumps({"customer": cust})
    # Try name match (partial)
    for cust in CUSTOMERS.values():
        if identifier in cust["name"].lower():
            return json.dumps({"customer": cust})
    return json.dumps(
        {
            "error": "customer_not_found",
            "message": f"No customer found for '{identifier}'. Ask the customer to verify their email or account ID.",
        }
    )


def get_order_history(customer_id: str) -> str:
    """Get order history for a customer."""
    orders = ORDERS.get(customer_id)
    if orders is None:
        return json.dumps(
            {"error": "customer_not_found", "message": f"No customer with ID '{customer_id}'."}
        )
    return json.dumps({"customer_id": customer_id, "orders": orders, "total_orders": len(orders)})


def create_ticket(customer_id: str, subject: str, priority: str, notes: str) -> str:
    """Create an internal support ticket."""
    if priority not in ("urgent", "high", "normal", "low"):
        return json.dumps(
            {
                "error": "invalid_priority",
                "message": f"Priority must be urgent/high/normal/low, got '{priority}'.",
            }
        )
    if customer_id not in CUSTOMERS:
        return json.dumps(
            {
                "error": "customer_not_found",
                "message": f"Cannot create ticket: customer '{customer_id}' not found.",
            }
        )
    # Check free plan restriction
    if CUSTOMERS[customer_id]["plan"] == "free":
        return json.dumps(
            {
                "error": "plan_restriction",
                "message": "Free plan customers cannot have support tickets. Redirect to community forum.",
            }
        )
    ticket_id = f"TKT-{next(_ticket_ids)}"
    return json.dumps(
        {
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "subject": subject,
            "priority": priority,
            "status": "open",
        }
    )


def issue_refund(order_id: str, reason: str) -> str:
    """Issue a refund for a specific order."""
    # Simulate intermittent failure (refund system down)
    if random.random() < 0.15:
        return json.dumps(
            {
                "error": "service_unavailable",
                "message": "Refund service is temporarily unavailable. Try again or create a ticket for the billing team.",
            }
        )
    # Find the order
    for orders in ORDERS.values():
        for order in orders:
            if order["order_id"] == order_id:
                if order["status"] == "refunded":
                    return json.dumps(
                        {
                            "error": "already_refunded",
                            "message": f"Order {order_id} was already refunded.",
                        }
                    )
                if order["amount"] == 0.0:
                    return json.dumps(
                        {"error": "zero_amount", "message": "Cannot refund a $0 order."}
                    )
                if order["status"] == "pending":
                    return json.dumps(
                        {
                            "error": "pending_order",
                            "message": "Cannot refund a pending order. Cancel it instead.",
                        }
                    )
                order["status"] = "refunded"
                return json.dumps(
                    {
                        "refund_id": f"REF-{random.randint(7000, 7999)}",
                        "order_id": order_id,
                        "amount": order["amount"],
                        "status": "refund_initiated",
                        "message": "Refund will appear in 3-5 business days.",
                    }
                )
    return json.dumps(
        {"error": "order_not_found", "message": f"No order found with ID '{order_id}'."}
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "Search the support knowledge base by topic or keyword. Returns matching "
                "articles with policies, procedures, and response guidelines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term (topic, keyword, or customer issue)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": (
                "Look up a customer by email address, customer ID, or name. Returns "
                "customer profile including plan, company, tags, and account manager."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "identifier": {
                        "type": "string",
                        "description": "Customer email, ID (e.g. C-1001), or name",
                    },
                },
                "required": ["identifier"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_history",
            "description": (
                "Get the full order history for a customer. Requires customer ID "
                "(use lookup_customer first to find the ID)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Customer ID (e.g. C-1001)",
                    },
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": (
                "Create an internal support ticket for follow-up or escalation. "
                "Free plan customers cannot have tickets — redirect to community forum."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string", "description": "Customer ID"},
                    "subject": {"type": "string", "description": "Ticket subject line"},
                    "priority": {
                        "type": "string",
                        "enum": ["urgent", "high", "normal", "low"],
                        "description": "Ticket priority",
                    },
                    "notes": {"type": "string", "description": "Internal notes for the next agent"},
                },
                "required": ["customer_id", "subject", "priority", "notes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_refund",
            "description": (
                "Issue a refund for a specific order. Requires the order ID (use "
                "get_order_history first). May fail if refund service is down."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Order ID (e.g. ORD-4001)"},
                    "reason": {"type": "string", "description": "Reason for the refund"},
                },
                "required": ["order_id", "reason"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "search_knowledge_base": lambda args: search_knowledge_base(args["query"]),
    "lookup_customer": lambda args: lookup_customer(args["identifier"]),
    "get_order_history": lambda args: get_order_history(args["customer_id"]),
    "create_ticket": lambda args: create_ticket(
        args["customer_id"], args["subject"], args["priority"], args["notes"]
    ),
    "issue_refund": lambda args: issue_refund(args["order_id"], args["reason"]),
}

SYSTEM_PROMPT = """\
You are a customer support agent for a SaaS company. You handle inbound tickets.

WORKFLOW — follow this order:
1. Look up the customer (by email, name, or ID from the ticket).
2. Search the knowledge base for relevant policies.
3. Check order history if the issue involves billing, refunds, or charges.
4. Take action: draft a response, create a ticket, issue a refund, or escalate.

RULES:
- ALWAYS look up the customer before responding. You need their plan and tags to apply the right policy.
- ALWAYS search the KB before promising anything. Policies change.
- For refunds: check the order date against the refund window. Never promise a refund outside policy.
- Enterprise customers with 'custom-sla' tag: follow contract terms, not standard policy.
- VIP customers: acknowledge their status. Route to account manager if they have one.
- Free plan customers: cannot create tickets. Redirect to community forum or suggest upgrade.
- If a tool fails (service unavailable, not found), handle it gracefully. Inform the customer and create a follow-up ticket if needed.
- Never tell a customer "there is nothing I can do." Always offer an alternative.
- If the customer is angry, acknowledge their frustration before solving.

Output format:
- Classification: billing / technical / account / feature-request / escalation
- Priority: urgent / high / normal / low
- Action: respond / escalate / escalate + respond / refund + respond
- Response: <draft customer response>
- Internal note: <context for next agent if escalating>
"""


def run(ticket: str) -> str:
    """Run the support agent on a ticket."""
    client = OpenAI()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"New support ticket:\n\n{ticket}"},
    ]

    for _ in range(10):
        response = client.chat.completions.create(
            model="gpt-5.4-mini",
            max_completion_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )

        choice = response.choices[0]

        if choice.finish_reason == "stop":
            return choice.message.content or ""

        if choice.finish_reason == "tool_calls":
            messages.append(
                {
                    "role": "assistant",
                    "content": choice.message.content,
                    "tool_calls": choice.message.tool_calls,
                }
            )
            for tool_call in choice.message.tool_calls:
                fn_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                handler = TOOL_DISPATCH.get(fn_name)
                if handler:
                    result = handler(args)
                else:
                    result = json.dumps(
                        {"error": "unknown_tool", "message": f"No tool named '{fn_name}'."}
                    )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

    return "Error: agent exceeded maximum iterations."


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent.py '<support ticket>'")
        sys.exit(1)
    print(run(sys.argv[1]))
