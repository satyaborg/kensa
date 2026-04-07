"""SQL analyst agent — answers natural language questions about a SaaS business database.

Multi-tool agent with a realistic schema: 8 tables, nullable columns, soft deletes,
ambiguous column names, and enough data for joins, aggregations, and status filtering
to go subtly wrong. The kind of agent where wrong answers cost real money.
"""

from __future__ import annotations

import json
import sqlite3
import sys

from openai import OpenAI

DB = sqlite3.connect(":memory:")
DB.execute("PRAGMA foreign_keys = ON")
DB.executescript("""
-- Core tables
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    plan TEXT NOT NULL CHECK(plan IN ('free', 'starter', 'pro', 'enterprise')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'churned', 'suspended')),
    company TEXT,
    mrr REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    deleted_at TEXT  -- soft delete
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL CHECK(status IN ('completed', 'refunded', 'pending', 'failed', 'cancelled')),
    created_at TEXT NOT NULL,
    deleted_at TEXT  -- soft delete
);

CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_id INTEGER REFERENCES orders(id),
    amount REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('paid', 'overdue', 'void', 'draft')),
    due_date TEXT NOT NULL,
    paid_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE subscriptions (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    plan TEXT NOT NULL CHECK(plan IN ('free', 'starter', 'pro', 'enterprise')),
    status TEXT NOT NULL CHECK(status IN ('active', 'cancelled', 'past_due', 'trialing')),
    interval TEXT NOT NULL CHECK(interval IN ('monthly', 'annual')),
    mrr REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    cancelled_at TEXT,
    trial_ends_at TEXT
);

CREATE TABLE usage_events (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    event_type TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    recorded_at TEXT NOT NULL
);

CREATE TABLE support_tickets (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    subject TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open', 'pending', 'resolved', 'closed')),
    priority TEXT NOT NULL CHECK(priority IN ('low', 'normal', 'high', 'urgent')),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE team_members (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner', 'admin', 'member', 'viewer')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'invited', 'deactivated')),
    invited_at TEXT NOT NULL,
    joined_at TEXT
);

CREATE TABLE feature_flags (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    feature TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    enabled_at TEXT
);

-- Seed customers (10 with variety — including churned and soft-deleted)
INSERT INTO customers (id, name, email, plan, status, company, mrr, created_at, deleted_at) VALUES
    (1,  'Sarah Chen',     'sarah@acmecorp.com',      'enterprise', 'active',    'Acme Corp',           4999.00, '2023-06-15', NULL),
    (2,  'Jake Morrison',  'jake@startupxyz.io',      'pro',        'active',    'StartupXYZ',          149.00,  '2024-03-22', NULL),
    (3,  'Priya Patel',    'priya@solodev.io',        'free',       'active',    NULL,                  0.00,    '2024-06-01', NULL),
    (4,  'Marcus Webb',    'marcus@megacorp.com',      'enterprise', 'active',    'MegaCorp Industries', 12500.00,'2023-02-10', NULL),
    (5,  'Li Wei',         'wei@tinyteam.co',          'starter',    'active',    'TinyTeam',            49.00,   '2024-07-18', NULL),
    (6,  'Alex Ruiz',      'alex@growthco.com',        'pro',        'churned',   'GrowthCo',           0.00,    '2024-01-05', NULL),
    (7,  'Nina Okoro',     'nina@fastship.dev',        'pro',        'active',    'FastShip',            149.00,  '2024-09-12', NULL),
    (8,  'Tom Baker',      'tom@oldclient.com',        'starter',    'active',    'OldClient Inc',       49.00,   '2023-11-20', '2025-02-01'),
    (9,  'Yuki Tanaka',    'yuki@devhub.jp',           'enterprise', 'suspended', 'DevHub Japan',        0.00,    '2024-04-01', NULL),
    (10, 'Elena Volkov',   'elena@scaleup.eu',         'pro',        'active',    'ScaleUp EU',          149.00,  '2024-11-01', NULL);

-- Seed orders (30+ with variety — including failed, cancelled, multi-currency, soft-deleted)
INSERT INTO orders (id, customer_id, amount, currency, status, created_at, deleted_at) VALUES
    (1,  1, 4999.00,  'USD', 'completed',  '2024-01-15', NULL),
    (2,  1, 4999.00,  'USD', 'completed',  '2024-07-15', NULL),
    (3,  1, 4999.00,  'USD', 'completed',  '2025-01-15', NULL),
    (4,  2, 149.00,   'USD', 'completed',  '2024-08-22', NULL),
    (5,  2, 149.00,   'USD', 'refunded',   '2024-09-22', NULL),
    (6,  2, 149.00,   'USD', 'completed',  '2024-10-22', NULL),
    (7,  2, 149.00,   'USD', 'completed',  '2024-11-22', NULL),
    (8,  2, 149.00,   'USD', 'completed',  '2024-12-22', NULL),
    (9,  3, 0.00,     'USD', 'completed',  '2024-06-01', NULL),
    (10, 4, 12500.00, 'USD', 'completed',  '2024-02-10', NULL),
    (11, 4, 12500.00, 'USD', 'completed',  '2024-08-10', NULL),
    (12, 4, 12500.00, 'USD', 'pending',    '2025-02-10', NULL),
    (13, 5, 49.00,    'USD', 'completed',  '2024-07-18', NULL),
    (14, 5, 49.00,    'USD', 'completed',  '2024-08-18', NULL),
    (15, 5, 49.00,    'USD', 'failed',     '2024-09-18', NULL),
    (16, 5, 49.00,    'USD', 'completed',  '2024-09-19', NULL),
    (17, 6, 149.00,   'USD', 'completed',  '2024-01-05', NULL),
    (18, 6, 149.00,   'USD', 'completed',  '2024-02-05', NULL),
    (19, 6, 149.00,   'USD', 'refunded',   '2024-03-05', NULL),
    (20, 6, 149.00,   'USD', 'cancelled',  '2024-04-05', NULL),
    (21, 7, 149.00,   'USD', 'completed',  '2024-10-12', NULL),
    (22, 7, 149.00,   'USD', 'completed',  '2024-11-12', NULL),
    (23, 7, 149.00,   'USD', 'completed',  '2024-12-12', NULL),
    (24, 7, 149.00,   'USD', 'completed',  '2025-01-12', NULL),
    (25, 8, 49.00,    'USD', 'completed',  '2024-01-20', '2025-02-01'),
    (26, 8, 49.00,    'USD', 'completed',  '2024-02-20', '2025-02-01'),
    (27, 9, 8500.00,  'USD', 'completed',  '2024-04-01', NULL),
    (28, 9, 8500.00,  'USD', 'completed',  '2024-10-01', NULL),
    (29, 10, 149.00,  'EUR', 'completed',  '2024-12-01', NULL),
    (30, 10, 149.00,  'EUR', 'completed',  '2025-01-01', NULL),
    (31, 10, 149.00,  'EUR', 'completed',  '2025-02-01', NULL),
    (32, 10, 149.00,  'EUR', 'pending',    '2025-03-01', NULL);

-- Seed invoices
INSERT INTO invoices (id, customer_id, order_id, amount, status, due_date, paid_at, created_at) VALUES
    (1,  1, 1,    4999.00,  'paid',    '2024-02-15', '2024-01-18', '2024-01-15'),
    (2,  1, 2,    4999.00,  'paid',    '2024-08-15', '2024-07-20', '2024-07-15'),
    (3,  1, 3,    4999.00,  'paid',    '2025-02-15', '2025-01-20', '2025-01-15'),
    (4,  4, 10,   12500.00, 'paid',    '2024-03-10', '2024-02-15', '2024-02-10'),
    (5,  4, 11,   12500.00, 'paid',    '2024-09-10', '2024-08-12', '2024-08-10'),
    (6,  4, 12,   12500.00, 'overdue', '2025-03-10', NULL,         '2025-02-10'),
    (7,  9, 27,   8500.00,  'paid',    '2024-05-01', '2024-04-05', '2024-04-01'),
    (8,  9, 28,   8500.00,  'void',    '2024-11-01', NULL,         '2024-10-01'),
    (9,  10, 29,  149.00,   'paid',    '2024-12-15', '2024-12-03', '2024-12-01'),
    (10, 10, 32,  149.00,   'draft',   '2025-03-15', NULL,         '2025-03-01');

-- Seed subscriptions
INSERT INTO subscriptions (id, customer_id, plan, status, interval, mrr, started_at, cancelled_at, trial_ends_at) VALUES
    (1,  1,  'enterprise', 'active',    'annual',  4999.00,  '2023-06-15', NULL,          NULL),
    (2,  2,  'pro',        'active',    'monthly', 149.00,   '2024-03-22', NULL,          NULL),
    (3,  3,  'free',       'active',    'monthly', 0.00,     '2024-06-01', NULL,          NULL),
    (4,  4,  'enterprise', 'active',    'annual',  12500.00, '2023-02-10', NULL,          NULL),
    (5,  5,  'starter',    'active',    'monthly', 49.00,    '2024-07-18', NULL,          NULL),
    (6,  6,  'pro',        'cancelled', 'monthly', 0.00,     '2024-01-05', '2024-04-05', NULL),
    (7,  7,  'pro',        'active',    'monthly', 149.00,   '2024-09-12', NULL,          NULL),
    (8,  9,  'enterprise', 'past_due',  'annual',  0.00,     '2024-04-01', NULL,          NULL),
    (9,  10, 'pro',        'active',    'monthly', 149.00,   '2024-11-01', NULL,          NULL),
    (10, 10, 'enterprise', 'trialing',  'annual',  0.00,     '2025-03-01', NULL,          '2025-04-01');

-- Seed usage events
INSERT INTO usage_events (id, customer_id, event_type, quantity, recorded_at) VALUES
    (1,  1, 'api_call',     15000, '2025-03-01'),
    (2,  1, 'api_call',     18200, '2025-03-15'),
    (3,  1, 'api_call',     16800, '2025-04-01'),
    (4,  2, 'api_call',     3200,  '2025-03-01'),
    (5,  2, 'api_call',     4100,  '2025-04-01'),
    (6,  3, 'api_call',     95,    '2025-03-01'),
    (7,  3, 'api_call',     102,   '2025-04-01'),
    (8,  4, 'api_call',     45000, '2025-03-01'),
    (9,  4, 'data_export',  12,    '2025-03-15'),
    (10, 7, 'api_call',     8900,  '2025-03-01'),
    (11, 7, 'api_call',     9200,  '2025-04-01'),
    (12, 10,'api_call',     5500,  '2025-03-01');

-- Seed support tickets
INSERT INTO support_tickets (id, customer_id, subject, status, priority, created_at, resolved_at) VALUES
    (1, 1, 'SSO configuration help',         'resolved', 'normal',  '2025-01-10', '2025-01-11'),
    (2, 2, 'Billing discrepancy September',  'resolved', 'high',    '2024-09-25', '2024-09-26'),
    (3, 4, 'Custom report request',          'open',     'normal',  '2025-03-20', NULL),
    (4, 4, 'Invoice not received',           'pending',  'high',    '2025-03-25', NULL),
    (5, 7, 'API rate limit increase',        'resolved', 'normal',  '2025-02-15', '2025-02-17'),
    (6, 9, 'Account suspension inquiry',     'open',     'urgent',  '2025-03-28', NULL),
    (7, 5, 'Payment failed twice',           'resolved', 'high',    '2024-09-18', '2024-09-19'),
    (8, 10,'Trial extension request',        'open',     'normal',  '2025-03-30', NULL);

-- Seed team members
INSERT INTO team_members (id, customer_id, email, role, status, invited_at, joined_at) VALUES
    (1,  1, 'sarah@acmecorp.com',   'owner',  'active',      '2023-06-15', '2023-06-15'),
    (2,  1, 'bob@acmecorp.com',     'admin',  'active',      '2023-06-20', '2023-06-21'),
    (3,  1, 'alice@acmecorp.com',   'member', 'active',      '2023-07-01', '2023-07-02'),
    (4,  1, 'carol@acmecorp.com',   'member', 'invited',     '2025-03-01', NULL),
    (5,  2, 'jake@startupxyz.io',   'owner',  'active',      '2024-03-22', '2024-03-22'),
    (6,  4, 'marcus@megacorp.com',  'owner',  'active',      '2023-02-10', '2023-02-10'),
    (7,  4, 'eng1@megacorp.com',    'admin',  'active',      '2023-02-15', '2023-02-16'),
    (8,  4, 'eng2@megacorp.com',    'member', 'active',      '2023-03-01', '2023-03-02'),
    (9,  4, 'eng3@megacorp.com',    'member', 'active',      '2023-03-01', '2023-03-05'),
    (10, 4, 'eng4@megacorp.com',    'viewer', 'deactivated', '2023-04-01', '2023-04-02'),
    (11, 7, 'nina@fastship.dev',    'owner',  'active',      '2024-09-12', '2024-09-12'),
    (12, 7, 'dev@fastship.dev',     'member', 'active',      '2024-09-15', '2024-09-16'),
    (13, 10,'elena@scaleup.eu',     'owner',  'active',      '2024-11-01', '2024-11-01');

-- Seed feature flags
INSERT INTO feature_flags (id, customer_id, feature, enabled, enabled_at) VALUES
    (1, 1, 'beta_v2_api',     1, '2025-01-15'),
    (2, 1, 'advanced_export', 1, '2024-08-01'),
    (3, 4, 'beta_v2_api',     1, '2025-02-01'),
    (4, 4, 'custom_reports',  1, '2024-06-01'),
    (5, 4, 'advanced_export', 1, '2024-03-01'),
    (6, 7, 'beta_v2_api',     0, NULL),
    (7, 10,'beta_v2_api',     1, '2025-03-01');
""")


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": (
                "Get the database schema — table names, columns, types, and constraints. "
                "Call this first if you're unsure about the table structure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Optional: specific table name. Omit for all tables.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": (
                "Execute a read-only SQL query. Only SELECT statements allowed. "
                "Tables: customers, orders, invoices, subscriptions, usage_events, "
                "support_tickets, team_members, feature_flags. "
                "IMPORTANT: Many tables have 'status' and 'deleted_at' columns — "
                "filter appropriately to avoid counting deleted/inactive records."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A SELECT SQL query",
                    },
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_query",
            "description": (
                "Run EXPLAIN QUERY PLAN on a SQL query to understand how it will execute. "
                "Use this to debug slow or unexpected queries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "The SQL query to explain",
                    },
                },
                "required": ["sql"],
            },
        },
    },
]

