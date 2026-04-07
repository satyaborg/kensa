import { IBM_Plex_Mono, Silkscreen } from "next/font/google";
import { GeistSans } from "geist/font/sans";
import type { Metadata } from "next";
import "./globals.css";

const ibmPlexMono = IBM_Plex_Mono({
  weight: ["400", "500", "700"],
  subsets: ["latin"],
  display: "swap",
  variable: "--font-ibm-plex-mono",
});

const silkscreen = Silkscreen({
  weight: "400",
  subsets: ["latin"],
  display: "swap",
  variable: "--font-silkscreen",
});

const SITE_URL = "https://kensa.sh";
const SITE_TITLE = "kensa — the open source agent evals harness";
const SITE_DESCRIPTION =
  "Tell your coding agent to evaluate an agent and get a working eval suite in minutes. Deterministic checks, LLM judge, OTel tracing. No platform.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: SITE_TITLE,
  description: SITE_DESCRIPTION,
  icons: {
    icon: [
      { url: "/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: "kensa",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    images: [
      {
        url: "/thumbnail.png",
        width: 1920,
        height: 1080,
        alt: "kensa — the open source agent evals harness",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    images: ["/thumbnail.png"],
    creator: "@kensa_sh",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${ibmPlexMono.variable} ${silkscreen.variable} dark`}
      suppressHydrationWarning
    >
      <body>{children}</body>
    </html>
  );
}
