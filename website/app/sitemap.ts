import type { MetadataRoute } from "next";

export const dynamic = "force-static";

const SITE_URL = "https://kensa.sh";
const hasDocsProxy = Boolean(process.env.MINTLIFY_DOCS_ORIGIN);

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  const pages: MetadataRoute.Sitemap = [
    {
      url: SITE_URL,
      lastModified: now,
      changeFrequency: "weekly",
      priority: 1.0,
    },
  ];

  if (hasDocsProxy) {
    pages.push({
      url: `${SITE_URL}/docs`,
      lastModified: now,
      changeFrequency: "weekly",
      priority: 0.9,
    });
  }

  return pages;
}
