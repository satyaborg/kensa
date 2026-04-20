# Kensa Docs (Mintlify)

These are the Kensa documentation pages, authored as MDX and served by [Mintlify](https://mintlify.com).

## Local preview

Use an LTS Node release (`20`, `22`, or `24`). Mintlify does not currently support Node 25+.

```bash
# Install the Mintlify CLI once
npm i -g mint

# From this directory
mint dev
```

The dev server reads `docs.json` for navigation and theme configuration, and picks up the `.mdx` files in this folder automatically.

## Validation

Run these before shipping larger docs changes:

```bash
mint broken-links
mint a11y
```

## Structure

```
docs/
  docs.json            # navigation, redirects, theme, colors, SEO
  style.css            # site-wide Mintlify chrome overrides
  getting-started.mdx  # section landing page
  introduction.mdx     # landing page of the docs (/)
  quickstart.mdx
  concepts.mdx
  reference.mdx        # section landing page
  scenarios.mdx
  checks.mdx
  judge.mdx
  tracing.mdx
  workflows.mdx        # section landing page
  skills.mdx
  cli.mdx
  mcp.mdx
  ci.mdx
  examples.mdx
  changelog.mdx
```

Each `.mdx` filename is the URL slug (e.g. `quickstart.mdx` → `/quickstart`).

## Deploying

Connect this repository to Mintlify at [dashboard.mintlify.com](https://dashboard.mintlify.com) and point the deployment at `docs/`. Mintlify auto-deploys on push to the default branch.
