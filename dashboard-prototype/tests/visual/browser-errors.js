import { expect, test as base } from '@playwright/test';

// Vite's optional HMR socket may be blocked by local-network browser policy.
// Permit this exact diagnostic only in local development, never in CI. The
// rendered UI still has to pass all geometry, font, interaction and focus checks.
function allowedLocalHmr(message) {
  if (process.env.CI || process.env.ART_ALLOW_BLOCKED_HMR !== '1') return false;
  const baseUrl = process.env.ART_BASE_URL || 'http://127.0.0.1:5174';
  if (message.location().url !== `${baseUrl}/@vite/client`) return false;
  const text = message.text();
  return /^WebSocket connection to 'ws:\/\/127\.0\.0\.1:5174\/\?token=[A-Za-z0-9_-]+' failed: Error in connection establishment: net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS$/.test(text) ||
    text.startsWith('[vite] failed to connect to websocket.\nyour current setup:\n') &&
    text.endsWith('https://vite.dev/config/server-options.html#server-hmr .');
}

export const test = base.extend({
  browserErrors: [async ({ page }, use) => {
    const errors = [];
    const pageError = () => errors.push('Uncaught page exception');
    const consoleError = message => {
      if (message.type() === 'error' && !allowedLocalHmr(message)) {
        errors.push('Unexpected browser console error');
      }
    };
    page.on('pageerror', pageError);
    page.on('console', consoleError);
    await use();
    page.off('pageerror', pageError);
    page.off('console', consoleError);
    // Do not echo arbitrary error messages, URLs or credential-like payloads.
    expect(errors, 'Unexpected JavaScript errors in the art fixture').toEqual([]);
  }, { auto: true }],
});
