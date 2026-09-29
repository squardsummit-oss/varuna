import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque } from "next/font/google";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";

import { DEFAULT_THEME } from "@varuna/tokens";

import { SwRegister } from "@/components/varuna/sw-register";
import { devanagari } from "@/lib/i18n/devanagari";
import { Providers } from "@/lib/providers";
import { THEME_BOOTSTRAP_SCRIPT } from "@/lib/theme-bootstrap";

import "./globals.css";

/** Display face: landing headlines, page titles, the big depth number (SPEC.md section 6.3). */
const bricolage = Bricolage_Grotesque({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-bricolage",
  display: "swap",
});

export const metadata: Metadata = {
  // Resolves the Open Graph and Twitter image URLs; the demo runs on the laptop, so localhost
  // is the honest default and a deployment overrides it with NEXT_PUBLIC_SITE_URL.
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"),
  title: {
    default: "VARUNA",
    template: "%s - VARUNA",
  },
  description:
    "Street-level urban flood nowcasting digital twin: Doppler radar to street-by-street depth for the next three hours, a drain map that learns from every flood, and routes for emergency services. SIH 2026, PS SIH26085.",
  applicationName: "VARUNA",
  // The SVG mark for browser tabs, and the 180 px PNG iOS asks for when the public map is added
  // to a home screen: Safari ignores an SVG there and would screenshot the page instead.
  icons: {
    icon: "/icon.svg",
    apple: "/apple-icon.png",
  },
};

export const viewport: Viewport = {
  // The dark default; the head script and lib/theme.ts rewrite it to the light --ink on a switch.
  themeColor: "#0A1020", // lint-design-allow: browser chrome colour must be a literal; equals --ink
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      // Dark is the server default; the head script below swaps in a stored light choice before
      // first paint, so React must accept the attribute the DOM ends up with.
      data-theme={DEFAULT_THEME}
      suppressHydrationWarning
      className={`h-full ${bricolage.variable} ${GeistSans.variable} ${GeistMono.variable} ${devanagari.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP_SCRIPT }} />
      </head>
      <body className="bg-ink text-text flex min-h-full flex-col font-sans antialiased">
        <Providers>{children}</Providers>
        <SwRegister />
      </body>
    </html>
  );
}
