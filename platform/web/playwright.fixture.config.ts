import { defineConfig } from "@playwright/test";

// Browser-only fixtures intercept API calls and can serve the release assets.
// This configuration deliberately does not start the database-backed E2E server.
const built = process.env.PLAYWRIGHT_BUILT_ASSETS === "1";

export default defineConfig({
  testDir: "./tests",
  testMatch: [
    "workflow-planning.spec.ts",
    "deployment.spec.ts",
    "application-flow.spec.ts",
    "observability.spec.ts",
    "presentation.spec.ts",
    "release.spec.ts",
    "project-delete.spec.ts",
    "runtime-removal.spec.ts",
  ],
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5174",
    viewport: { width: 1360, height: 1000 },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: built
      ? "npm exec vite preview -- --host 127.0.0.1 --port 5174 --strictPort"
      : "npm run dev -- --host 127.0.0.1 --port 5174",
    url: "http://127.0.0.1:5174",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
