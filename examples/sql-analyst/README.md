# SQL Analyst Agent

Multi-tool agent that answers natural language questions about a SaaS business by querying an 8-table SQLite database. Schema includes soft deletes, ambiguous column names, multi-currency, and enough traps for joins and aggregations to go subtly wrong.

## Tools

| Tool | Purpose |
|------|---------|
| `get_schema` | Inspect table structure, columns, constraints |
| `query_database` | Execute read-only SQL queries |
| `explain_query` | Run EXPLAIN QUERY PLAN for debugging |

## What makes this hard

- **Soft deletes**: `deleted_at` columns on customers and orders. Forgetting to filter = overcounting.
- **Ambiguous `status` columns**: 6 tables have `status` but with different valid values (`completed` vs `paid` vs `active`). Easy to confuse.
- **Multi-currency**: some orders are EUR, some USD. Mixing them silently produces wrong revenue numbers.
- **MRR trap**: MRR should come from `subscriptions.mrr` (active only), not by summing orders.
- **Churned customers**: counting churned customers as active inflates metrics.
- **Pending/failed orders**: including them in revenue is wrong. Including refunded orders is also wrong.
- **8 tables**: `customers`, `orders`, `invoices`, `subscriptions`, `usage_events`, `support_tickets`, `team_members`, `feature_flags`: realistic joins are required.

## Data

10 customers (active, churned, suspended, soft-deleted). 32 orders (completed, refunded, pending, failed, cancelled, multi-currency). Invoices with overdue/void/draft states. Subscriptions with trials and past_due. Usage events, support tickets, team members, and feature flags for cross-table queries.

## Eval it

```bash
cd examples/sql-analyst
# then in Claude Code:
> evaluate this agent
```

Requires `OPENAI_API_KEY`.
