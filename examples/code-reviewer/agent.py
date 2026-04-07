"""Code review agent — flags security vulnerabilities, bugs, and performance issues.

Multi-tool agent that looks up rules, fetches surrounding file context, and checks
test coverage before making BLOCK/APPROVE decisions. Handles tricky diffs: renames
that look like deletes, refactors across files, and patterns that look vulnerable
but aren't.

A missed vuln ships a CVE. A false positive wastes engineering time and
erodes trust in the tool until people stop reading its output.
"""

from __future__ import annotations

import json
import sys

import anthropic

RULES: list[dict] = [
    {
        "id": "SEC-001",
        "category": "injection",
        "name": "SQL injection",
        "severity": "critical",
        "pattern": "String concatenation or f-string in SQL query with user input",
        "fix": "Use parameterized queries (? placeholders)",
        "example_bad": 'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")',
        "example_good": 'cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))',
        "false_positive_note": "f-strings for table/column names from hardcoded constants are safe. Only flag when user input flows into the query string.",
    },
    {
        "id": "SEC-002",
        "category": "secrets",
        "name": "Hardcoded secret",
        "severity": "critical",
        "pattern": "API keys, passwords, or tokens as string literals in source code",
        "fix": "Use environment variables or a secret manager",
        "example_bad": 'api_key = "sk-proj-abc123..."',
        "example_good": 'api_key = os.environ["API_KEY"]',
        "false_positive_note": "Test fixtures with fake keys (e.g. 'test-key-xxx') are fine. Only flag if it looks like a real credential.",
    },
    {
        "id": "SEC-003",
        "category": "injection",
        "name": "Command injection",
        "severity": "critical",
        "pattern": "os.system() or subprocess with shell=True using unsanitized input",
        "fix": "Use subprocess with a list of args, never shell=True with user input",
        "example_bad": 'os.system(f"ping {hostname}")',
        "example_good": 'subprocess.run(["ping", hostname], check=True)',
    },
    {
        "id": "SEC-004",
        "category": "auth",
        "name": "Missing authentication check",
        "severity": "high",
        "pattern": "Route handler without auth decorator or middleware",
        "fix": "Add @require_auth or verify session before processing",
        "false_positive_note": "Public endpoints (health checks, login, signup, webhooks with signature verification) don't need auth.",
    },
    {
        "id": "SEC-005",
        "category": "auth",
        "name": "Broken access control",
        "severity": "critical",
        "pattern": "Accessing resource by ID without checking ownership/permissions",
        "fix": "Verify requesting user has permission to access the specific resource",
        "example_bad": "order = Order.get(order_id)  # no ownership check",
        "example_good": "order = Order.get(order_id, user_id=current_user.id)",
    },
    {
        "id": "SEC-006",
        "category": "crypto",
        "name": "Weak hash function",
        "severity": "high",
        "pattern": "MD5 or SHA1 used for passwords or security-sensitive hashing",
        "fix": "Use bcrypt, scrypt, or argon2 for passwords. SHA-256+ for integrity checks.",
        "false_positive_note": "MD5/SHA1 for non-security checksums (cache keys, file dedup) is acceptable.",
    },
    {
        "id": "SEC-007",
        "category": "injection",
        "name": "Cross-site scripting (XSS)",
        "severity": "high",
        "pattern": "Rendering user input in HTML without escaping",
        "fix": "Use framework auto-escaping or explicit sanitization (e.g., bleach, DOMPurify)",
    },
    {
        "id": "BUG-001",
        "category": "concurrency",
        "name": "Race condition (TOCTOU)",
        "severity": "high",
        "pattern": "Check-then-act without locking — time-of-check vs time-of-use",
        "fix": "Use atomic operations or acquire a lock around check+act",
        "example_bad": "if not path.exists():\n    path.write_text(data)",
        "example_good": "Use try/except FileExistsError or file locking",
    },
    {
        "id": "BUG-002",
        "category": "error-handling",
        "name": "Swallowed exception",
        "severity": "medium",
        "pattern": "Bare except: pass or catch Exception without logging",
        "fix": "Log the exception or re-raise. Never silently swallow.",
        "example_bad": "try:\n    do_thing()\nexcept:\n    pass",
    },
    {
        "id": "BUG-003",
        "category": "error-handling",
        "name": "Unhandled None / optional",
        "severity": "medium",
        "pattern": "Accessing attributes on a value that could be None without checking",
        "fix": "Add a None check, use optional chaining, or guard clause",
    },
    {
        "id": "BUG-004",
        "category": "data",
        "name": "Floating-point currency",
        "severity": "high",
        "pattern": "Using float for monetary calculations",
        "fix": "Use Decimal or integer cents",
        "example_bad": "total = price * 1.08  # tax calculation with float",
        "example_good": "total = Decimal(price) * Decimal('1.08')",
    },
    {
        "id": "BUG-005",
        "category": "data",
        "name": "Timezone-naive datetime",
        "severity": "medium",
        "pattern": "datetime.now() or datetime.utcnow() without timezone info",
        "fix": "Use datetime.now(timezone.utc) or a timezone-aware library",
    },
    {
        "id": "BUG-006",
        "category": "concurrency",
        "name": "Shared mutable state without synchronization",
        "severity": "high",
        "pattern": "Global dict/list modified by multiple threads or async tasks",
        "fix": "Use threading.Lock, asyncio.Lock, or thread-safe data structures",
    },
    {
        "id": "PERF-001",
        "category": "performance",
        "name": "N+1 query",
        "severity": "medium",
        "pattern": "Database query inside a loop",
        "fix": "Batch query outside the loop, index results by key",
    },
    {
        "id": "PERF-002",
        "category": "performance",
        "name": "Unbounded query",
        "severity": "high",
        "pattern": "SELECT * without LIMIT on user-facing endpoint",
        "fix": "Add LIMIT/OFFSET or cursor-based pagination",
    },
    {
        "id": "PERF-003",
        "category": "performance",
        "name": "Blocking I/O in async context",
        "severity": "high",
        "pattern": "Synchronous file/network I/O inside an async function",
        "fix": "Use async equivalents (aiofiles, httpx.AsyncClient) or run_in_executor",
    },
]