SYSTEM_PROMPT = """\
You are a SQL analyst for a SaaS company. You answer questions about customers, \
orders, revenue, usage, and support by querying the database.

RULES:
1. ALWAYS call get_schema first if you're unsure about the table structure.
2. ALWAYS query the database — never guess or use assumptions.
3. Only use SELECT statements. Never modify data.
4. WATCH OUT for soft deletes: many tables have a 'deleted_at' column. Unless the \
   question asks about deleted records, filter with WHERE deleted_at IS NULL.
5. WATCH OUT for status columns: 'status' appears in customers, orders, invoices, \
   subscriptions, support_tickets, and team_members. Each has different valid values. \
   'completed' orders ≠ 'paid' invoices ≠ 'active' subscriptions.
6. Revenue questions: only count 'completed' orders. Exclude refunded, pending, \
   failed, and cancelled. Unless specifically asked about those.
7. MRR (Monthly Recurring Revenue): use the subscriptions table, not orders. \
   Only count 'active' subscriptions.
8. Multi-currency: some orders are in EUR. Don't mix currencies without noting it.
9. Round dollar amounts to 2 decimal places.
10. If the data doesn't answer the question, say so. Don't fabricate numbers.
11. Show your SQL query and explain the results clearly.
"""


def get_schema(table_name: str | None = None) -> str:
    """Get database schema."""
    if table_name:
        cursor = DB.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        row = cursor.fetchone()
        if row:
            return json.dumps({"table": table_name, "schema": row[0]})
        return json.dumps({"error": f"Table '{table_name}' not found."})

    cursor = DB.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [{"name": row[0], "schema": row[1]} for row in cursor.fetchall()]
    return json.dumps({"tables": tables})


