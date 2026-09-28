import type { NextConfig } from "next";

// `VARUNA_DIST_DIR` exists for the performance pass (P10.4). Section 14's budgets are all
// production-build numbers - a `next dev` build invents stalls that are not in the product - but
// `make demo` runs `next dev` out of `.next`, so a production build into the same directory while
// the demo is up would tear the demo down mid-measurement. Setting VARUNA_DIST_DIR=.next-perf
// builds and serves the production bundle beside a running dev server instead of on top of it.
// Unset (the demo, CI, Vercel) it is exactly `.next` as before.
const distDir = process.env.VARUNA_DIST_DIR ?? ".next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  typedRoutes: true,
  transpilePackages: ["@varuna/tokens"],
  distDir,
  // `make demo` serves the console from `next dev`, so the dev route indicator would sit in the
  // corner of the map on stage and in every docs/screens baseline. Compile and runtime errors are
  // still surfaced (SPEC.md section 15: nothing on screen that is not the product).
  devIndicators: false,
};

export default nextConfig;
