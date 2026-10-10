import { test, expect } from '@playwright/test';

// Synthetic presentation only; these fixtures cannot verify U03 authority or delivery.
if (process.env.D02_CHROMIUM_EXECUTABLE) test.use({ launchOptions: { executablePath: process.env.D02_CHROMIUM_EXECUTABLE } });
const A = '-1009007199254740993';
const B = '-1002';
const C = '-1003';
const previewAction = (chat_id = A) => ({ action_id: 'fv1-' + 'a'.repeat(32), action_type: 'enable_group_learning', chat_id, status: 'pending', payload: { first_source_preview: '8d42bf4162e544c5847515a5e80b1952', limit: 1000 }, preview: 'Nguồn duy nhất; đồng bộ tối đa 1000 tin. LOCAL ONLY; chưa thực thi.', reason: null, expires_at: new Date(Date.now() + 300000).toISOString() });
const operation = (state = 'running', progress = null) => ({ operation_id: '41', state, progress, code: 'synthetic', message: 'Kết quả backend', next_action: 'Kiểm tra nguồn' });
async function fixture(page, options = {}) {
  if (options.realtime) await page.addInitScript(() => {
    window.__fixtureStreams = [];
    window.EventSource = class {
      constructor(url) { this.url = url; this.listeners = {}; this.closed = false; window.__fixtureStreams.push(this); }
      addEventListener(name, callback) { this.listeners[name] = callback; }
      close() { this.closed = true; this.listeners = {}; this.onopen = null; this.onerror = null; }
    };
  });
  const requests = [];
  const state = {
    status: { profile_id: 'synthetic-profile', source_id: options.selected ? A : null, learning_operation: options.learning || null, answer_operation: null, bot_username: options.bot || null, test_available: Boolean(options.bot), code: 'selection_required', message: 'Chọn một nguồn; chưa cấp quyền.', next_action: 'Xem quyền trước khi học' },
    allowed: options.allowed ?? true,
    jobs: options.jobs || [],
    action: previewAction(),
    decisions: [],
  };
  const services = ['telegram_account', 'control_bot', 'chat_ai', 'embeddings', 'storage', 'runtime'].map(service => ({ service, state: 'unknown', checked_at: null, code: 'synthetic', message: 'Chưa kiểm tra', next_action: null, capabilities: [] }));
  await page.route('**/api/v1/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    requests.push({ path, method: request.method(), body: request.postDataJSON(), headers: request.headers(), search: url.search });
    let body;
    if (path.endsWith('/auth/session')) body = { authenticated: true, authority: options.setupOnly ? 'setup_only' : 'management', profile_id: 'synthetic-profile', owner_id: options.setupOnly ? null : '9007199254740993', csrf_token: 'synthetic-csrf' };
    else if (path.endsWith('/setup/status')) body = { profile: { profile_id: 'synthetic-profile', owner_id: options.setupOnly ? null : '9007199254740993', storage_backend: 'sqlite', setup_stage: options.setupOnly ? 'ai_configured' : 'owner_paired', version: 1 }, connections: services, stage_evidence_ids: {}, next_action: options.aiSkip ? 'AI đã bỏ qua; chức năng giới hạn' : 'Chọn nguồn', disabled_capabilities: options.aiSkip ? ['chat_ai', 'embeddings'] : [] };
    else if (path.endsWith('/connections')) body = services;
    else if (path.endsWith('/native/dialogs')) body = { profile_id: 'synthetic-profile', commands: [] };
    else if (path.endsWith('/onboarding/first-source')) { if (state.statusGate) await state.statusGate; body = state.status; }
    else if (path.endsWith('/onboarding/first-source/preview')) { if (state.previewGate) await state.previewGate; body = state.action; }
    else if (path.endsWith('/onboarding/source-selection')) { state.status = { ...state.status, source_id: request.postDataJSON().source_id }; body = state.status; }
    else if (path.endsWith('/groups')) body = { items: [A, B, C].map((chat_id, index) => ({ chat_id, title: ['Nguồn A tiếng Việt rất dài để kiểm tra xuống dòng an toàn', 'Nguồn B', 'Nguồn C'][index], chat_type: index === 1 ? 'channel' : 'supergroup', policy: { allowed: false } })), total: 3, page: 1, page_size: 10 };
    else if (path.startsWith('/api/v1/groups/')) body = { chat_id: decodeURIComponent(path.split('/').at(-1)), policy: { allowed: state.allowed } };
    else if (path.endsWith('/learning-jobs')) body = { items: state.jobs };
    else if (/\/pending-actions\/[^/]+\/(confirm|cancel)$/.test(path)) { state.decisions.push(path); body = { status: path.endsWith('/confirm') ? 'confirmed' : 'cancelled' }; }
    else if (path.endsWith('/knowledge/sources')) body = { items: [], total: 0, summary: { total_sources: 3, auto_knowledge_sources: 0 }, status_counts: { not_learned: 3 } };
    else if (path.endsWith('/knowledge/enable-all-action')) body = { ...previewAction(), action_id: 'advanced-action', action_type: 'enable_all_knowledge', chat_id: null };
    else return route.fulfill({ status: 403, json: { detail: 'Synthetic unavailable' } });
    await route.fulfill({ json: body });
  });
  await page.goto('http://127.0.0.1:5177');
  const emitSnapshot = async value => page.evaluate(value => {
    for (const stream of window.__fixtureStreams) if (!stream.closed) {
      stream.onopen?.();
      stream.listeners['dashboard-snapshot']?.(new MessageEvent('dashboard-snapshot', { data: JSON.stringify(value) }));
    }
  }, value);
  if (options.realtime) await expect.poll(() => page.evaluate(() => window.__fixtureStreams.filter(stream => !stream.closed).length)).toBe(1);
  return { requests, state, emitSnapshot, guide: page.getByRole('region', { name: 'Nguồn đầu tiên' }) };
}
for (const width of [360, 390, 1280, 1440]) test.describe(`first value ${width}`, () => {
  test.beforeEach(async ({ page }) => page.setViewportSize({ width, height: 900 }));
  test('test_pick_one_source_confirm', async ({ page }) => {
    const { guide, requests } = await fixture(page);
    await expect(guide).toBeVisible();
    await guide.getByLabel('Tìm nguồn').fill('Nguồn');
    await guide.getByLabel('Loại nguồn').selectOption('channel');
    await expect.poll(() => requests.some(row => row.search.includes('chat_type=channel') && row.search.includes('query=Ngu'))).toBe(true);
    await guide.getByLabel('Chọn một nguồn').selectOption(A);
    const save = guide.getByRole('button', { name: 'Lưu nguồn đã chọn' }); await save.focus(); await page.keyboard.press('Enter');
    await expect(guide.getByText('Chưa có preview quyền gắn với nguồn đã chọn.')).toBeVisible();
    await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled();
    const writes = requests.filter(row => row.method === 'POST');
    expect(writes).toHaveLength(1); expect(writes[0].body).toEqual({ source_id: A }); expect(writes[0].headers['x-csrf-token']).toBe('synthetic-csrf');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const control of await guide.locator('button, input, select').all()) { const box = await control.boundingBox(); expect(box.height).toBeGreaterThanOrEqual(44); await expect(control).toHaveCSS('font-family', /Darley Sans/); }
    await expect(guide).toHaveCSS('border-top-width', '3px'); await expect(guide).toHaveCSS('box-shadow', 'rgb(9, 9, 9) 7px 7px 0px 0px');
  });
  test('guided preview uses explicit canonical POST and existing keyboard review confirmation', async ({ page }) => {
    const { guide, requests, state } = await fixture(page, { selected: true, allowed: false });
    state.action.reason = 'Owner xem lại quyền thực tế trước khi xác nhận.';
    const preview = guide.getByRole('button', { name: 'Xem và xác nhận quyền' });
    await expect(preview).toBeEnabled(); expect(requests.filter(row => row.method === 'POST')).toHaveLength(0);
    const observations = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length;
    await preview.focus(); await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog'); await expect(dialog).toBeVisible(); await expect(dialog).toContainText(A); await expect(dialog).toContainText(state.action.preview); await expect(dialog).toContainText(state.action.reason);
    expect(requests.filter(row => row.path === '/api/v1/onboarding/first-source')).toHaveLength(observations);
    expect(requests.filter(row => row.method === 'POST')).toEqual([expect.objectContaining({ path: '/api/v1/onboarding/first-source/preview', body: { source_id: A }, headers: expect.objectContaining({ 'x-csrf-token': 'synthetic-csrf' }) })]);
    expect(state.decisions).toHaveLength(0); await expect(guide.getByRole('link')).toHaveCount(0);
    for (const button of await dialog.getByRole('button').all()) expect((await button.boundingBox()).height).toBeGreaterThanOrEqual(44);
    await dialog.getByRole('button', { name: 'Đóng', exact: true }).focus(); await page.keyboard.press('Shift+Tab'); await expect(dialog.getByRole('button', { name: 'Xác nhận thực thi' })).toBeFocused(); await page.keyboard.press('Tab'); await expect(dialog.getByRole('button', { name: 'Đóng', exact: true })).toBeFocused();
    await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0); await expect(preview).toBeFocused();
    await page.keyboard.press('Enter'); await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Xác nhận thực thi' }).focus(); await page.keyboard.press('Enter'); await expect(dialog).toHaveCount(0);
    expect(state.decisions).toEqual([`/api/v1/pending-actions/${state.action.action_id}/confirm`]);
    expect(requests.filter(row => row.method === 'POST' && row.path.endsWith('/preview'))).toHaveLength(2);
    await expect(page.getByText('Owner đã xác nhận. Worker sẽ kiểm tra lại và thực thi.', { exact: true })).toBeVisible(); await expect(guide.getByRole('link')).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
  test('guided review cancel consumes only the returned action once', async ({ page }) => {
    const { guide, requests, state } = await fixture(page, { selected: true });
    await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled();
    await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Hủy yêu cầu' }).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(state.decisions).toEqual([`/api/v1/pending-actions/${state.action.action_id}/cancel`]);
    expect(requests.filter(row => row.method === 'POST').map(row => row.path)).toEqual(['/api/v1/onboarding/first-source/preview', `/api/v1/pending-actions/${state.action.action_id}/cancel`]);
  });
  test('test_no_automatic_allow_all', async ({ page }) => {
    const { guide, requests } = await fixture(page);
    await expect(guide.getByLabel('Chọn một nguồn').locator('option')).toHaveCount(4);
    await guide.getByLabel('Chọn một nguồn').selectOption(A); await guide.getByRole('button', { name: 'Lưu nguồn đã chọn' }).click();
    await expect(guide).toContainText('không tự ALLOW');
    expect(requests.filter(row => row.method !== 'GET').map(row => row.path)).toEqual(['/api/v1/onboarding/source-selection']);
    expect(requests.some(row => row.path.includes('enable-all') || row.path.includes('permissions') || row.path.includes('confirm'))).toBe(false);
  });
  test('test_job_accounting_visible', async ({ page }) => {
    const { guide, state } = await fixture(page, { selected: true, learning: operation(), jobs: [{ id: 41, payload: { chat_id: A, processed: 0, total: 0, synced_messages: 0, indexed_new: 0 }, status: 'running' }, { id: 42, payload: { chat_id: B, processed: 999 }, status: 'completed' }] });
    await expect(guide).toContainText('Chưa có dữ liệu tiến độ'); await expect(guide.getByRole('progressbar')).toHaveCount(0);
    await expect(guide.getByText('Đã xử lý / tổng: 0 / 0', { exact: true })).toBeVisible(); await expect(guide).toContainText('Đã tái sử dụng: Chưa biết'); await expect(guide).not.toContainText('999');
    state.status.learning_operation.progress = 0; await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click();
    await expect(guide.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
    state.status.learning_operation.operation_id = '99'; await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click(); await expect(guide).toContainText('Chưa có số liệu của đúng công việc và nguồn.');
  });
  test('test_block_progress_cancels', async ({ page }) => {
    const { guide, state, requests } = await fixture(page, { selected: true, learning: operation('completed', 100), bot: 'Safe_bot' });
    await expect(guide.getByRole('link', { name: 'Mở bot để hỏi thử' })).toHaveAttribute('href', 'https://t.me/Safe_bot');
    state.allowed = false; await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click();
    await expect(guide).toContainText('BLOCK'); await expect(guide.getByRole('link')).toHaveCount(0); await expect(guide.getByRole('progressbar')).toHaveCount(0);
    expect(requests.every(row => row.method === 'GET')).toBe(true);
  });
  test('test_recovery_verified', async ({ page }) => {
    const { guide, state, requests } = await fixture(page, { selected: true, bot: 'Safe_bot' });
    const link = guide.getByRole('link', { name: 'Mở bot để hỏi thử' }); await expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    await expect(guide).toContainText(`/ask in:${A}`);
    state.status.answer_operation = operation('uncertain'); await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click();
    await expect(guide).toContainText('Cần đối chiếu'); await expect(link).toHaveCount(0); await expect(guide).not.toContainText('Đã xác minh câu trả lời');
    state.status.answer_operation = null; state.status.bot_username = null; await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click(); await expect(link).toHaveCount(0);
    state.status.bot_username = 'Bad_bot\n'; await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click(); await expect(link).toHaveCount(0);
    state.status.profile_id = 'foreign-profile'; await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click(); await expect(guide.getByRole('alert')).toBeVisible(); await expect(link).toHaveCount(0);
    expect(requests.every(row => row.method === 'GET')).toBe(true);
  });
});
test('setup-only does not access guided management', async ({ page }) => { const { guide, requests } = await fixture(page, { setupOnly: true }); await expect(page.getByRole('heading', { name: 'Tổng quan', exact: true })).toBeVisible(); await expect(guide).toHaveCount(0); expect(requests.some(row => row.path.includes('/onboarding/'))).toBe(false); });
test('AI skip remains limited and cannot offer a bot test', async ({ page }) => { const { guide } = await fixture(page, { aiSkip: true, selected: true }); await expect(guide).toBeVisible(); await expect(guide.getByRole('link')).toHaveCount(0); await expect(guide).not.toContainText('Đã xác minh câu trả lời'); });
test('A to B to A does not revive a delayed old source observation', async ({ page }) => {
  const { guide, state } = await fixture(page, { selected: true, bot: 'Safe_bot' });
  await expect(guide.getByRole('link')).toBeVisible();
  let release; let started;
  const delayed = new Promise(resolve => { release = resolve; });
  const arrived = new Promise(resolve => { started = resolve; });
  let first = true;
  await page.route('**/api/v1/onboarding/first-source', async route => {
    if (!first) return route.fallback(); first = false;
    const old = { ...state.status }; started(); await delayed; await route.fulfill({ json: old });
  });
  await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click(); await arrived;
  await expect(guide.getByRole('link')).toHaveCount(0);
  state.status.test_available = false;
  await guide.getByLabel('Chọn một nguồn').selectOption(B); await guide.getByRole('button', { name: 'Lưu nguồn đã chọn' }).click();
  await expect(guide).toContainText(`Nguồn đã lưu: ${B}`);
  await guide.getByLabel('Chọn một nguồn').selectOption(A); await guide.getByRole('button', { name: 'Lưu nguồn đã chọn' }).click();
  await expect(guide).toContainText(`Nguồn đã lưu: ${A}`);
  release(); await page.waitForTimeout(200);
  await expect(guide.getByRole('link')).toHaveCount(0);
});
test('focus while source selection is pending does not strand the save control', async ({ page }) => {
  const { guide, state } = await fixture(page);
  await expect(guide.getByLabel('Chọn một nguồn')).toBeEnabled();
  let release; let started;
  const delayed = new Promise(resolve => { release = resolve; });
  const arrived = new Promise(resolve => { started = resolve; });
  await page.route('**/api/v1/onboarding/source-selection', async route => {
    started(); await delayed; state.status.source_id = A; await route.fulfill({ json: state.status });
  });
  await guide.getByLabel('Chọn một nguồn').selectOption(A); await guide.getByRole('button', { name: 'Lưu nguồn đã chọn' }).click(); await arrived;
  await page.evaluate(() => window.dispatchEvent(new Event('focus'))); release();
  await expect(guide.getByRole('button', { name: 'Kiểm tra nguồn' })).toBeEnabled();
  await expect(guide.getByRole('button', { name: 'Lưu nguồn đã chọn' })).toBeEnabled();
});
test('failed refresh withdraws the existing bot link and malformed source IDs fail closed', async ({ page }) => {
  const { guide, state } = await fixture(page, { selected: true, bot: 'Safe_bot' });
  await expect(guide.getByRole('link')).toBeVisible();
  await page.route('**/api/v1/onboarding/first-source', route => route.fulfill({ status: 503, json: { code: 'synthetic_unavailable' } }));
  await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click();
  await expect(guide.getByRole('alert')).toBeVisible(); await expect(guide.getByRole('link')).toHaveCount(0);
  await page.unroute('**/api/v1/onboarding/first-source'); state.status.source_id = -1002;
  await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click();
  await expect(guide.getByRole('alert')).toBeVisible(); await expect(guide.getByRole('link')).toHaveCount(0);
});

for (const mutation of ['foreign source', 'numeric source', 'wrong type', 'wrong prefix', 'id newline', 'wrong status', 'missing capture', 'bad capture', 'capture newline', 'wrong limit', 'missing preview', 'empty preview', 'missing reason', 'bad reason', 'bad expiry', 'no timezone expiry', 'expired']) test(`guided preview rejects ${mutation}`, async ({ page }) => {
  const { guide, state, requests } = await fixture(page, { selected: true });
  const changes = {
    'foreign source': { chat_id: B }, 'numeric source': { chat_id: Number(B) }, 'wrong type': { action_type: 'enable_all_knowledge' }, 'wrong prefix': { action_id: 'advanced-action' }, 'wrong status': { status: 'confirmed' },
    'id newline': { action_id: state.action.action_id + '\n' }, 'capture newline': { payload: { ...state.action.payload, first_source_preview: state.action.payload.first_source_preview + '\n' } }, 'empty preview': { preview: ' ' }, 'bad reason': { reason: 123 }, 'no timezone expiry': { expires_at: state.action.expires_at.slice(0, -1) },
    'missing capture': { payload: { limit: 1000 } }, 'bad capture': { payload: { first_source_preview: 'not-a-uuid', limit: 1000 } }, 'wrong limit': { payload: { ...state.action.payload, limit: 999 } }, 'missing preview': { preview: null }, 'missing reason': { reason: undefined }, 'bad expiry': { expires_at: 'tomorrow' }, 'expired': { expires_at: new Date(Date.now() - 1000).toISOString() },
  };
  state.action = { ...state.action, ...changes[mutation] };
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled();
  await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click();
  await expect(guide.getByRole('alert')).toContainText('chưa xác định'); await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeDisabled(); expect(requests.filter(row => row.method === 'POST')).toHaveLength(1);
});

for (const trigger of ['refresh', 'focus', 'hidden', 'source change', 'profile mismatch', 'failed refresh', 'malformed projection', 'unmount', 'expiry']) test(`guided review withdraws on ${trigger}`, async ({ page }) => {
  const { guide, state, requests } = await fixture(page, { selected: true });
  if (trigger === 'expiry') state.action.expires_at = new Date(Date.now() + 2000).toISOString();
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click(); await expect(page.getByRole('dialog')).toBeVisible();
  if (trigger === 'focus') await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  else if (trigger === 'hidden') await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
  else if (trigger === 'source change') await guide.getByLabel('Chọn một nguồn').selectOption(B);
  // Dispatch lifecycle mutations behind the modal; physical clicks are correctly trapped by its backdrop.
  else if (trigger === 'unmount') await page.getByRole('button', { name: 'Công việc', exact: true }).dispatchEvent('click');
  else if (trigger !== 'expiry') {
    if (trigger === 'profile mismatch') state.status.profile_id = 'foreign-profile';
    if (trigger === 'malformed projection') state.status.source_id = Number(B);
    if (trigger === 'failed refresh') await page.route('**/api/v1/onboarding/first-source', route => route.fulfill({ status: 503, json: {} }));
    await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).dispatchEvent('click');
  }
  await expect(page.getByRole('dialog')).toHaveCount(0); expect(requests.filter(row => row.method === 'POST')).toHaveLength(1); expect(state.decisions).toHaveLength(0);
});