def _validate_select(sql: str) -> str | None:
    """Strip and validate a SQL string is a SELECT. Returns cleaned SQL or None."""
    stripped = sql.strip().rstrip(";").strip()
    return stripped if stripped.upper().startswith("SELECT") else None


def execute_query(sql: str) -> str:
    """Execute a SQL query, returning results or an error message."""
    sql_stripped = _validate_select(sql)
    if sql_stripped is None:
        return json.dumps({"error": "Only SELECT queries are allowed."})
    try:
        cursor = DB.execute(sql_stripped)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        if not rows:
            return json.dumps({"columns": columns, "rows": [], "note": "Query returned 0 rows."})
        return json.dumps(
            {"columns": columns, "rows": [list(r) for r in rows], "row_count": len(rows)}
        )
    except sqlite3.Error as e:
        return json.dumps({"error": f"SQL error: {e}"})


def explain_query(sql: str) -> str:
    """Run EXPLAIN QUERY PLAN on a SQL query."""
    sql_stripped = _validate_select(sql)
    if sql_stripped is None:
        return json.dumps({"error": "Can only explain SELECT queries."})
    try:
        cursor = DB.execute(f"EXPLAIN QUERY PLAN {sql_stripped}")
        plan = [list(row) for row in cursor.fetchall()]
        return json.dumps({"query": sql_stripped, "plan": plan})
    except sqlite3.Error as e:
        return json.dumps({"error": f"SQL error: {e}"})


TOOL_DISPATCH = {
    "get_schema": lambda args: get_schema(args.get("table_name")),
    "query_database": lambda args: execute_query(args.get("sql", "")),
    "explain_query": lambda args: explain_query(args.get("sql", "")),
}


def run(question: str) -> str:
    """Run the agent loop: send question, handle tool calls, return final answer."""
    client = OpenAI()
    messages: list[dict[str, object]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    for _ in range(10):
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=messages,
            tools=TOOLS,
            max_completion_tokens=1024,
        )

        choice = response.choices[0]

        if choice.finish_reason != "tool_calls":
            return choice.message.content or ""

        messages.append(choice.message)  # type: ignore[arg-type]

        for tool_call in choice.message.tool_calls or []:
            fn_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            handler = TOOL_DISPATCH.get(fn_name)
            if handler:
                result = handler(args)
            else:
                result = json.dumps({"error": f"Unknown tool '{fn_name}'."})
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
        print("Usage: python agent.py '<question>'")
        sys.exit(1)
    print(run(sys.argv[1]))
