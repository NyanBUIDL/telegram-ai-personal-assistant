import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const channel = process.env.PLAYWRIGHT_BROWSER_CHANNEL || "chrome";
const baseURL = "http://127.0.0.1:5174";

export default defineConfig({
  testDir: ".",
  testMatch: "dashboard-reliability.spec.js",
  workers: 1,
  use: {
    baseURL,
    channel: channel === "chromium" ? undefined : channel,
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: "node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5174 --strictPort",
    cwd: fileURLToPath(new URL("..", import.meta.url)),
    url: baseURL,
    reuseExistingServer: false,
  },
});
