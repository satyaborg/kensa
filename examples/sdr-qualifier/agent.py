"""SDR qualifier agent — scores inbound leads and drafts personalized outreach.

Multi-tool agent that checks ICP criteria, enriches company data, searches the CRM
for existing relationships, and checks recent prospect activity before scoring.
Handles compound signals: existing customers, conflicting data, edge cases.

Bad qualification wastes AE time on dead leads or lets hot prospects go cold.
Generic outreach tanks reply rates. Both cost pipeline.
"""

from __future__ import annotations

import json
import sys

import anthropic

ICP_CRITERIA: dict = {
    "target_segments": [
        {
            "segment": "enterprise",
            "company_size": "500+",
            "industries": ["saas", "fintech", "healthcare", "e-commerce"],
            "signals": ["dedicated engineering team", "existing CI/CD", "compliance needs"],
            "score": "hot",
            "action": "book_meeting",
        },
        {
            "segment": "mid-market",
            "company_size": "50-500",
            "industries": ["saas", "fintech", "devtools", "marketplace"],
            "signals": ["growing engineering team", "evaluating tools", "series A-C"],
            "score": "warm",
            "action": "nurture_sequence",
        },
        {
            "segment": "smb",
            "company_size": "10-50",
            "industries": ["saas", "agency", "consulting"],
            "signals": ["technical founder", "developer-led buying"],
            "score": "warm",
            "action": "self_serve_trial",
        },
    ],
    "disqualifiers": [
        "company_size < 10 (unless funded startup with announced round)",
        "no engineering team and no technical co-founder",
        "government/public sector (no FedRAMP yet)",
        "direct competitor (check CRM and enrichment data)",
        "student or academic project (unless university research lab with grant funding)",
        "geographic restriction: sanctioned countries",
    ],
    "scoring_rubric": {
        "hot": "Matches target segment + 2 signals + budget authority indicated. Book meeting within 24h.",
        "warm": "Matches segment, some signals. Nurture with value-add content. Follow up in 3-5 days.",
        "cold": "Partial match, missing key signals. Add to newsletter, revisit in 30 days.",
        "disqualified": "Matches a disqualifier. Polite decline with alternative resource if applicable.",
    },
    "outreach_rules": [
        "Reference something specific about their company (recent funding, blog post, job listing).",
        "Lead with the problem you solve, not features.",
        "One clear CTA per message. Never 'let me know if you have any questions.'",
        "Max 3 sentences for initial outreach. Respect their time.",
        "Never lie about capabilities or timeline. If we can't do something, say so.",
    ],
}


