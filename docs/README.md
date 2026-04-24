# Kensa Docs (Mintlify)

Kensa's documentation pages, authored as MDX and served by [Mintlify](https://mintlify.com).

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
  docs.json            # navigation, theme, colors, SEO
  style.css            # site-wide Mintlify chrome overrides
  introduction.mdx     # landing page of the docs (/)
  quickstart.mdx
  concepts.mdx
  scenarios.mdx
  checks.mdx
  judge.mdx
  tracing.mdx
  skills.mdx
  cli.mdx
  mcp-server.mdx
  ci.mdx
  examples.mdx
  changelog.mdx
```

Each `.mdx` filename is the URL slug (e.g. `quickstart.mdx` → `/quickstart`).

## Deploying

Docs are served at `https://kensa.sh/docs` by proxying to a Mintlify project through the marketing site's Next.js rewrites.

1. Create the Mintlify project in the [Mintlify dashboard](https://dashboard.mintlify.com) and point it at this `docs/` directory. Note its Mintlify subdomain (`dashboard.mintlify.com/<org>/<subdomain>`).
2. In the Mintlify dashboard, open Custom domain setup and enable the `Host at /docs` toggle, then add `kensa.sh` as the domain.
3. Set `MINTLIFY_DOCS_ORIGIN=https://<subdomain>.mintlify.dev` on the Vercel project that deploys `website/`.
4. Deploy the Vercel project. `website/next.config.ts` will proxy `/docs` and `/docs/:path*` to `https://<subdomain>.mintlify.dev/docs`.

The upstream request path must include `/docs` for Mintlify to emit correct `/docs/...` URLs, which is why the proxy targets `${MINTLIFY_DOCS_ORIGIN}/docs` rather than the origin root.
