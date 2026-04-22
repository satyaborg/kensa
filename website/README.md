# Website

Landing page for kensa. Built with [Next.js](https://nextjs.org) + Tailwind CSS v4.

The production docs are being migrated to Mintlify from the sibling `../docs/` directory. This app stays on Vercel for the landing page and proxies `/docs` to Mintlify when `MINTLIFY_DOCS_ORIGIN=https://<subdomain>.mintlify.dev` is set in the Vercel project environment.

```bash
pnpm i
pnpm dev        # http://localhost:3000
```