ENRICHMENT_DB: dict[str, dict] = {
    "acmecorp.com": {
        "company": "Acme Corp",
        "domain": "acmecorp.com",
        "industry": "saas",
        "employee_count": 850,
        "engineering_team_size": 120,
        "funding": "Series D — $180M (2024)",
        "tech_stack": ["AWS", "Kubernetes", "Python", "React", "PostgreSQL"],
        "recent_news": "Launched AI-powered analytics suite in Q1 2025",
        "competitors": [],
        "location": "San Francisco, CA",
    },
    "startupxyz.io": {
        "company": "StartupXYZ",
        "domain": "startupxyz.io",
        "industry": "devtools",
        "employee_count": 28,
        "engineering_team_size": 18,
        "funding": "Series A — $12M (2025-02)",
        "tech_stack": ["GCP", "Go", "React", "MongoDB"],
        "recent_news": "Hiring 10 engineers, job posts on LinkedIn",
        "competitors": [],
        "location": "Austin, TX",
    },
    "megacorp.com": {
        "company": "MegaCorp Industries",
        "domain": "megacorp.com",
        "industry": "e-commerce",
        "employee_count": 3200,
        "engineering_team_size": 400,
        "funding": "Public (NYSE: MEGA)",
        "tech_stack": ["AWS", "Java", "React", "Oracle", "Jenkins"],
        "recent_news": "Announced cloud migration initiative for 2025",
        "competitors": [],
        "location": "New York, NY",
    },
    "tinyteam.co": {
        "company": "TinyTeam",
        "domain": "tinyteam.co",
        "industry": "agency",
        "employee_count": 8,
        "engineering_team_size": 3,
        "funding": "Bootstrapped",
        "tech_stack": ["Vercel", "Next.js", "Supabase"],
        "recent_news": None,
        "competitors": [],
        "location": "Remote",
    },
    "rivaltools.dev": {
        "company": "RivalTools",
        "domain": "rivaltools.dev",
        "industry": "devtools",
        "employee_count": 45,
        "engineering_team_size": 30,
        "funding": "Series A — $8M (2024)",
        "tech_stack": ["AWS", "Python", "Vue.js"],
        "recent_news": "Launched competing product in our space Q4 2024",
        "competitors": ["us"],
        "location": "London, UK",
    },
    "govtech.gov": {
        "company": "GovTech Solutions",
        "domain": "govtech.gov",
        "industry": "government",
        "employee_count": 500,
        "engineering_team_size": 80,
        "funding": "Government agency",
        "tech_stack": ["Azure", ".NET", "SQL Server"],
        "recent_news": "RFP for developer tooling modernization",
        "competitors": [],
        "location": "Washington, DC",
    },
    "fastship.dev": {
        "company": "FastShip",
        "domain": "fastship.dev",
        "industry": "saas",
        "employee_count": 75,
        "engineering_team_size": 35,
        "funding": "Series B — $25M (2024-11)",
        "tech_stack": ["AWS", "Python", "FastAPI", "React", "PostgreSQL"],
        "recent_news": "Blog post: 'Why we rebuilt our CI pipeline from scratch'",
        "competitors": [],
        "location": "Berlin, Germany",
    },
    "scaleup.eu": {
        "company": "ScaleUp EU",
        "domain": "scaleup.eu",
        "industry": "fintech",
        "employee_count": 200,
        "engineering_team_size": 60,
        "funding": "Series B — $40M (2025-01)",
        "tech_stack": ["AWS", "Kotlin", "React", "PostgreSQL"],
        "recent_news": "Expanding to US market, hiring US-based AEs",
        "competitors": [],
        "location": "Amsterdam, Netherlands",
    },
}


CRM_DB: dict[str, dict] = {
    "sarah@acmecorp.com": {
        "contact_id": "CON-001",
        "name": "Sarah Chen",
        "company": "Acme Corp",
        "status": "customer",
        "plan": "enterprise",
        "owner": "Dana Park (AE)",
        "last_contact": "2025-03-15",
        "notes": "Happy customer. Expanding to 3 more teams in Q2.",
        "deal_value": "$59,988/yr",
    },
    "marcus@megacorp.com": {
        "contact_id": "CON-004",
        "name": "Marcus Webb",
        "company": "MegaCorp Industries",
        "status": "customer",
        "plan": "enterprise",
        "owner": "Dana Park (AE)",
        "last_contact": "2025-03-20",
        "notes": "Renewal in Sep 2025. Custom SLA. Very happy.",
        "deal_value": "$150,000/yr",
    },
    "nina@fastship.dev": {
        "contact_id": "CON-007",
        "name": "Nina Okoro",
        "company": "FastShip",
        "status": "customer",
        "plan": "pro",
        "owner": "Unassigned",
        "last_contact": "2025-02-17",
        "notes": "Converted from trial. Asked about enterprise features.",
        "deal_value": "$1,788/yr",
    },
    "alex@growthco.com": {
        "contact_id": "CON-006",
        "name": "Alex Ruiz",
        "company": "GrowthCo",
        "status": "churned",
        "plan": None,
        "owner": "Unassigned",
        "last_contact": "2024-04-10",
        "notes": "Churned after 3 months. Cited 'not enough integrations'. Refunded last month.",
        "deal_value": None,
    },
    "cto@rivaltools.dev": {
        "contact_id": "CON-010",
        "name": "James Chen",
        "company": "RivalTools",
        "status": "competitor",
        "plan": None,
        "owner": None,
        "last_contact": None,
        "notes": "Direct competitor. Do not engage. Flagged by legal.",
        "deal_value": None,
    },
}


