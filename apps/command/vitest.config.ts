import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    globals: false,
    // 19 jsdom environments on a loaded machine (dev server compiling, Playwright running) can
    // outrun vitest's 5 s default; the suite itself is synchronous, so this only buys headroom.
    testTimeout: 15_000,
    hookTimeout: 15_000,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/__tests__/**/*.test.{ts,tsx}", "**/*.test.{ts,tsx}"],
    exclude: ["node_modules/**", ".next/**", "e2e/**", "tests/**"],
    css: false,
  },
});