for (const trigger of ['source A B A', 'focus', 'hidden', 'failed refresh', 'unmount']) test(`late guided preview cannot reopen after ${trigger}`, async ({ page }) => {
  const { guide, state, requests } = await fixture(page, { selected: true });
  let release; let started;
  const delayed = new Promise(resolve => { release = resolve; }); const arrived = new Promise(resolve => { started = resolve; });
  await page.route('**/api/v1/onboarding/first-source/preview', async route => { started(); await delayed; await route.fulfill({ json: state.action }); });
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click(); await arrived;
  if (trigger === 'source A B A') {
    await guide.getByLabel('Chọn một nguồn').selectOption(B); await guide.getByRole('button', { name: 'Lưu nguồn đã chọn' }).click(); await expect(guide).toContainText(`Nguồn đã lưu: ${B}`);
    await guide.getByLabel('Chọn một nguồn').selectOption(A); await guide.getByRole('button', { name: 'Lưu nguồn đã chọn' }).click(); await expect(guide).toContainText(`Nguồn đã lưu: ${A}`);
  } else if (trigger === 'focus') await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  else if (trigger === 'hidden') await page.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }); document.dispatchEvent(new Event('visibilitychange')); });
  else if (trigger === 'unmount') await page.getByRole('button', { name: 'Công việc', exact: true }).click();
  else { await page.route('**/api/v1/onboarding/first-source', route => route.fulfill({ status: 503, json: {} })); await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click(); await expect(guide.getByRole('alert')).toBeVisible(); }
  release(); await page.waitForTimeout(150); await expect(page.getByRole('dialog')).toHaveCount(0); expect(state.decisions).toHaveLength(0);
  expect(requests.some(row => row.path.includes('/confirm'))).toBe(false);
});

