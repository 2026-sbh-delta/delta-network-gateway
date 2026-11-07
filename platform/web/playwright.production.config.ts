import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: "production-ui.spec.ts",
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5182",
    screenshot: "only-on-failure",
  },
  webServer: {
    command:
      "npm exec vite preview -- --host 127.0.0.1 --port 5182 --strictPort",
    url: "http://127.0.0.1:5182",
    reuseExistingServer: false,
  },
});
