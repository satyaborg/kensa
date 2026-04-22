import type { NextConfig } from "next";

const mintlifyDocsOrigin = process.env.MINTLIFY_DOCS_ORIGIN?.replace(/\/+$/, "");

const nextConfig: NextConfig = {
  images: { unoptimized: true },
  async rewrites() {
    if (!mintlifyDocsOrigin) {
      return [];
    }

    return [
      {
        source: "/docs",
        destination: `${mintlifyDocsOrigin}/docs`,
      },
      {
        source: "/docs/:path*",
        destination: `${mintlifyDocsOrigin}/docs/:path*`,
      },
    ];
  },
};

export default nextConfig;