ACTIVITY_DB: dict[str, list[dict]] = {
    "sarah@acmecorp.com": [
        {"type": "page_view", "page": "/pricing", "timestamp": "2025-04-02T14:30:00Z"},
        {"type": "page_view", "page": "/enterprise", "timestamp": "2025-04-02T14:35:00Z"},
        {"type": "page_view", "page": "/case-studies", "timestamp": "2025-04-03T09:00:00Z"},
    ],
    "jake@startupxyz.io": [
        {"type": "page_view", "page": "/pricing", "timestamp": "2025-04-01T10:00:00Z"},
        {"type": "page_view", "page": "/docs/getting-started", "timestamp": "2025-04-01T10:15:00Z"},
        {"type": "form_submit", "page": "/demo-request", "timestamp": "2025-04-03T16:00:00Z"},
    ],
    "elena@scaleup.eu": [
        {"type": "page_view", "page": "/pricing", "timestamp": "2025-04-01T08:00:00Z"},
        {"type": "page_view", "page": "/pricing", "timestamp": "2025-04-02T08:00:00Z"},
        {"type": "page_view", "page": "/pricing", "timestamp": "2025-04-03T08:00:00Z"},
        {"type": "page_view", "page": "/enterprise", "timestamp": "2025-04-03T08:05:00Z"},
        {"type": "page_view", "page": "/security", "timestamp": "2025-04-03T08:10:00Z"},
    ],
    "cto@rivaltools.dev": [
        {"type": "page_view", "page": "/pricing", "timestamp": "2025-04-04T11:00:00Z"},
        {"type": "page_view", "page": "/docs/api-reference", "timestamp": "2025-04-04T11:15:00Z"},
    ],
}


_ICP_ASPECT_MAP: dict[str, str] = {
    "segments": "target_segments",
    "disqualifiers": "disqualifiers",
    "scoring": "scoring_rubric",
    "outreach": "outreach_rules",
}


def lookup_icp(aspect: str) -> str:
    """Look up ICP criteria by aspect."""
    aspect_lower = aspect.lower()
    if aspect_lower == "all":
        return json.dumps(ICP_CRITERIA)
    key = _ICP_ASPECT_MAP.get(aspect_lower)
    if key is None:
        return json.dumps(
            {
                "error": f"Unknown aspect '{aspect}'. Valid: segments, disqualifiers, scoring, outreach, all."
            }
        )
    return json.dumps({key: ICP_CRITERIA[key]})


def enrich_company(domain: str) -> str:
    """Enrich company data by domain."""
    domain = domain.lower().strip()
    # Strip protocol if present
    for prefix in ("https://", "http://", "www."):
        if domain.startswith(prefix):
            domain = domain[len(prefix) :]
    domain = domain.rstrip("/")

    data = ENRICHMENT_DB.get(domain)
    if data is None:
        return json.dumps(
            {
                "error": "not_found",
                "message": f"No enrichment data for '{domain}'. Company may be too small or too new for our data provider.",
            }
        )
    return json.dumps(data)


def check_crm(email: str) -> str:
    """Check the CRM for an existing contact by email."""
    email = email.lower().strip()
    contact = CRM_DB.get(email)
    if contact is None:
        return json.dumps(
            {
                "status": "new_prospect",
                "message": f"No existing CRM record for '{email}'. This is a net-new lead.",
            }
        )
    return json.dumps(contact)


def get_recent_activity(email: str) -> str:
    """Get recent website activity for a prospect."""
    email = email.lower().strip()
    activities = ACTIVITY_DB.get(email)
    if activities is None:
        return json.dumps(
            {
                "email": email,
                "activities": [],
                "note": "No tracked activity. Prospect may have come through an offline channel.",
            }
        )
    return json.dumps(
        {
            "email": email,
            "activities": activities,
            "total_events": len(activities),
            "page_views": sum(1 for a in activities if a["type"] == "page_view"),
            "form_submits": sum(1 for a in activities if a["type"] == "form_submit"),
        }
    )


