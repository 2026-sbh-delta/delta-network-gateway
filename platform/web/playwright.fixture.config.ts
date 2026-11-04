import { defineConfig } from "@playwright/test";

// Browser-only fixtures use Vite and intercept every API call in the spec.
// This configuration deliberately does not start the database-backed E2E server.
export default defineConfig({
  testDir: "./tests",
  testMatch: [
    "workflow-planning.spec.ts",
    "observability.spec.ts",
    "presentation.spec.ts",
  ],
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5174",
    viewport: { width: 1360, height: 1000 },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5174",
    url: "http://127.0.0.1:5174",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