FILE_CONTEXT: dict[str, dict] = {
    "src/api/routes.py": {
        "content": [
            "from flask import Flask, request, jsonify",
            "from src.auth import require_auth, require_admin",
            "from src.models import User, Order, Product",
            "from src.db import get_db",
            "",
            "app = Flask(__name__)",
            "",
            "@app.route('/health')",
            "def health():",
            "    return jsonify({'status': 'ok'})",
            "",
            "@app.route('/api/users/<int:user_id>')",
            "@require_auth",
            "def get_user(user_id):",
            "    user = User.query.get(user_id)",
            "    return jsonify(user.to_dict())",
            "",
            "@app.route('/api/orders', methods=['POST'])",
            "@require_auth",
            "def create_order():",
            "    data = request.get_json()",
            "    order = Order.create(**data)",
            "    return jsonify(order.to_dict()), 201",
        ],
        "language": "python",
        "test_file": "tests/test_routes.py",
    },
    "src/api/search.py": {
        "content": [
            "from src.db import get_db",
            "from src.auth import require_auth",
            "",
            "ALLOWED_SORT_COLUMNS = {'name', 'created_at', 'updated_at', 'price'}",
            "",
            "@require_auth",
            "def search(query, sort_by='name', limit=50):",
            "    if sort_by not in ALLOWED_SORT_COLUMNS:",
            "        sort_by = 'name'",
            "    db = get_db()",
            "    # sort_by is from a whitelist, so f-string is safe here",
            "    results = db.execute(",
            "        f'SELECT * FROM products ORDER BY {sort_by} LIMIT ?',",
            "        (limit,)",
            "    )",
            "    return results.fetchall()",
        ],
        "language": "python",
        "test_file": "tests/test_search.py",
    },
    "src/billing/calculator.py": {
        "content": [
            "from decimal import Decimal",
            "from src.models import Subscription, Invoice",
            "",
            "TAX_RATE = Decimal('0.08')",
            "",
            "def calculate_invoice(subscription_id: int) -> Invoice:",
            "    sub = Subscription.query.get(subscription_id)",
            "    base = Decimal(str(sub.price))",
            "    tax = base * TAX_RATE",
            "    total = base + tax",
            "    return Invoice(subscription_id=subscription_id, amount=total)",
        ],
        "language": "python",
        "test_file": "tests/test_billing.py",
    },
    "src/auth/middleware.py": {
        "content": [
            "import hashlib",
            "import hmac",
            "from functools import wraps",
            "from flask import request, abort",
            "",
            "def require_auth(f):",
            "    @wraps(f)",
            "    def decorated(*args, **kwargs):",
            "        token = request.headers.get('Authorization')",
            "        if not token or not verify_token(token):",
            "            abort(401)",
            "        return f(*args, **kwargs)",
            "    return decorated",
            "",
            "def verify_webhook_signature(payload, signature, secret):",
            "    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()",
            "    return hmac.compare_digest(expected, signature)",
        ],
        "language": "python",
        "test_file": "tests/test_auth.py",
    },
    "src/workers/export.py": {
        "content": [
            "import asyncio",
            "import csv",
            "from pathlib import Path",
            "from src.db import get_db",
            "",
            "async def export_data(customer_id: int, format: str = 'csv'):",
            "    db = get_db()  # synchronous DB call",
            "    rows = db.execute('SELECT * FROM records WHERE customer_id = ?', (customer_id,))",
            "    data = rows.fetchall()",
            "    path = Path(f'/tmp/export_{customer_id}.{format}')",
            "    with open(path, 'w') as f:  # synchronous file I/O",
            "        writer = csv.writer(f)",
            "        writer.writerows(data)",
            "    return str(path)",
        ],
        "language": "python",
        "test_file": None,
    },
}


