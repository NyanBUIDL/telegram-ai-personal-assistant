import { test, expect } from '@playwright/test';

// Synthetic transport fixtures; render the production components and hook.
if (process.env.D02_CHROMIUM_EXECUTABLE) test.use({ launchOptions: { executablePath: process.env.D02_CHROMIUM_EXECUTABLE } });
else if (process.env.D02_BROWSER_CHANNEL) test.use({ channel: process.env.D02_BROWSER_CHANNEL });
const now = new Date('2026-10-10T00:01:00Z');
const services = ['runtime', 'telegram_account', 'control_bot', 'chat_ai', 'embeddings', 'storage'];
const profile = id => ({ profile_id: id, owner_id: null, storage_backend: 'sqlite', setup_stage: 'ai_configured', version: 1 });
const rows = checked_at => services.map(service => ({ service, state: 'ready', checked_at, code: 'synthetic', message: `Observed ${service}`, next_action: 'Kiểm tra trên Windows', capabilities: [] }));
async function harness(page, component, props = {}) {
  page.on('pageerror', error => { throw error; });
  await page.route('**/__status_test', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="vi"><head><meta charset="utf-8"><link rel="stylesheet" href="/src/styles.css"></head><body><div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh';
    RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type; window.__vite_plugin_react_preamble_installed__ = true;
    const {default: React} = await import('/node_modules/.vite/deps/react.js');
    const {default: ReactDOM} = await import('/node_modules/.vite/deps/react-dom_client.js');
    const {SetupReadiness} = await import('/src/views/OnboardingView.jsx');
    const {OverviewView} = await import('/src/views/OverviewView.jsx');
    const {useResource} = await import('/src/hooks.js');
    const e = React.createElement; const root = ReactDOM.createRoot(document.getElementById('root'));
    window.pendingLoads = [];
    function Resource({id, refreshKey = 0}) {
      const resource = useResource(() => new Promise((resolve, reject) => window.pendingLoads.push({id, resolve, reject})), [id], refreshKey);
      window.reloadResource = resource.reload;
      return e('section', null, e('p', {id:'value'}, resource.data ?? 'empty'), e('p', {id:'error'}, resource.error?.message ?? ''), e('p', {id:'loading'}, String(resource.loading)));
    }
    const components = {SetupReadiness, OverviewView, Resource};
    window.renderStatus = props => root.render(e(components[${JSON.stringify(component)}], {...props, onNavigate:()=>{}, onReview:()=>{}}));
    window.unmountStatus = () => root.unmount();
    window.renderStatus(${JSON.stringify(props)});
  </script></body></html>` }));
  await page.goto('http://127.0.0.1:5177/__status_test');
  await page.waitForFunction(() => typeof window.renderStatus === 'function');
}
async function setupFixture(page, checkedAt = now.toISOString()) {
  const state = { id: 'profile-a', failed: false, checkedAt };
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (state.failed) return route.fulfill({ status: 503, json: { code: 'synthetic_unavailable' } });
    const connections = rows(state.checkedAt);
    const body = path.endsWith('/setup/status') ? { profile: profile(state.id), connections, stage_evidence_ids: {}, next_action: 'Tiếp tục trên Windows', disabled_capabilities: ['management'] }
      : path.endsWith('/connections') ? connections
      : { profile_id: state.id, commands: ['open_connection_dialog'] };
    return route.fulfill({ json: body });
  });
  return state;
}
test('failed same-profile refresh retains measured rows but withdraws readiness and commands', async ({ page }) => {
  await page.clock.install({ time: now });
  const state = await setupFixture(page);
  await harness(page, 'SetupReadiness', { session: { profile_id: state.id } });
  const storage = page.locator('.setup-service').filter({ hasText: 'Lưu trữ' });
  await expect(storage.locator('.badge')).toHaveClass(/badge--success/);
  state.failed = true;
  await page.getByRole('button', { name: 'Kiểm tra lại', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('không khả dụng');
  await expect(storage).toContainText('Observed storage');
  await expect(storage).toContainText('DỮ LIỆU CŨ');
  await expect(storage.locator('.badge')).not.toHaveClass(/badge--success/);
  await expect(page.getByRole('button', { name: 'Mở cấu hình AI trên Windows' })).toBeDisabled();
  state.failed = false;
  await page.getByRole('button', { name: 'Kiểm tra lại', exact: true }).click();
  await expect(storage.locator('.badge')).toHaveClass(/badge--success/);
});
test('profile change cannot retain or revive a former profile while replacement fails', async ({ page }) => {
  const state = await setupFixture(page, new Date().toISOString());
  await harness(page, 'SetupReadiness', { session: { profile_id: state.id } });
  await expect(page.getByText(/Profile: profile-a/)).toBeVisible();
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  await page.route('**/api/v1/connections', async route => { await delayed; await route.fulfill({ json: rows(new Date().toISOString()) }); });
  await page.getByRole('button', { name: 'Kiểm tra lại', exact: true }).click();
  let releaseNew;
  const newRequest = new Promise(resolve => { releaseNew = resolve; });
  await page.route('**/api/v1/setup/status', async route => { await newRequest; await route.fulfill({ status: 503, json: { code: 'synthetic_profile_unavailable' } }); });
  state.id = 'profile-b'; state.failed = true;
  await page.route('**/api/v1/native/dialogs', async route => { await newRequest; await route.fulfill({ json: { profile_id: 'profile-b', commands: [] } }); });
  await page.evaluate(() => window.renderStatus({ session: { profile_id: 'profile-b' } }));
  await expect(page.getByText(/Profile: profile-a/)).toHaveCount(0);
  await expect(page.getByText(/Observed storage/)).toHaveCount(0);
  release();
  releaseNew();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByText(/Observed storage/)).toHaveCount(0);
  await expect(page.locator('.badge--success')).toHaveCount(0);
});

test('Overview expires at 60s and retains failed refresh with stale status until a successful retry', async ({ page }) => {
  await page.clock.install({ time: now }); await page.clock.pauseAt(now);
  let failed = false;
  let checkedAt = new Date(now.getTime() - 59000).toISOString();
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/overview')) return route.fulfill(failed ? { status: 503, json: {} } : { json: { messages: 42, health: [{ component: 'runtime', status: 'ok', checked_at: checkedAt, latency_ms: 0 }] } });
    return route.fulfill({ json: { items: [], history: [] } });
  });
  await harness(page, 'OverviewView');
  await expect(page.locator('.pulse-panel .service-light--online')).toHaveCount(1);
  await expect(page.locator('.service-row strong')).toContainText('0 ms');
  await page.clock.setSystemTime(new Date(now.getTime() + 1000));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.locator('.pulse-panel')).toContainText('DỮ LIỆU CŨ');
  await expect(page.locator('.service-light--online')).toHaveCount(0);
  failed = true;
  await page.evaluate(() => window.renderStatus({ refreshKey: 1 }));
  await expect(page.getByText('DỮ LIỆU CŨ · Tổng quan chưa cập nhật được.', { exact: false })).toBeVisible();
  await expect(page.locator('.stat-block').first().locator('strong')).toHaveText('42');
  checkedAt = new Date(now.getTime() + 1000).toISOString();
  failed = false;
  let release;
  const delayed = new Promise(resolve => { release = resolve; });
  await page.route('**/api/v1/overview', async route => { await delayed; await route.fulfill({ json: { messages: 43, health: [{ component: 'runtime', status: 'ok', checked_at: checkedAt }] } }); });
  await page.evaluate(() => window.renderStatus({ refreshKey: 2 }));
  await expect(page.getByText('DỮ LIỆU CŨ · Tổng quan chưa cập nhật được.', { exact: false })).toBeVisible();
  await expect(page.locator('.service-light--online')).toHaveCount(0);
  release();
  await expect(page.locator('.service-light--online')).toHaveCount(1);
  await expect(page.locator('.stat-block').first().locator('strong')).toHaveText('43');
});

test('legacy invalid Overview timestamps render unknown without crashing', async ({ page }) => {
  await page.clock.setFixedTime(now);
  await page.route('**/api/v1/**', route => {
    const body = new URL(route.request().url()).pathname.endsWith('/overview')
      ? { health: [null, 'bad', new Date(now.getTime() + 1000).toISOString()].map((checked_at, index) => ({ component: `service-${index}`, status: 'ok', checked_at })) }
      : { items: [], history: [] };
    return route.fulfill({ json: body });
  });
  await harness(page, 'OverviewView');
  await expect(page.locator('.pulse-panel .service-row')).toHaveCount(3);
  await expect(page.locator('.service-light--online')).toHaveCount(0);
  await expect(page.locator('.service-row strong').getByText('CHƯA BIẾT', { exact: true })).toHaveCount(3);
});

test('Overview labels retained telemetry, actions and audit as stale when their refresh fails', async ({ page }) => {
  let failed = false;
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/overview')) return route.fulfill({ json: { health: [] } });
    if (failed) return route.fulfill({ status: 503, json: {} });
    const body = path.endsWith('/audit') ? { items: [{ id: 1, outcome: 'uncertain', action: 'retained_audit', occurred_at: now.toISOString() }] }
      : path.endsWith('/workers') ? { history: [{ id: 1, collected_at: now.toISOString(), rss_bytes: 1024 }] }
      : { items: [{ action_id: 'retained-action', action_type: 'synthetic', expires_at: now.toISOString(), chat_id: '-9007199254740993' }] };
    return route.fulfill({ json: body });
  });
  await harness(page, 'OverviewView');
  await expect(page.getByText('retained-action', { exact: true })).toBeVisible();
  await expect(page.getByText('Retained Audit', { exact: true })).toBeVisible();
  failed = true;
  await page.evaluate(() => window.renderStatus({ refreshKey: 1 }));
  for (const label of ['Telemetry', 'Hành động chờ', 'Audit']) await expect(page.getByText(`DỮ LIỆU CŨ · ${label}`, { exact: true })).toBeVisible();
  await expect(page.getByText('retained-action', { exact: true })).toBeVisible();
  await expect(page.getByText('-9007199254740993', { exact: true })).toBeVisible();
  await expect(page.getByText('Retained Audit', { exact: true })).toBeVisible();
});
for (const event of ['focus', 'visibilitychange']) test(`sleep recomputes readiness on ${event} before refresh returns`, async ({ page }) => {
  await page.clock.install({ time: now }); await page.clock.pauseAt(now);
  await setupFixture(page, new Date(now.getTime() - 59000).toISOString());
  await harness(page, 'SetupReadiness', { session: { profile_id: 'profile-a' } });
  const storage = page.locator('.setup-service').filter({ hasText: 'Lưu trữ' });
  await expect(storage.locator('.badge')).toHaveClass(/badge--success/);
  await page.route('**/api/v1/connections', () => {});
  await page.clock.setSystemTime(new Date(now.getTime() + 1000));
  await page.evaluate(event => (event === 'focus' ? window : document).dispatchEvent(new Event(event)), event);
  await expect(storage).toContainText('DỮ LIỆU CŨ');
  await expect(storage.locator('.badge')).not.toHaveClass(/badge--success/);
});
test('future checked_at cannot turn setup green', async ({ page }) => {
  await page.clock.install({ time: now });
  await setupFixture(page, new Date(now.getTime() + 60000).toISOString());
  await harness(page, 'SetupReadiness', { session: { profile_id: 'profile-a' } });
  await expect(page.getByText(/Profile: profile-a/)).toBeVisible();
  await expect(page.locator('.badge--success')).toHaveCount(0);
  await expect(page.locator('.setup-service').first()).toContainText('CHƯA BIẾT');
});
test('empty Overview health and absent metrics stay unknown; warning outcomes never green', async ({ page }) => {
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith('/overview') ? { health: [], messages: 0, runtime: null }
      : path.endsWith('/audit') ? { items: ['completed_with_warning', 'uncertain'].map((outcome, id) => ({ id, outcome, action: 'synthetic', occurred_at: now.toISOString() })) }
      : { items: [], history: [] };
    return route.fulfill({ json: body });
  });
  await harness(page, 'OverviewView');
  await expect(page.getByText('Nhịp hệ thống', { exact: true })).toBeVisible();
  await expect(page.getByText('CONNECTED', { exact: true })).toHaveCount(0);
  await expect(page.getByText('ONLINE', { exact: true })).toHaveCount(0);
  await expect(page.locator('.pulse-panel .badge--success')).toHaveCount(0);
  await expect(page.locator('.event-row .badge--success')).toHaveCount(0);
  await expect(page.locator('.stat-block').first().locator('strong')).toHaveText('0');
  await expect(page.locator('.stat-block').nth(1).locator('strong')).toHaveText('—');
  await expect(page.locator('.live-resource-strip').getByText('0 B', { exact: true })).toHaveCount(0);
});
test('resource reload retains same-key errors, rejects older reloads and clears former-key data', async ({ page }) => {
  await harness(page, 'Resource', { id: '-9007199254740993' });
  await page.waitForFunction(() => window.pendingLoads.length === 1);
  await page.evaluate(() => window.pendingLoads[0].resolve('chat-a'));
  await expect(page.locator('#value')).toHaveText('chat-a');
  await page.evaluate(() => { window.reloadResource(); });
  await page.evaluate(() => window.pendingLoads[1].reject(new Error('refresh unavailable')));
  await expect(page.locator('#error')).toHaveText('refresh unavailable');
  await expect(page.locator('#value')).toHaveText('chat-a');
  await page.evaluate(() => { window.reloadResource(); window.oldReload = window.reloadResource; window.renderStatus({ id: '-9007199254740995' }); });
  await page.waitForFunction(() => window.pendingLoads.length === 4);
  await expect(page.locator('#value')).toHaveText('empty');
  await page.evaluate(() => window.pendingLoads[3].resolve('chat-b'));
  await expect(page.locator('#value')).toHaveText('chat-b');
  await page.evaluate(() => window.pendingLoads[2].resolve('late chat-a'));
  await expect(page.locator('#value')).toHaveText('chat-b');
  await page.evaluate(async () => { window.oldReloadResult = await window.oldReload(); });
  expect(await page.evaluate(() => window.pendingLoads.length)).toBe(4);
  expect(await page.evaluate(() => window.oldReloadResult)).toBeNull();
});
test('latest reload wins, refreshKey keeps data, unmounted reload cannot start another load', async ({ page }) => {
  await harness(page, 'Resource', { id: 'page-1' });
  await page.waitForFunction(() => window.pendingLoads.length === 1);
  await page.evaluate(() => window.pendingLoads[0].resolve('initial'));
  await expect(page.locator('#value')).toHaveText('initial');
  await page.evaluate(() => window.renderStatus({ id: 'page-1', refreshKey: 1 }));
  await page.waitForFunction(() => window.pendingLoads.length === 2);
  await expect(page.locator('#value')).toHaveText('initial');
  await page.evaluate(() => { window.reloadResource(); });
  await page.evaluate(() => window.pendingLoads[2].resolve('newest'));
  await expect(page.locator('#value')).toHaveText('newest');
  await page.evaluate(() => window.pendingLoads[1].resolve('late refresh'));
  await expect(page.locator('#value')).toHaveText('newest');
  await page.evaluate(() => { window.reloadResource(); window.unmountStatus(); });
  await page.evaluate(async () => { window.pendingLoads[3].resolve('after unmount'); window.unmountedResult = await window.reloadResource(); });
  expect(await page.evaluate(() => window.pendingLoads.length)).toBe(4);
  expect(await page.evaluate(() => window.unmountedResult)).toBeNull();
});