test('unknown preview outcome requires refresh before another explicit preview', async ({ page }) => {
  const { guide, requests } = await fixture(page, { selected: true });
  let attempts = 0;
  await page.route('**/api/v1/onboarding/first-source/preview', route => { attempts++; return route.abort('failed'); });
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click();
  await expect(guide.getByRole('alert')).toContainText('chưa xác định'); await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeDisabled(); await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.unroute('**/api/v1/onboarding/first-source/preview'); await guide.getByRole('button', { name: 'Kiểm tra nguồn' }).click(); await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled();
  expect(attempts).toBe(1); expect(requests.filter(row => row.method === 'POST')).toHaveLength(0); await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click(); await expect(page.getByRole('dialog')).toBeVisible(); expect(requests.filter(row => row.method === 'POST')).toHaveLength(1);
});

test('preview timeout reports unknown and never resends or opens a late review', async ({ page }) => {
  const { guide, state } = await fixture(page, { selected: true });
  await page.clock.install();
  let release; let started; let previews = 0;
  const delayed = new Promise(resolve => { release = resolve; }); const arrived = new Promise(resolve => { started = resolve; });
  await page.route('**/api/v1/onboarding/first-source/preview', async route => { previews++; started(); await delayed; await route.fulfill({ json: state.action }); });
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click(); await arrived;
  await page.clock.fastForward(10001); await expect(guide.getByRole('alert')).toContainText('chưa xác định'); await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeDisabled();
  release(); await page.waitForTimeout(150); await expect(page.getByRole('dialog')).toHaveCount(0); expect(previews).toBe(1); expect(state.decisions).toHaveLength(0);
});