TEST_COVERAGE: dict[str, dict] = {
    "src/api/routes.py": {
        "covered_lines": 18,
        "total_lines": 22,
        "coverage_pct": 81.8,
        "uncovered_lines": [19, 20, 21, 22],
        "test_file": "tests/test_routes.py",
        "test_count": 8,
        "last_run": "2025-04-04T10:30:00Z",
        "status": "passing",
    },
    "src/api/search.py": {
        "covered_lines": 14,
        "total_lines": 16,
        "coverage_pct": 87.5,
        "uncovered_lines": [8, 9],
        "test_file": "tests/test_search.py",
        "test_count": 5,
        "last_run": "2025-04-04T10:30:00Z",
        "status": "passing",
    },
    "src/billing/calculator.py": {
        "covered_lines": 10,
        "total_lines": 11,
        "coverage_pct": 90.9,
        "uncovered_lines": [11],
        "test_file": "tests/test_billing.py",
        "test_count": 6,
        "last_run": "2025-04-04T10:30:00Z",
        "status": "passing",
    },
    "src/auth/middleware.py": {
        "covered_lines": 12,
        "total_lines": 16,
        "coverage_pct": 75.0,
        "uncovered_lines": [14, 15, 16, 17],
        "test_file": "tests/test_auth.py",
        "test_count": 4,
        "last_run": "2025-04-04T10:30:00Z",
        "status": "passing",
    },
    "src/workers/export.py": {
        "covered_lines": 0,
        "total_lines": 13,
        "coverage_pct": 0.0,
        "uncovered_lines": list(range(1, 14)),
        "test_file": None,
        "test_count": 0,
        "last_run": None,
        "status": "no_tests",
    },
}


TOOLS = [
    {
        "name": "lookup_rule",
        "description": (
            "Look up code review rules by category. Categories: injection, secrets, "
            "auth, crypto, concurrency, error-handling, data, performance. Returns rules "
            "with severity, patterns, fixes, and false-positive notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Rule category to look up",
                },
            },
            "required": ["category"],
        },
    },
    {
        "name": "get_file_context",
        "description": (
            "Get the surrounding code context for a file. Returns the full file "
            "content, language, and associated test file. Use this to understand "
            "what the diff is changing relative to existing code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file (e.g., 'src/api/routes.py')",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "check_test_coverage",
        "description": (
            "Check test coverage for a file. Returns coverage percentage, uncovered "
            "lines, test count, and whether tests are passing. Use to assess if "
            "changed code has adequate test coverage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the source file",
                },
            },
            "required": ["file_path"],
        },
    },
]

