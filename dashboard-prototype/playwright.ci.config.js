import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/visual',
  testMatch: 'art-baseline.spec.js',
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: 'list',
  use: { browserName: 'chromium', trace: 'off', video: 'off', screenshot: 'off' },
  webServer: {
    command: 'node node_modules/vite/bin/vite.js --config vite.ci.config.mjs --host 127.0.0.1 --port 5174 --strictPort',
    url: 'http://127.0.0.1:5174',
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