test('pending preview preserves keyboard focus and accepts only one activation', async ({ page }) => {
  const { guide, state } = await fixture(page, { selected: true });
  let release; let started; let previews = 0;
  const delayed = new Promise(resolve => { release = resolve; }); const arrived = new Promise(resolve => { started = resolve; });
  await page.route('**/api/v1/onboarding/first-source/preview', async route => { previews++; started(); await delayed; await route.fulfill({ json: state.action }); });
  const preview = guide.getByRole('button', { name: 'Xem và xác nhận quyền' }); await expect(preview).toBeEnabled(); await preview.focus(); await page.keyboard.press('Enter'); await arrived;
  const pending = guide.getByRole('button', { name: 'Đang tạo preview…' }); await expect(pending).toBeFocused(); await expect(pending).toHaveAttribute('aria-disabled', 'true');
  await expect(pending).toHaveCSS('opacity', '0.72'); await expect(pending).toHaveCSS('cursor', 'wait');
  await page.keyboard.press('Enter'); await page.keyboard.press('Space'); await pending.dispatchEvent('click'); await page.waitForTimeout(100); expect(previews).toBe(1);
  release(); await expect(page.getByRole('dialog').getByRole('button', { name: 'Đóng', exact: true })).toBeFocused(); await page.keyboard.press('Escape'); await expect(preview).toBeFocused();
});