SYSTEM_PROMPT = """\
You are a code review agent. You receive diffs and flag security vulnerabilities, \
bugs, and performance issues.

WORKFLOW:
1. Look up relevant rules for the categories you see in the diff.
2. Get file context to understand what the diff changes relative to existing code.
3. Check test coverage to assess risk.
4. Flag issues with evidence. Avoid false positives.

RULES:
- For each issue found, cite the rule ID, severity, the exact line(s), and the fix.
- Distinguish CRITICAL (must fix before merge) from MEDIUM/LOW (non-blocking suggestions).
- READ THE FALSE POSITIVE NOTES on rules. Don't flag safe patterns.
  - f-strings with hardcoded/whitelisted values in SQL are safe.
  - Test fixtures with fake credentials are safe.
  - Public endpoints (health, login, webhooks with sig verification) don't need @require_auth.
  - MD5/SHA1 for non-security purposes (cache keys, dedup) is acceptable.
- If the code is clean, say so. Don't invent issues to appear thorough.
- Focus on what CHANGED in the diff, not pre-existing code.
- Check if changed lines have test coverage. Flag untested critical paths.

Output format per issue:
- [RULE-ID] severity: description
  Line: <the problematic code>
  Fix: <specific recommendation>
  Coverage: <whether this code path is tested>

End with: BLOCK (has critical issues) or APPROVE (safe to merge). \
Include non-blocking suggestions separately.
"""


def lookup_rule(category: str) -> str:
    """Look up rules by category."""
    category_lower = category.lower()
    matches = [r for r in RULES if category_lower in r["category"].lower()]
    if not matches:
        # Fuzzy: search across all fields
        matches = [
            r
            for r in RULES
            if category_lower in r["name"].lower()
            or category_lower in r.get("pattern", "").lower()
            or category_lower in r["id"].lower()
        ]
    if not matches:
        return json.dumps({"matches": [], "note": f"No rules found for '{category}'."})
    return json.dumps({"matches": matches})


def _fuzzy_lookup(registry: dict[str, dict], key: str, error_msg: str) -> str:
    """Look up by exact key, then fall back to partial match."""
    exact = registry.get(key)
    if exact is not None:
        return json.dumps({"file": key, **exact})
    for path, data in registry.items():
        if key in path or path in key:
            return json.dumps({"file": path, **data})
    return json.dumps({"error": error_msg})


def get_file_context(file_path: str) -> str:
    """Get the surrounding code context for a file."""
    return _fuzzy_lookup(
        FILE_CONTEXT, file_path, f"File '{file_path}' not found in codebase context."
    )


def check_test_coverage(file_path: str) -> str:
    """Check test coverage for a file."""
    return _fuzzy_lookup(
        TEST_COVERAGE, file_path, f"No coverage data for '{file_path}'. File may be untested."
    )


TOOL_DISPATCH = {
    "lookup_rule": lambda args: lookup_rule(args["category"]),
    "get_file_context": lambda args: get_file_context(args["file_path"]),
    "check_test_coverage": lambda args: check_test_coverage(args["file_path"]),
}


def run(diff: str) -> str:
    """Run the code review agent on a diff."""
    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": f"Review this diff:\n\n{diff}"}]

    for _ in range(10):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return "\n".join(block.text for block in response.content if hasattr(block, "text"))

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                fn_name = block.name
                handler = TOOL_DISPATCH.get(fn_name)
                if handler:
                    result = handler(block.input)
                else:
                    result = json.dumps({"error": f"Unknown tool '{fn_name}'."})
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )
        messages.append({"role": "user", "content": tool_results})

    return "Error: agent exceeded maximum iterations."


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent.py '<diff or file path>'")
        sys.exit(1)
    print(run(sys.argv[1]))
