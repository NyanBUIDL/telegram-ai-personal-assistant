import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

// Synthetic DTO/control fixtures only. This is not evidence of Windows provider health.
if (process.env.D02_CHROMIUM_EXECUTABLE) test.use({ launchOptions: { executablePath: process.env.D02_CHROMIUM_EXECUTABLE } });
else if (process.env.D02_BROWSER_CHANNEL) test.use({ channel: process.env.D02_BROWSER_CHANNEL });
const services = ['telegram_account', 'control_bot', 'chat_ai', 'embeddings', 'storage', 'runtime'];
const primary = ['Tổng quan', 'Kết nối', 'Nguồn Telegram', 'Tri thức', 'Công việc', 'Vận hành', 'Cài đặt & trợ giúp'];
async function fixture(page, options = {}) {
  const requests = [];
  const profile = { profile_id: 'synthetic-profile', owner_id: null, storage_backend: 'sqlite', setup_stage: 'ai_configured', version: 1 };
  const rows = services.map((service, index) => ({ service, state: index === 2 ? 'unknown' : index === 3 ? 'degraded' : options.unmeasuredReady && index === 4 ? 'ready' : 'disconnected', checked_at: index === 3 ? '2000-01-01T00:00:00Z' : null, code: 'synthetic', message: 'Synthetic observation', next_action: 'Kiểm tra trên Windows', capabilities: index === 2 ? ['metadata'] : [] }));
  if (options.readyAt) rows[4] = { ...rows[4], state: 'ready', checked_at: options.readyAt };
  await page.route('**/api/v1/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname; requests.push({ path, body: request.postDataJSON(), headers: request.headers() });
    let body;
    if (path.endsWith('/auth/session')) body = { authenticated: true, authority: options.management ? 'management' : 'setup_only', profile_id: profile.profile_id, owner_id: options.management ? '9007199254740993' : null, csrf_token: 'synthetic-csrf' };
    else if (path.endsWith('/setup/status')) body = { profile: { ...profile, profile_id: options.mismatch ? 'wrong-profile' : profile.profile_id }, connections: rows, stage_evidence_ids: {}, next_action: 'Mở kết nối trên Windows', disabled_capabilities: ['management'] };
    else if (path.endsWith('/connections')) body = options.malformed ? [{ ...rows[0], checked_at: 'not-a-time' }] : rows;
    else if (path.endsWith('/native/dialogs')) body = { profile_id: profile.profile_id, commands: options.extraCommand ? ['open_connection_dialog', 'runtime_start'] : ['open_connection_dialog'] };
    else if (path.endsWith('/native/commands')) {
      if (options.expired) return route.fulfill({ status: 401, json: { code: 'native_reopen_required' } });
      if (options.conflict) return route.fulfill({ status: 409, json: { code: 'native_dialog_unavailable' } });
      body = { operation_id: 'synthetic-operation', state: options.failed ? 'failed' : 'queued', progress: null, code: 'native_command_queued', message: options.failed ? 'Không sẵn sàng' : 'Queued', next_action: 'Return to Windows' };
    } else return route.fulfill({ status: 403, json: { code: 'unexpected_management_call' } });
    await route.fulfill({ json: body });
  });
  await page.goto('http://127.0.0.1:5177');
  return requests;
}
test('synthetic setup only: measured states, seven primary, native empty payload and CSRF queued semantics', async ({ page }) => {
  const requests = await fixture(page);
  await expect(page.getByRole('heading', { name: 'Tổng quan', exact: true })).toBeVisible();
  for (const label of primary) await expect(page.getByRole('navigation', { name: 'Điều hướng chính' }).getByRole('button', { name: label, exact: true })).toHaveCount(1);
  await expect(page.getByText('CHƯA BIẾT', { exact: true })).toBeVisible();
  await expect(page.getByText('DỮ LIỆU CŨ · SUY GIẢM', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Mở cấu hình AI trên Windows' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Yêu cầu đã xếp hàng trên Windows' })).toBeVisible();
  const command = requests.find(r => r.path.endsWith('/native/commands'));
  expect(command.body).toMatchObject({ name: 'open_connection_dialog', profile_id: 'synthetic-profile', payload_nonsecret: {} });
  expect(command.body.request_id).toMatch(/^[0-9a-f-]{36}$/i);
  expect(command.headers['x-csrf-token']).toBe('synthetic-csrf');
  expect(await page.locator('input[type=password]').count()).toBe(0);
  expect(requests.every(r => ['/api/v1/auth/session', '/api/v1/setup/status', '/api/v1/connections', '/api/v1/native/dialogs', '/api/v1/native/commands'].includes(r.path))).toBe(true);
});
test('synthetic ready with no checked_at remains unknown; other installed native commands do not break dialog availability', async ({ page }) => {
  await fixture(page, { unmeasuredReady: true, extraCommand: true });
  await expect(page.locator('.setup-service').filter({ hasText: 'Lưu trữ' })).toContainText('CHƯA BIẾT');
  await expect(page.getByRole('button', { name: 'Mở cấu hình AI trên Windows' })).toBeEnabled();
});
for (const width of [360, 390, 1280, 1440]) test(`synthetic setup geometry, type and keyboard at ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 }); await fixture(page);
  await expect(page.locator('.setup-services')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  for (const button of await page.getByRole('button').all()) {
    const box = await button.boundingBox(); expect(box.width).toBeGreaterThanOrEqual(44); expect(box.height).toBeGreaterThanOrEqual(44);
    await expect(button).toHaveCSS('font-family', /Darley Sans/);
  }
  await expect(page.locator('.setup-readiness')).toHaveCSS('border-top-width', '3px');
  await expect(page.locator('.setup-readiness')).toHaveCSS('box-shadow', 'rgb(9, 9, 9) 7px 7px 0px 0px');
  await expect(page.getByRole('heading', { name: 'Tổng quan', exact: true })).toHaveCSS('font-family', /Peter Obscure/);
  const help = page.getByRole('button', { name: 'Cài đặt & trợ giúp', exact: true }); await help.focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Cài đặt & trợ giúp', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Tổng quan', exact: true }).click();
  const evidence = resolve(process.env.ART_EVIDENCE_DIR || '../.test-temp/onboarding-art'); mkdirSync(evidence, { recursive: true });
  await page.screenshot({ path: resolve(evidence, `synthetic-setup-${width}.png`), fullPage: true });
});
test('synthetic management preserves advanced feature navigation and exact owner ID', async ({ page }) => {
  await fixture(page, { management: true });
  await page.getByText('Chức năng nâng cao', { exact: true }).click();
  const advanced = page.getByRole('navigation', { name: 'Điều hướng nâng cao' });
  for (const label of ['Policy Engine', 'AI & RAG', 'Local Models', 'Bộ nhớ & lưu trữ', 'Audit Log', 'Bảo mật', 'Chức năng Telegram']) await expect(advanced.getByRole('button', { name: label, exact: true })).toBeVisible();
  await expect(page.getByText('Owner 9007199254740993', { exact: true })).toBeVisible();
});
test('synthetic mismatched profile is unavailable and cannot request native commands', async ({ page }) => {
  await fixture(page, { mismatch: true });
  await expect(page.getByRole('alert')).toContainText('không khả dụng');
  await expect(page.getByRole('button', { name: 'Mở cấu hình AI trên Windows' })).toBeDisabled();
});
test('synthetic expired session gives reopen guidance without automatic reload', async ({ page }) => {
  const requests = await fixture(page, { expired: true });
  await expect(page.getByRole('button', { name: 'Mở cấu hình AI trên Windows' })).toBeEnabled();
  const initialSessionReads = requests.filter(request => request.path.endsWith('/auth/session')).length;
  await page.getByRole('button', { name: 'Mở cấu hình AI trên Windows' }).click();
  await expect(page.getByRole('heading', { name: 'Mở từ ứng dụng Windows' })).toBeVisible();
  expect(requests.filter(request => request.path.endsWith('/auth/session'))).toHaveLength(initialSessionReads);
});
test('synthetic malformed contract refuses native controls without a mock fallback', async ({ page }) => {
  await fixture(page, { malformed: true });
  await expect(page.getByRole('alert')).toContainText('không khả dụng');
  await expect(page.getByRole('button', { name: 'Mở cấu hình AI trên Windows' })).toBeDisabled();
});
for (const state of ['conflict', 'failed']) test(`synthetic native ${state} remains actionable and unverified`, async ({ page }) => {
  await fixture(page, { [state]: true });
  await page.getByRole('button', { name: 'Mở cấu hình AI trên Windows' }).click();
  await expect(page.getByRole('status').filter({ hasText: state === 'conflict' ? 'Mở lại ứng dụng Windows' : 'failed: Không sẵn sàng' })).toBeVisible();
  await expect(page.getByRole('status')).not.toContainText('kết nối thành công');
});

test('synthetic freshness crosses 59 to 60 seconds while measured refresh is delayed then fails', async ({ page }) => {
  const now = new Date('2026-10-06T05:00:00Z');
  await page.clock.install({ time: now });
  await page.clock.pauseAt(new Date(now.getTime() + 1000));
  await fixture(page, { readyAt: new Date(now.getTime() - 58000).toISOString() });
  const storage = page.locator('.setup-service').filter({ hasText: 'Lưu trữ' });
  await expect(storage.locator('.badge')).toHaveClass(/badge--success/);
  let releaseRefresh;
  const delayed = new Promise(resolve => { releaseRefresh = resolve; });
  await page.route('**/api/v1/connections', async route => { await delayed; await route.fulfill({ status: 503, json: { code: 'synthetic_delayed_failure' } }); });
  await page.getByRole('button', { name: 'Kiểm tra lại', exact: true }).click();
  await page.clock.runFor(1000);
  await expect(storage.locator('.badge')).not.toHaveClass(/badge--success/);
  await expect(storage).toContainText('DỮ LIỆU CŨ · SẴN SÀNG');
  releaseRefresh();
  await expect(page.getByRole('alert')).toContainText('không khả dụng');
  await expect(storage.locator('.badge')).not.toHaveClass(/badge--success/);
});
for (const event of ['focus', 'visibilitychange']) test(`synthetic freshness recomputes immediately on ${event} before delayed refresh resolves`, async ({ page }) => {
  const now = new Date('2026-10-06T05:00:00Z');
  await page.clock.install({ time: now });
  await page.clock.pauseAt(new Date(now.getTime() + 1000));
  await fixture(page, { readyAt: new Date(now.getTime() - 58000).toISOString() });
  const storage = page.locator('.setup-service').filter({ hasText: 'Lưu trữ' });
  await expect(storage.locator('.badge')).toHaveClass(/badge--success/);
  let releaseRefresh;
  const delayed = new Promise(resolve => { releaseRefresh = resolve; });
  await page.route('**/api/v1/connections', async route => { await delayed; await route.fulfill({ status: 503, json: { code: 'synthetic_focus_failure' } }); });
  // Advance wall time without executing timers: returning from suspension must
  // withdraw stale readiness before a delayed network response can arrive.
  await page.clock.setSystemTime(new Date(now.getTime() + 2000));
  await page.evaluate(event => { (event === 'focus' ? window : document).dispatchEvent(new Event(event)); }, event);
  await expect(storage.locator('.badge')).not.toHaveClass(/badge--success/);
  await expect(storage).toContainText('DỮ LIỆU CŨ · SẴN SÀNG');
  releaseRefresh();
  await expect(page.getByRole('alert')).toContainText('không khả dụng');
});
