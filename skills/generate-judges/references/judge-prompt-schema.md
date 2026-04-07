# Judge Prompt Schema

Structured judge prompts live at `.kensa/judges/{name}.yaml`. Scenarios reference them with `judge: {name}` (mutually exclusive with `criteria:`).

## Schema

```yaml
criterion: string               # what is being evaluated (one thing only)
pass: string                    # explicit pass definition
fail: string                    # explicit fail definition
examples:                       # few-shot examples (2-4 recommended)
  - output: string              # agent output example
    label: pass | fail          # ground truth label
    critique: string            # why this label is correct
```

## Example

```yaml
criterion: Email tone matches the client persona

pass: |
  Language appropriate for the client type:
  - Luxury: formal, exclusive features, premium positioning
  - First-time: warm, educational, avoids jargon
  - Investor: data-driven, ROI-focused, concise

fail: |
  Tone mismatched to persona. Casual slang for luxury,
  heavy jargon for first-time, emotional language for investor.

examples:
  - output: "Dear Mr. Harrington, I am pleased to present an exclusive listing..."
    label: pass
    critique: "Formal salutation, luxury positioning language throughout."
  - output: "Hey! Check out this awesome place, it's got a pool and stuff!"
    label: fail
    critique: "Casual slang inappropriate for luxury buyer."
  - output: "Hi Sarah, I found a property that might be a great fit for your first home..."
    label: pass
    critique: "Warm greeting, relatable terms, no jargon. Matches first-time buyer tone."
```

## Referencing from a scenario

```yaml
# Instead of:
criteria: The email should match the client persona tone

# Use:
judge: tone-match   # loads .kensa/judges/tone-match.yaml
```

## When to use structured vs inline

Use `criteria:` (inline string) for objective, simple evaluations:
- "The response mentions Tokyo"
- "The agent used the correct tool"

Use `judge:` (structured file) when:
- Pass/fail boundary is subjective and needs explicit definitions
- You need few-shot examples to calibrate the judge
- Multiple scenarios share the same evaluation standard
- One-line criteria produce inconsistent judge verdicts
