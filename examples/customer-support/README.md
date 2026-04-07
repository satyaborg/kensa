# Customer Support Agent

Multi-tool agent that classifies tickets, drafts responses, and takes actions (refunds, escalations, ticket creation). Looks up customers, checks order history, and searches policies before responding.

Misrouted ticket = customer bounced between teams for days. Wrong promise = liability.

## Tools

| Tool | Purpose | Can fail? |
|------|---------|-----------|
| `lookup_customer` | Find customer by email/ID/name | Customer not found |
| `get_order_history` | Pull billing and order records | Customer not found |
| `search_knowledge_base` | Search policy articles by keyword | No matches |
| `create_ticket` | Create internal support ticket | Free plan restriction, invalid priority |
| `issue_refund` | Refund a specific order | Service down (15%), already refunded, pending order, zero amount |

## What makes this hard

- Agent must **chain tools**: look up customer → check plan/tags → search policy → check orders → decide action.
- Refund tool has a **15% random failure rate** (simulates payment system flakiness). Agent must handle gracefully.
- Free plan customers **cannot create tickets**: agent must redirect to community forum.
- Enterprise customers with `custom-sla` tag follow different rules than standard policy.
- VIP customers expect their status acknowledged and routing to their account manager.

## Data

5 customers across free/pro/enterprise plans. 12 KB articles with real constraints (14-day refund window, GDPR deadlines, free plan limitations, custom SLA terms). Order history includes completed, refunded, and pending orders.

## Eval it

```bash
cd examples/customer-support
# then in Claude Code:
> evaluate this agent
```

Requires `OPENAI_API_KEY`.
