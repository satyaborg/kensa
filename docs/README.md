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

To serve the docs at `https://kensa.sh/docs` instead of a separate docs subdomain:

1. Create the Mintlify project first and note its Mintlify subdomain from the dashboard URL (`dashboard.mintlify.com/<org>/<subdomain>`).
2. In the Mintlify dashboard, open Custom domain setup and enable the `Host at /docs` toggle.
3. Add `kensa.sh` as the domain in Mintlify.
4. Set `MINTLIFY_DOCS_ORIGIN=https://<subdomain>.mintlify.dev` on the Vercel project that deploys `website/`.
5. Deploy the Vercel project so `website/next.config.ts` can proxy `/docs` and `/docs/:path*` to `https://<subdomain>.mintlify.dev/docs`.

Do not proxy `/docs` to `https://docs.kensa.sh` or any other custom domain root. Mintlify only emits the correct `/docs/...` URLs when the upstream request path also includes `/docs`.
