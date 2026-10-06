import { defineConfig } from '@playwright/test';

const channel = process.env.D02_BROWSER_CHANNEL || 'chrome';
if (!['chrome', 'chromium'].includes(channel)) {
  throw new Error('Invalid internal D02 browser channel override');
}

export default defineConfig({
  testDir: './tests',
  testMatch: ['d02-auth.spec.js', 'u02-native.spec.js'],
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: 'line',
  use: { channel, screenshot: 'off', trace: 'off', video: 'off' },
});
