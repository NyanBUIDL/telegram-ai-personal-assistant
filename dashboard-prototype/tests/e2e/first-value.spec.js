import { test, expect } from '@playwright/test';

// Synthetic presentation only; these fixtures cannot verify U03 authority or delivery.
if (process.env.D02_CHROMIUM_EXECUTABLE) test.use({ launchOptions: { executablePath: process.env.D02_CHROMIUM_EXECUTABLE } });
const A = '-1009007199254740993';
const B = '-1002';
const C = '-1003';
const operation = (state = 'running', progress = null) => ({ operation_id: '41', state, progress, code: 'synthetic', message: 'Kết quả backend', next_action: 'Kiểm tra nguồn' });
async function fixture(page, options = {}) {
  const requests = [];
  const state = {
    status: { profile_id: 'synthetic-profile', source_id: options.selected ? A : null, learning_operation: options.learning || null, answer_operation: null, bot_username: options.bot || null, test_available: Boolean(options.bot), code: 'selection_required', message: 'Chọn một nguồn; chưa cấp quyền.', next_action: 'Xem quyền trước khi học' },
    allowed: options.allowed ?? true,
    jobs: options.jobs || [],
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
    else if (path.endsWith('/onboarding/first-source')) body = state.status;
    else if (path.endsWith('/onboarding/source-selection')) { state.status = { ...state.status, source_id: request.postDataJSON().source_id }; body = state.status; }
    else if (path.endsWith('/groups')) body = { items: [A, B, C].map((chat_id, index) => ({ chat_id, title: ['Nguồn A tiếng Việt rất dài để kiểm tra xuống dòng an toàn', 'Nguồn B', 'Nguồn C'][index], chat_type: index === 1 ? 'channel' : 'supergroup', policy: { allowed: false } })), total: 3, page: 1, page_size: 10 };
    else if (path.startsWith('/api/v1/groups/')) body = { chat_id: decodeURIComponent(path.split('/').at(-1)), policy: { allowed: state.allowed } };
    else if (path.endsWith('/learning-jobs')) body = { items: state.jobs };
    else return route.fulfill({ status: 403, json: { detail: 'Synthetic unavailable' } });
    await route.fulfill({ json: body });
  });
  await page.goto('http://127.0.0.1:5177');
  return { requests, state, guide: page.getByRole('region', { name: 'Nguồn đầu tiên' }) };
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
    await expect(guide.getByRole('button', { name: 'Xem và xác nhận quyền' })).toBeDisabled();
    const writes = requests.filter(row => row.method === 'POST');
    expect(writes).toHaveLength(1); expect(writes[0].body).toEqual({ source_id: A }); expect(writes[0].headers['x-csrf-token']).toBe('synthetic-csrf');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const control of await guide.locator('button, input, select').all()) { const box = await control.boundingBox(); expect(box.height).toBeGreaterThanOrEqual(44); await expect(control).toHaveCSS('font-family', /Darley Sans/); }
    await expect(guide).toHaveCSS('border-top-width', '3px'); await expect(guide).toHaveCSS('box-shadow', 'rgb(9, 9, 9) 7px 7px 0px 0px');
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
