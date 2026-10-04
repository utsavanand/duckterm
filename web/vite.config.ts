/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), {
    name: "legacy-favicon",
    generateBundle() {
      // Keep the stable URL for older clients, without a second editable mark.
      // Current HTML and the header both use Vite's content-hashed SVG URL.
      this.emitFile({
        type: "asset",
        fileName: "favicon.svg",
        source: readFileSync(new URL("./src/assets/duckmark.svg", import.meta.url)),
      });
    },
  }],
  server: {
    proxy: {
      "/events": "http://localhost:4300",
      "/stream": "http://localhost:4300",
      "/sessions": "http://localhost:4300",
      "/snapshots": "http://localhost:4300",
      "/tree": "http://localhost:4300",
      "/approvals": "http://localhost:4300",
      "/terminals": "http://localhost:4300",
      "/browse": "http://localhost:4300",
    },
  },
  test: {
    // jsdom for the few modules that touch localStorage/Date; most tests are
    // pure-logic and would run under 'node' too.
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    // Unit tests live next to source as *.test.ts(x). Playwright e2e specs in
    // web/e2e/ run separately (npm run e2e) and must be excluded here.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
