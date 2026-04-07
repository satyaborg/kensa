# SDR Qualifier Agent

Multi-tool agent that scores inbound leads by enriching company data, checking CRM for existing relationships, analyzing prospect website activity, and matching against an Ideal Customer Profile. Handles existing customers, churned contacts, competitors, and conflicting signals.

Bad qualification wastes AE time on dead leads or lets hot prospects go cold. Generic outreach tanks reply rates.

## Tools

| Tool | Purpose | Can fail? |
|------|---------|-----------|
| `lookup_icp` | Get target segments, disqualifiers, scoring, outreach rules |, |
| `enrich_company` | Company data: size, funding, industry, tech stack, news | Unknown domain |
| `check_crm` | Check if contact is existing customer/churned/competitor | New prospect (no record) |
| `get_recent_activity` | Website page views, form submissions, engagement signals | No tracked activity |

## What makes this hard

- **Existing customers**: Sarah (Acme Corp) is already an enterprise customer: agent should route to her AE for upsell, not send cold outreach.
- **Competitors**: RivalTools CTO is browsing our docs: CRM has them flagged. Agent must disqualify.
- **Churned contacts**: Alex (GrowthCo) churned citing "not enough integrations." Win-back opportunity or still a bad fit?
- **Data conflicts**: a lead might claim "500 employees" but enrichment shows 8. Trust enrichment.
- **Activity signals**: Elena (ScaleUp EU) viewed pricing 3 days in a row + enterprise page = very hot. Jake submitted a demo request form = high intent.
- **Edge cases**: TinyTeam has 8 employees (below threshold) but 3 engineers and bootstrapped: is this a disqualify or SMB?
- **Compound scoring**: agent must combine CRM status + enrichment + activity + ICP match.

## Data

ICP with 3 segments and 6 disqualifiers. Enrichment data for 8 companies. CRM records for 5 contacts (2 customers, 1 churned, 1 competitor, 1 prospect). Activity tracking for 4 prospects.

## Eval it

```bash
cd examples/sdr-qualifier
# then in Claude Code:
> evaluate this agent
```

Requires `ANTHROPIC_API_KEY`.
