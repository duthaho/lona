import { defineConfig, devices } from "@playwright/test";

const chromiumExecutable = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
const browserOverride = chromiumExecutable ? { launchOptions: { executablePath: chromiumExecutable } } : {};

export default defineConfig({
  testDir: "./frontend/e2e",
  testMatch: "**/*.e2e.ts",
  fullyParallel: false,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:18765",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], ...browserOverride } },
    { name: "mobile", use: { ...devices["Pixel 7"], ...browserOverride } },
  ],
  webServer: {
    command: "python3 -m tests.ui_server",
    url: "http://127.0.0.1:18765",
    reuseExistingServer: false,
    timeout: 15_000,
  },
});
