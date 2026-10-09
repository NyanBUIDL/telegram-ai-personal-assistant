import { expect, test } from '@playwright/test';
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fixtureMessages, withFixtureCleanup, publicFixtureMessage, navigateToDashboard } from './helpers/native-fixture.js';

const evidence = resolve(process.env.ART_EVIDENCE_DIR || '../.test-temp/u02-static-fix1-art');
mkdirSync(evidence, { recursive: true });
test.skip(process.platform !== 'win32', 'Actual Windows worker and SID pipe required');
if (process.env.D02_CHROMIUM_EXECUTABLE) test.use({ channel: undefined, launchOptions: { executablePath: process.env.D02_CHROMIUM_EXECUTABLE } });

for (const variant of ['lf', 'crlf', 'cr']) {
  test(`compiled ${variant} bootstrap reaches actual hidden Qt provider once`, async ({ page, context }) => {
    test.setTimeout(75000);
    const child = spawn(process.env.D02_PYTHON || resolve('../.venv-q01/Scripts/python.exe'),
      ['-u', resolve('../tests/fixtures/u02_native_browser_server.py'), resolve(process.env.U02_BUILD || 'dist/client'), variant, evidence],
      { cwd: resolve('..'), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, QT_QPA_PLATFORM: 'offscreen' } });
    const next = fixtureMessages(child);
    // Resolve only on actual exit; a timeout remains an assertion failure below.
    const exited = new Promise(done => child.once('exit', done));
    let result = 'failed';
    let cleanup = 'failed';
    try { await withFixtureCleanup(async () => {
      const ready = publicFixtureMessage(await next(), 'ready');
      expect(ready.hidden).toBe(true);
      await page.addInitScript(() => {
        window.__observations = [];
        const fetch = window.fetch;
        window.fetch = (...args) => {
          window.__observations.push({ kind: 'fetch', url: new URL(String(args[0]), location.origin).href, method: args[1]?.method || 'GET', hashEmpty: location.hash === '' });
          return fetch(...args);
        };
        new PerformanceObserver(list => list.getEntries().forEach(() => window.__observations.push({ kind: 'resource', hashEmpty: location.hash === '' }))).observe({ entryTypes: ['resource'] });
      });
      child.stdin.write('issue\n');
      const launch = publicFixtureMessage(await next(), 'ticket');
      const redeemed = page.waitForResponse(response => response.url() === ready.origin + '/api/v1/auth/launch/redeem', { timeout: 10000 }).catch(() => null);
      const measured = page.waitForResponse(response => response.url() === ready.origin + '/api/v1/connections');
      await navigateToDashboard(page, launch.url);
      const response = await redeemed;
      // Boolean assertion prevents the disposable URL fragment appearing in failure output.
      expect(response !== null, 'bootstrap_redeem_missing').toBe(true);
      expect(response.status()).toBe(200);
      expect(await page.evaluate(() => location.hash === '')).toBe(true);
      await expect(page.getByRole('heading', { name: 'Tiếp tục thiết lập trên Windows' })).toBeVisible();
      const observations = await page.evaluate(() => window.__observations);
      expect(observations.every(value => value.hashEmpty)).toBe(true);
      const requests = observations.filter(value => value.kind === 'fetch');
      expect(requests[0]).toEqual({ kind: 'fetch', url: ready.origin + '/api/v1/auth/launch/redeem', method: 'POST', hashEmpty: true });
      for (const path of ['/api/v1/setup/status', '/api/v1/connections', '/api/v1/native/dialogs']) expect(requests.some(value => value.url === path || value.url === ready.origin + path)).toBe(true);
      const cookie = (await context.cookies()).find(value => value.name === 'tg_admin_session');
      expect(cookie?.httpOnly).toBe(true); expect(cookie?.sameSite).toBe('Strict');
      expect(await page.evaluate(() => document.cookie.includes('tg_admin_session'))).toBe(false);
      expect(await page.locator('input[type=password], input[name*=token], input[name*=secret]').count()).toBe(0);
      await expect(page.getByText(`Profile: ${ready.profile_id}`, { exact: false })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Kết nối bot trên Windows' })).toBeVisible();
      expect(await page.getByText('CHƯA BIẾT', { exact: true }).count()).toBeGreaterThan(0);
      const rows = await (await measured).json();
      expect(rows.length).toBe(6);
      expect(rows.some(row => row.state === 'unknown' && row.checked_at === null)).toBe(true);
      for (const row of rows) {
        expect(['unknown', 'checking', 'ready', 'degraded', 'disconnected']).toContain(row.state);
        if (row.state === 'ready' || row.state === 'degraded') expect(row.checked_at !== null).toBe(true);
      }
      // Await the actual pipe heartbeat before asking React to refresh availability.
      await expect.poll(async () => (await (await context.request.get(ready.origin + '/api/v1/native/dialogs')).json()).commands,
        { timeout: 10000 }).toEqual(['open_bot_dialog', 'open_connection_dialog', 'open_telegram_login']);
      await page.getByRole('button', { name: 'Kiểm tra lại', exact: true }).click();
      const button = page.getByRole('button', { name: 'Mở cấu hình AI trên Windows' });
      await expect(button).toBeEnabled({ timeout: 10000 });
      await expect(page.getByRole('button', { name: 'Đăng nhập Telegram trên Windows' })).toBeEnabled();
      await expect(page.getByRole('button', { name: 'Kết nối bot trên Windows' })).toBeEnabled();
      const queuedPromise = page.waitForResponse(value => value.url().endsWith('/api/v1/native/commands'));
      await button.click();
      const queued = await queuedPromise;
      const command = queued.request().postDataJSON();
      expect(command.payload_nonsecret).toEqual({}); expect(command.profile_id).toBe(ready.profile_id);
      expect(queued.request().headers()['x-csrf-token']?.length > 0).toBe(true);
      expect((await queued.json()).state).toBe('queued');
      const dialog = publicFixtureMessage(await next(), 'dialog');
      expect(dialog).toEqual({ type: 'dialog', count: 1, empty: true, launcher_visible: true });
      // Same authenticated request including original CSRF proves relay replay cannot open twice.
      const replayResponse = await context.request.post(ready.origin + '/api/v1/native/commands', { data: command, headers: { Origin: ready.origin, 'X-CSRF-Token': queued.request().headers()['x-csrf-token'] } });
      expect((await replayResponse.json()).code).toBe('native_request_replayed');
      await page.waitForTimeout(1200);
      child.stdin.write('status\n');
      expect(publicFixtureMessage(await next(), 'status')).toEqual({ type: 'status', count: 1, launcher_visible: true });
      if (variant === 'lf') {
        // Same real browser/session bridge also reaches the actual Telegram
        // dialog. Auth fields stay native and the old worker must exit first.
        await page.getByRole('button', { name: 'Kiểm tra lại', exact: true }).click();
        const telegramQueued = page.waitForResponse(value => value.url().endsWith('/api/v1/native/commands'));
        await page.getByRole('button', { name: 'Đăng nhập Telegram trên Windows' }).click();
        const telegramResponse = await telegramQueued;
        expect((await telegramResponse.json()).state).toBe('queued');
        expect(telegramResponse.request().postDataJSON().payload_nonsecret).toEqual({});
        expect(publicFixtureMessage(await next(), 'telegram')).toEqual({ type: 'telegram', empty: true, worker_stopped: true, retained_context: true });
        expect(publicFixtureMessage(await next(), 'telegram_resumed')).toEqual({ type: 'telegram_resumed', ready: true });
      }
      result = 'passed';
    }, async () => {
      if (!child.stdin.destroyed && child.exitCode === null && child.signalCode === null) {
        child.stdin.write('stop\n'); child.stdin.end();
      }
      const stopped = publicFixtureMessage(await next(), 'stopped');
      expect(stopped).toEqual({ type: 'stopped', clean: true, owned_handle_released: true });
      if (child.exitCode === null && child.signalCode === null) {
        let timeout;
        try { await Promise.race([exited, new Promise(done => { timeout = setTimeout(done, 5000); })]); }
        finally { clearTimeout(timeout); }
      }
      expect(child.exitCode).toBe(0);
      expect(child.signalCode).toBe(null);
      cleanup = 'passed';
    }); } finally {
      writeFileSync(resolve(evidence, `${variant}-result.json`), JSON.stringify({ variant, result: result === 'passed' && cleanup === 'passed' ? 'passed' : 'failed', cleanup }));
    }
  });
}
