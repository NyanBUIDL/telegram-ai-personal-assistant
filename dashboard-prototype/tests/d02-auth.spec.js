import { expect, test } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { resolve } from 'node:path';
import { mkdirSync } from 'node:fs';

const pythonOverride = process.env.D02_PYTHON;
const evidenceOverride = process.env.ART_EVIDENCE_DIR;
for (const value of [pythonOverride, evidenceOverride]) {
  if (value !== undefined && (!value || value.length > 4096 || /[\r\n\0]/.test(value))) throw new Error('Invalid internal D02 test path override');
}
const python = pythonOverride || resolve('../.venv-q01/Scripts/python.exe');
const evidenceDirectory = resolve(evidenceOverride || '../.test-temp');
mkdirSync(evidenceDirectory, { recursive: true });

let child, origin;
const messages = [], waiters = [];
function nextMessage() {
  if (messages.length) return Promise.resolve(messages.shift());
  return new Promise((resolveMessage, reject) => {
    const timeout = setTimeout(() => reject(new Error('Synthetic native fixture did not reply')), 15000);
    waiters.push((message) => { clearTimeout(timeout); resolveMessage(message); });
  });
}
async function ticket() {
  child.stdin.write('issue\n');
  const result = await nextMessage();
  expect(result.type).toBe('ticket');
  return result.url;
}

test.describe.configure({ mode: 'serial' });
test.beforeAll(async () => {
  child = spawn(python, ['-u', resolve('../tests/fixtures/d02_browser_server.py'), resolve('dist/client')], {
    cwd: resolve('..'), stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
  });
  createInterface({ input: child.stdout }).on('line', (line) => {
    let result;
    try { result = JSON.parse(line); } catch { return; }
    if (waiters.length) waiters.shift()(result); else messages.push(result);
  });
  // Do not persist stdout (it contains disposable synthetic launch tickets).
  const ready = await nextMessage();
  expect(ready.type).toBe('ready');
  origin = ready.origin;
});
test.afterAll(async () => {
  child?.stdin.write('stop\n');
  if (child && child.exitCode === null) {
    await new Promise((done) => { child.once('exit', done); setTimeout(() => { if (child.exitCode === null) child.kill(); done(); }, 5000); });
  }
});

test('earliest inline scrub precedes fetch/resources and issues protected setup session', async ({ page, context }) => {
  await page.addInitScript(() => {
    window.__tgFetchHashes = [];
    const original = window.fetch;
    window.fetch = (...args) => { window.__tgFetchHashes.push(location.hash); return original(...args); };
    window.__tgResourceHashes = [];
    new PerformanceObserver((entries) => { for (const entry of entries.getEntries()) if (entry.entryType === 'resource') window.__tgResourceHashes.push(location.hash); }).observe({ entryTypes: ['resource'] });
  });
  const launch = await ticket();
  const redeem = page.waitForResponse((response) => response.url().endsWith('/api/v1/auth/launch/redeem'));
  await page.goto(launch);
  expect((await redeem).status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'Tiếp tục thiết lập trên Windows' })).toBeVisible();
  expect(await page.evaluate(() => location.hash)).toBe('');
  expect(await page.evaluate(() => window.__tgFetchHashes)).toEqual(['']);
  expect((await page.evaluate(() => window.__tgResourceHashes)).every((hash) => hash === '')).toBe(true);
  expect(await page.evaluate(() => document.cookie)).not.toContain('tg_admin_session');
  const cookie = (await context.cookies()).find((value) => value.name === 'tg_admin_session');
  expect(cookie.httpOnly).toBe(true);
  expect(cookie.sameSite).toBe('Strict');
  const secret = new URL(launch).hash.slice('#launch_ticket='.length);
  expect(await page.content()).not.toContain(secret);
  for (const width of [360, 390, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByRole('button', { name: 'Đăng xuất', exact: true })).toBeVisible();
    await page.screenshot({ path: resolve(evidenceDirectory, `d02-browser-${width}.png`), fullPage: true });
  }
  await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Mở từ ứng dụng Windows' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Mở từ ứng dụng Windows' })).toBeVisible();
});

test('a consumed ticket cannot replay in a fresh browser context', async ({ browser }) => {
  const url = await ticket();
  const first = await browser.newContext();
  const page1 = await first.newPage();
  await page1.goto(url);
  await expect(page1.getByRole('heading', { name: 'Tiếp tục thiết lập trên Windows' })).toBeVisible();
  const second = await browser.newContext();
  const page2 = await second.newPage();
  const response = page2.waitForResponse((value) => value.url().endsWith('/api/v1/auth/launch/redeem'));
  await page2.goto(url);
  expect((await response).status()).toBe(401);
  await expect(page2.getByRole('alert')).toContainText('đã hết hạn hoặc đã dùng');
  expect(await page2.evaluate(() => location.hash)).toBe('');
  expect((await second.cookies()).some((value) => value.name === 'tg_admin_session')).toBe(false);
  await first.close(); await second.close();
});

test('a real 30-second ticket expires without creating a cookie', async ({ page, context }) => {
  test.setTimeout(45000);
  const url = await ticket();
  await new Promise((done) => setTimeout(done, 31000));
  const response = page.waitForResponse((value) => value.url().endsWith('/api/v1/auth/launch/redeem'));
  await page.goto(url);
  expect((await response).status()).toBe(401);
  await expect(page.getByRole('alert')).toContainText('đã hết hạn hoặc đã dùng');
  expect((await context.cookies()).some((value) => value.name === 'tg_admin_session')).toBe(false);
});

test('foreign Origin/Host and HTTP mint are denied without consuming the ticket', async ({ request }) => {
  const url = await ticket();
  const raw = new URL(url).hash.slice('#launch_ticket='.length);
  for (const headers of [{ Origin: 'https://foreign.invalid' }, { Origin: origin, Host: 'localhost:9999' }, {}]) {
    expect((await request.post(origin + '/api/v1/auth/launch/redeem', { headers, data: { ticket: raw } })).status()).toBe(403);
  }
  expect((await request.post(origin + '/api/v1/auth/launch/issue', { headers: { Origin: origin } })).status()).toBe(404);
  expect((await request.post(origin + '/api/v1/auth/launch/redeem', { headers: { Origin: origin }, data: { ticket: raw } })).status()).toBe(200);
});

test('malformed and duplicate fragments are erased without a redeem request', async ({ page }) => {
  for (const suffix of ['#launch_ticket=bad', '#launch_ticket=' + 'a'.repeat(43) + '&launch_ticket=' + 'b'.repeat(43)]) {
    const requests = [];
    const handler = (request) => { if (request.url().endsWith('/api/v1/auth/launch/redeem')) requests.push(request.url()); };
    page.on('request', handler);
    await page.goto(origin + '/' + suffix);
    await expect(page.getByRole('alert')).toContainText('không hợp lệ');
    expect(await page.evaluate(() => location.hash)).toBe('');
    expect(requests).toHaveLength(0);
    page.off('request', handler);
  }
});