test('guided unmount and focus refresh do not close unrelated advanced review', async ({ page }) => {
  const { guide, state, requests } = await fixture(page, { selected: true });
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click(); await expect(page.getByRole('dialog').getByRole('button', { name: 'Đóng', exact: true })).toBeFocused(); await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Tri thức', exact: true }).click(); await page.getByRole('button', { name: 'Đưa nguồn còn thiếu vào bộ não' }).click(); await expect(page.getByRole('dialog')).toContainText('advanced-action');
  const reads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length;
  await page.evaluate(() => window.dispatchEvent(new Event('focus'))); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBe(reads + 1); await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await expect(page.getByRole('dialog')).toContainText('advanced-action');
  await page.getByRole('button', { name: 'Công việc', exact: true }).dispatchEvent('click'); await expect(guide).toHaveCount(0); await expect(page.getByRole('dialog')).toContainText('advanced-action');
  await page.getByRole('dialog').getByRole('button', { name: 'Hủy yêu cầu' }).click(); await expect(page.getByRole('dialog')).toHaveCount(0); expect(state.decisions).toEqual(['/api/v1/pending-actions/advanced-action/cancel']);
});

test('background polling defers an open guided review across 30 seconds and resumes after Escape', async ({ page }) => {
  await page.clock.install();
  const { guide, requests, state } = await fixture(page, { selected: true });
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click();
  const dialog = page.getByRole('dialog'); const close = dialog.getByRole('button', { name: 'Đóng', exact: true }); await expect(close).toBeFocused();
  const reads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length;
  await page.clock.runFor(31000); await expect(dialog).toBeVisible(); await expect(close).toBeFocused(); expect(requests.filter(row => row.path === '/api/v1/onboarding/first-source')).toHaveLength(reads); expect(state.decisions).toEqual([]);
  expect(requests.filter(row => row.method === 'POST').map(row => row.path)).toEqual(['/api/v1/onboarding/first-source/preview']);
  await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0);
  await page.clock.runFor(15000); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBeGreaterThan(reads); await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); expect(state.decisions).toEqual([]);
});

