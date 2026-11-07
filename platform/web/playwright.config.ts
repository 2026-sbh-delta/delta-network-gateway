import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testIgnore: "production-ui.spec.ts",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8088",
    viewport: { width: 1360, height: 1000 },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "cd ../backend && uv run python tests/e2e_server.py",
    url: "http://127.0.0.1:8088/health/ready",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