TOOLS = [
    {
        "name": "lookup_icp",
        "description": (
            "Look up the Ideal Customer Profile criteria. Returns target segments, "
            "disqualifiers, scoring rubric, and outreach rules. Use to validate "
            "lead qualification decisions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "aspect": {
                    "type": "string",
                    "description": "Which aspect: 'segments', 'disqualifiers', 'scoring', 'outreach', or 'all'",
                },
            },
            "required": ["aspect"],
        },
    },
    {
        "name": "enrich_company",
        "description": (
            "Enrich company data using domain name. Returns industry, employee count, "
            "engineering team size, funding, tech stack, recent news, and competitor flag. "
            "Use this to verify company details before scoring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Company domain (e.g., 'acmecorp.com')",
                },
            },
            "required": ["domain"],
        },
    },
    {
        "name": "check_crm",
        "description": (
            "Check the CRM for an existing contact. Returns contact status (customer, "
            "churned, competitor), current plan, owner, and notes. ALWAYS check before "
            "qualifying — the lead may already be a customer or flagged as a competitor."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Contact email address",
                },
            },
            "required": ["email"],
        },
    },
    {
        "name": "get_recent_activity",
        "description": (
            "Get recent website activity for a prospect. Returns page views, form "
            "submissions, and engagement patterns. High activity on pricing/enterprise "
            "pages is a strong buying signal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Prospect email address",
                },
            },
            "required": ["email"],
        },
    },
]

SYSTEM_PROMPT = """\
You are an SDR qualification agent. You receive inbound leads and must score, \
route, and draft outreach.

WORKFLOW — follow this order:
1. Check the CRM — is this person already a customer, churned, or a competitor?
2. Enrich the company — get real data on size, funding, industry, tech stack.
3. Check recent activity — what pages have they visited? Did they submit a form?
4. Look up ICP criteria — match enriched data against segments and disqualifiers.
5. Score and draft outreach based on ALL the above.

RULES:
- ALWAYS check CRM first. If they're already a customer, don't send cold outreach. \
  Route to their account owner or suggest an upsell path.
- If CRM says "competitor", disqualify immediately. Do not engage.
- If CRM says "churned", note the churn reason. They may be a win-back opportunity \
  if circumstances changed — or they may still be a bad fit.
- Use enrichment data to verify what the lead claims. If they say "500 employees" \
  but enrichment shows 8, trust the enrichment data.
- Activity signals matter: pricing page 3x + enterprise page = hot. Docs only = evaluating. \
  No activity = cold (offline channel or tire kicker).
- Match against ICP segments using ENRICHED data, not just what the lead says.
- Check for disqualifiers BEFORE scoring. A disqualified lead should never be scored hot.
- Draft outreach using the outreach rules. Reference specific enrichment data \
  (funding round, blog post, job listings).

Output format:
- Score: hot / warm / cold / disqualified
- Segment: <matched segment or "none">
- Signals: <matching signals observed>
- CRM status: <new / existing customer / churned / competitor>
- Activity summary: <key engagement signals>
- Action: <book_meeting / nurture_sequence / self_serve_trial / upsell / win_back / disqualify>
- Outreach: <draft message if applicable>
- Reasoning: <why this score, citing enrichment data and activity>
"""


TOOL_DISPATCH = {
    "lookup_icp": lambda args: lookup_icp(args["aspect"]),
    "enrich_company": lambda args: enrich_company(args["domain"]),
    "check_crm": lambda args: check_crm(args["email"]),
    "get_recent_activity": lambda args: get_recent_activity(args["email"]),
}


def run(lead_info: str) -> str:
    """Run the SDR agent on a lead."""
    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": f"New inbound lead:\n\n{lead_info}"}]

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
        print("Usage: python agent.py '<lead information>'")
        sys.exit(1)
    print(run(sys.argv[1]))