test('background polling defers a preview still pending when its timer ticks', async ({ page }) => {
  await page.clock.install();
  const { guide, requests, state } = await fixture(page, { selected: true });
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await page.clock.runFor(14000);
  let release; let started; let previews = 0;
  const delayed = new Promise(resolve => { release = resolve; }); const arrived = new Promise(resolve => { started = resolve; });
  await page.route('**/api/v1/onboarding/first-source/preview', async route => { previews++; started(); await delayed; await route.fulfill({ json: state.action }); });
  const reads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length;
  await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click(); await arrived;
  await page.clock.runFor(2000); await expect(guide.getByRole('button', { name: 'Đang tạo preview…' })).toHaveAttribute('aria-disabled', 'true'); expect(requests.filter(row => row.path === '/api/v1/onboarding/first-source')).toHaveLength(reads); expect(previews).toBe(1);
  release(); await expect(page.getByRole('dialog')).toBeVisible(); await page.clock.runFor(31000); await expect(page.getByRole('dialog')).toBeVisible(); expect(state.decisions).toEqual([]); expect(previews).toBe(1);
});

test('background polling resumes after a successful guided confirmation closes review', async ({ page }) => {
  await page.clock.install();
  const { guide, requests, state } = await fixture(page, { selected: true });
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click();
  const dialog = page.getByRole('dialog'); await expect(dialog.getByRole('button', { name: 'Đóng', exact: true })).toBeFocused(); await page.clock.runFor(31000); await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Xác nhận thực thi' }).click(); await expect(dialog).toHaveCount(0); await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled();
  expect(state.decisions).toEqual([`/api/v1/pending-actions/${state.action.action_id}/confirm`]);
  const reads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length;
  await page.clock.runFor(16000); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBeGreaterThan(reads); await expect(dialog).toHaveCount(0); expect(state.decisions).toHaveLength(1);
});

test('an unrelated advanced modal does not defer background source polling', async ({ page }) => {
  await page.clock.install();
  const { guide, requests, state } = await fixture(page, { selected: true });
  await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click(); await expect(page.getByRole('dialog').getByRole('button', { name: 'Đóng', exact: true })).toBeFocused(); await page.keyboard.press('Escape'); await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Tri thức', exact: true }).click(); await page.getByRole('button', { name: 'Đưa nguồn còn thiếu vào bộ não' }).click(); await expect(page.getByRole('dialog')).toContainText('advanced-action');
  const reads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length;
  await page.clock.runFor(16000); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBeGreaterThan(reads); await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeEnabled(); await expect(page.getByRole('dialog')).toContainText('advanced-action'); expect(state.decisions).toEqual([]);
});

for (const width of [360, 390, 1280, 1440]) test.describe(`SSE guided lifecycle ${width}`, () => {
  test.beforeEach(async ({ page }) => { await page.setViewportSize({ width, height: 900 }); await page.clock.install(); });
  test('SSE own-preview snapshot preserves review and flushes after Escape', async ({ page }) => {
    const { guide, state, requests, emitSnapshot } = await fixture(page, { selected: true, realtime: true });
    const snapshot = { pending_actions: 0, jobs: {}, runtime: null, latest_audit_id: 0, sent_at: new Date().toISOString() };
    await emitSnapshot(snapshot); await page.getByRole('button', { name: 'Thông báo', exact: true }).click();
    const preview = guide.getByRole('button', { name: 'Xem và xác nhận quyền' }); await expect(preview).toBeEnabled(); await preview.click();
    const dialog = page.getByRole('dialog'); await expect(dialog.getByRole('button', { name: 'Đóng', exact: true })).toBeFocused();
    const reads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length; const views = requests.filter(row => row.path === '/api/v1/overview').length;
    await emitSnapshot({ ...snapshot, pending_actions: 1, latest_audit_id: 1 });
    await expect.poll(() => requests.filter(row => row.path === '/api/v1/overview').length).toBe(views + 1);
    await expect(dialog).toBeVisible(); await expect(page.locator('.notification-popover')).toContainText('1 hành động đang chờ owner.');
    await page.clock.runFor(10000); await expect(dialog).toBeVisible(); expect(requests.filter(row => row.path === '/api/v1/onboarding/first-source')).toHaveLength(reads);
    expect(requests.filter(row => row.method === 'POST')).toEqual([expect.objectContaining({ path: '/api/v1/onboarding/first-source/preview', body: { source_id: A }, headers: expect.objectContaining({ 'x-csrf-token': 'synthetic-csrf' }) })]); expect(state.decisions).toEqual([]);
    let releaseStatus; state.statusGate = new Promise(resolve => { releaseStatus = resolve; });
    try {
      await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0);
      await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBe(reads + 1);
      await expect(preview).toHaveAttribute('aria-disabled', 'true'); await expect(preview).toBeFocused();
      await page.keyboard.press('Enter'); await page.keyboard.press('Space'); await preview.dispatchEvent('click');
      expect(requests.filter(row => row.path.endsWith('/first-source/preview'))).toHaveLength(1);
    } finally { releaseStatus(); }
    await expect(preview).toBeEnabled(); await expect(preview).toBeFocused(); expect(state.decisions).toEqual([]);
  });
  test('SSE pending preview keeps response and resumes after exactly one confirmation', async ({ page }) => {
    const { guide, state, requests, emitSnapshot } = await fixture(page, { selected: true, realtime: true });
    const snapshot = { pending_actions: 0, jobs: {}, runtime: null, latest_audit_id: 0, sent_at: new Date().toISOString() }; await emitSnapshot(snapshot);
    let release; state.previewGate = new Promise(resolve => { release = resolve; });
    const preview = guide.getByRole('button', { name: 'Xem và xác nhận quyền' }); await expect(preview).toBeEnabled();
    const reads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length; const views = requests.filter(row => row.path === '/api/v1/overview').length;
    await preview.click(); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source/preview').length).toBe(1);
    try {
      await emitSnapshot({ ...snapshot, pending_actions: 1, latest_audit_id: 1 }); await expect.poll(() => requests.filter(row => row.path === '/api/v1/overview').length).toBe(views + 1);
      await expect(guide.getByRole('button', { name: 'Đang tạo preview…' })).toHaveAttribute('aria-disabled', 'true'); expect(requests.filter(row => row.path === '/api/v1/onboarding/first-source')).toHaveLength(reads);
    } finally { release(); }
    const dialog = page.getByRole('dialog'); await expect(dialog.getByRole('button', { name: 'Đóng', exact: true })).toBeFocused(); expect(state.decisions).toEqual([]);
    await dialog.getByRole('button', { name: 'Xác nhận thực thi' }).click(); await expect(dialog).toHaveCount(0);
    await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBe(reads + 1); await expect(preview).toBeEnabled();
    await emitSnapshot({ ...snapshot, latest_audit_id: 2 }); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBe(reads + 2); await expect(preview).toBeEnabled();
    expect(state.decisions).toEqual([`/api/v1/pending-actions/${state.action.action_id}/confirm`]); expect(requests.filter(row => row.method === 'POST').map(row => row.path)).toEqual(['/api/v1/onboarding/first-source/preview', `/api/v1/pending-actions/${state.action.action_id}/confirm`]);
  });
  test('SSE refreshes normal guide and advanced view without closing advanced review', async ({ page }) => {
    const { guide, state, requests, emitSnapshot } = await fixture(page, { selected: true, realtime: true });
    const snapshot = { pending_actions: 0, jobs: {}, runtime: null, latest_audit_id: 0, sent_at: new Date().toISOString() }; await emitSnapshot(snapshot);
    const preview = guide.getByRole('button', { name: 'Xem và xác nhận quyền' }); await expect(preview).toBeEnabled();
    const initialReads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length; state.status.message = 'Nguồn hiện tại sau snapshot.';
    await emitSnapshot({ ...snapshot, latest_audit_id: 1 }); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBe(initialReads + 1); await expect(guide.getByRole('status')).toContainText(state.status.message); expect(requests.filter(row => row.method === 'POST')).toHaveLength(0);
    const menu = page.getByRole('button', { name: 'Mở menu', exact: true }); if (await menu.isVisible()) await menu.click();
    await page.getByRole('button', { name: 'Tri thức', exact: true }).click(); await page.getByRole('button', { name: 'Đưa nguồn còn thiếu vào bộ não' }).click(); await expect(page.getByRole('dialog')).toContainText('advanced-action');
    const reads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length; const views = requests.filter(row => row.path === '/api/v1/knowledge/sources').length;
    await emitSnapshot({ ...snapshot, pending_actions: 1, latest_audit_id: 2 }); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBe(reads + 1); await expect.poll(() => requests.filter(row => row.path === '/api/v1/knowledge/sources').length).toBe(views + 1);
    await expect(preview).toBeEnabled(); await expect(page.getByRole('dialog')).toContainText('advanced-action'); expect(state.decisions).toEqual([]); expect(requests.filter(row => row.method === 'POST').map(row => row.path)).toEqual(['/api/v1/knowledge/enable-all-action']);
  });
});

test('SSE queued refresh is consumed by explicit topbar reconnect and closed streams stay closed', async ({ page }) => {
  await page.clock.install();
  const { guide, requests, emitSnapshot } = await fixture(page, { selected: true, realtime: true });
  const snapshot = { pending_actions: 0, jobs: {}, runtime: null, latest_audit_id: 0, sent_at: new Date().toISOString() }; await emitSnapshot(snapshot);
  await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click(); await expect(page.getByRole('dialog').getByRole('button', { name: 'Đóng', exact: true })).toBeFocused();
  const reads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length; const views = requests.filter(row => row.path === '/api/v1/overview').length;
  await emitSnapshot({ ...snapshot, pending_actions: 1, latest_audit_id: 1 }); await expect.poll(() => requests.filter(row => row.path === '/api/v1/overview').length).toBe(views + 1); await expect(page.getByRole('dialog')).toBeVisible(); expect(requests.filter(row => row.path === '/api/v1/onboarding/first-source')).toHaveLength(reads);
  await page.evaluate(() => { for (const stream of window.__fixtureStreams) if (!stream.closed) stream.onerror?.(new Event('error')); });
  await page.getByRole('button', { name: /Thử kết nối lại/ }).dispatchEvent('click'); await expect(page.getByRole('dialog')).toHaveCount(0); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBe(reads + 1);
  await expect.poll(() => page.evaluate(() => window.__fixtureStreams.filter(stream => !stream.closed).length)).toBe(1);
  expect(await page.evaluate(() => window.__fixtureStreams.some(stream => stream.closed) && window.__fixtureStreams.filter(stream => stream.closed).every(stream => Object.keys(stream.listeners).length === 0))).toBe(true);
  await emitSnapshot({ ...snapshot, latest_audit_id: 2 }); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBe(reads + 2);
});

test('SSE queued snapshots do not renew original guided expiry and flush only once', async ({ page }) => {
  await page.clock.install();
  const { guide, state, requests, emitSnapshot } = await fixture(page, { selected: true, realtime: true });
  const snapshot = { pending_actions: 0, jobs: {}, runtime: null, latest_audit_id: 0, sent_at: new Date().toISOString() }; await emitSnapshot(snapshot);
  state.action.expires_at = await page.evaluate(() => new Date(Date.now() + 10000).toISOString());
  await guide.getByRole('button', { name: 'Xem và xác nhận quyền' }).click(); const dialog = page.getByRole('dialog'); await expect(dialog.getByRole('button', { name: 'Đóng', exact: true })).toBeFocused();
  const reads = requests.filter(row => row.path === '/api/v1/onboarding/first-source').length;
  await emitSnapshot({ ...snapshot, pending_actions: 1, latest_audit_id: 1 }); await page.clock.runFor(5000); await expect(dialog).toBeVisible();
  await emitSnapshot({ ...snapshot, pending_actions: 1, latest_audit_id: 2 }); await page.clock.runFor(5500); await expect(dialog).toHaveCount(0); await expect.poll(() => requests.filter(row => row.path === '/api/v1/onboarding/first-source').length).toBe(reads + 1);
  expect(state.decisions).toEqual([]); expect(requests.filter(row => row.method === 'POST').map(row => row.path)).toEqual(['/api/v1/onboarding/first-source/preview']);
});
