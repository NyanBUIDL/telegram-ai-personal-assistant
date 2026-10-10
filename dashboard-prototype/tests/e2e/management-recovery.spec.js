import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';

if (process.env.D02_CHROMIUM_EXECUTABLE) test.use({ launchOptions: { executablePath: process.env.D02_CHROMIUM_EXECUTABLE } });
else if (process.env.D02_BROWSER_CHANNEL) test.use({ channel: process.env.D02_BROWSER_CHANNEL });
const A = '-1009007199254740993';
const group = { chat_id: A, title: 'Nguồn tiếng Việt dài để kiểm tra quản lý hằng ngày', chat_type: 'supergroup', knowledge: {}, permissions: {}, policy: { allowed: true, ai_mode: 'local_only', retention_days: 30 } };
async function harness(page, component, props = {}) {
  page.on('pageerror', error => { throw error; });
  await page.route('**/__management_test', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="vi"><head><meta charset="utf-8"><link rel="stylesheet" href="/src/styles.css"></head><body><div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type; window.__vite_plugin_react_preamble_installed__=true;
    const {default: React}=await import('/node_modules/.vite/deps/react.js'); const {default: ReactDOM}=await import('/node_modules/.vite/deps/react-dom_client.js');
    const operations=await import('/src/views/OperationsViews.jsx'); const system=await import('/src/views/SystemViews.jsx'); const ai=await import('/src/views/AiViews.jsx'); const {KnowledgeView}=await import('/src/views/KnowledgeView.jsx'); const {GroupDetailView}=await import('/src/views/GroupsView.jsx'); const {Toast}=await import('/src/ui.jsx'); const {api,setCsrfToken,onAuthFailure}=await import('/src/api.js');
    setCsrfToken('synthetic-csrf'); window.api=api; window.withdrawn=false; onAuthFailure(()=>window.withdrawn=true);
    const {App}=await import('/src/App.jsx'); const e=React.createElement; const components={...operations,...system,...ai,KnowledgeView,GroupDetailView,App}; const root=ReactDOM.createRoot(document.getElementById('root'));
    function Harness(props) { const [toast,setToast]=React.useState(null); return e(React.Fragment,null,e(components[${JSON.stringify(component)}],{...props,onToast:(message,tone)=>setToast({message,tone}),onCreatedAction:action=>window.createdAction=action,onBack:()=>{},onNavigate:id=>window.navigation=id}),e(Toast,{toast,onClose:()=>setToast(null)})); }
    window.renderManagement=props=>root.render(e(Harness,props)); window.renderManagement(${JSON.stringify(props)});
  </script></body></html>` }));
  await page.goto('http://127.0.0.1:5177/__management_test');
  await page.waitForFunction(() => typeof window.renderManagement === 'function');
}
async function fixture(page) {
  const state = { requests: [], error: 403, jobs: [{ id: 1, status: 'running', payload: { chat_id: A } }, { id: 2, status: 'failed', payload: {} }, { id: 3, status: 'uncertain', payload: {} }], backups: [], projectionStatus: 200 };
  await page.route('**/api/v1/**', async route => {
    const req=route.request(), url=new URL(req.url()), path=url.pathname;
    state.requests.push({path,method:req.method(),body:req.postDataJSON(),search:url.search,csrf:req.headers()['x-csrf-token']});
    if (/\/(ai-route|limits|ai-efficiency|provider)$/.test(path)) return route.fulfill({ status:state.error,json:{detail:{code:'denied',input:'PRIVATE_REJECTED_INPUT'}} });
    if (path.endsWith('/groups/'+A)) return route.fulfill({json:group});
    if (/\/learning-jobs\/\d+\/(pause|retry|resume)$/.test(path)) { const id=Number(path.split('/').at(-2)); const job=state.jobs.find(j=>j.id===id); job.status=path.endsWith('/pause')?'pause_requested':'queued'; return route.fulfill({json:job}); }
    if(path.endsWith('/learning-jobs')) return route.fulfill({json:{items:state.jobs}});
    if(path.endsWith('/ai/config')) return route.fulfill({json:{provider:'ollama',providers:['ollama','openai','off']}});
    if(path.endsWith('/knowledge/sources')) return route.fulfill({json:{items:[],total:0,summary:{coverage_percent:null}}});
    if(path.endsWith('/backups')) { if(req.method()==='POST') { if(state.backupGate) await state.backupGate; if(state.backupStatus) return route.fulfill({status:state.backupStatus,json:{detail:{code:state.backupCode}}}); state.backups=[{id:'backup-synthetic',size_bytes:300,validation_state:'manifest_only',manifest:{created_at:'2026-10-10T00:00:00Z'}}]; return route.fulfill({status:201,json:{id:'backup-synthetic',manifest:{}}}); } return route.fulfill({json:{items:state.backups}}); }
    if(path.endsWith('/audit')) return route.fulfill({status:url.searchParams.has('support_export')?state.projectionStatus:200,json:{items:url.searchParams.has('support_export')?[{occurred_at:'2026-10-10T00:00:00Z',action:'dashboard_ai_provider_changed',target_type:'unknown',outcome:'failed'}]:[{id:1,reason:'PRIVATE_CACHED_TEXT',target_id:A}],page:1,page_size:100,total:1}});
    if(path.endsWith('/docs')) return route.fulfill({json:{items:[{id:'user-guide',name:'Hướng dẫn sử dụng',available:true},{id:'second',name:'Tài liệu thứ hai',available:true}]}});
    if(path.includes('/docs/')) { if(state.docGate) await state.docGate; return route.fulfill({json:{id:path.split('/').at(-1),name:'Hướng dẫn sử dụng',content:'Nội dung tài liệu'}}); }
    if(path.endsWith('/workers')) return route.fulfill({json:{scheduler:[{id:'configured'}],latest_metric:{cpu_percent:state.cpu??null,collected_at:state.measuredAt??null},jobs:[]}});
    return route.fulfill({json:{items:[],summary:{}}});
  });
  return state;
}
test('test_403_rollback_action',async({page})=>{
  const state=await fixture(page); await harness(page,'GroupDetailView',{chatId:A});
  await page.getByLabel(/^AI mode/).selectOption('off'); await page.getByRole('button',{name:'Lưu AI route'}).click();
  await expect(page.getByRole('alert').first()).toContainText('quyền'); await expect(page.getByText(/Bản nháp chưa lưu/).first()).toBeVisible();
  await expect(page.locator('body')).not.toContainText('PRIVATE_REJECTED_INPUT'); await expect(page.locator('body')).not.toContainText('[object Object]');
  expect(state.requests.find(r=>r.method==='PUT')).toMatchObject({path:'/api/v1/groups/'+A+'/ai-route',body:{mode:'off'},csrf:'synthetic-csrf'});
  await expect(page.locator('.permission-matrix [role="switch"]')).toHaveCount(19);
});
test('test_provider_failure_next_action',async({page})=>{
  const state=await fixture(page); state.error=409; await harness(page,'AiView');
  await page.getByRole('radio',{name:'OPENAI',exact:true}).click(); await page.getByRole('button',{name:'Xác nhận chuyển'}).click();
  await expect(page.getByRole('radio',{name:'OLLAMA',exact:true})).toHaveAttribute('aria-checked','true');
  await expect(page.getByRole('alert').first()).toContainText('Windows');
  await expect(page.getByRole('button',{name:'Mở Kết nối'})).toBeVisible();
  expect(state.requests.filter(r=>r.path.endsWith('/provider'))).toHaveLength(1);
});
test('test_pause_retry_cancel',async({page})=>{
  await fixture(page); await harness(page,'KnowledgeView');
  const cards=page.locator('.learning-job-card'); await cards.nth(0).getByRole('button',{name:'Tạm dừng',exact:true}).click();
  await expect(cards.nth(0)).toContainText('Đang yêu cầu tạm dừng'); await expect(page.getByRole('status')).toContainText('Đang yêu cầu tạm dừng');
  await cards.nth(1).getByRole('button',{name:'Thử lại',exact:true}).click(); await expect(cards.nth(1)).toContainText('Đang chờ');
  await expect(cards.nth(2).getByRole('button')).toHaveCount(0); await expect(cards.nth(2)).toContainText('đối chiếu');
  await expect(page.getByText('Chưa biết ĐỘ PHỦ')).toBeVisible();
});
test('test_backup_restore_preview',async({page})=>{
  const state=await fixture(page); await harness(page,'StorageView'); const create=page.getByRole('button',{name:'Tạo bản sao lưu',exact:true});
  await create.click(); await expect(page.getByRole('status')).toContainText('Đã tạo');
  expect(state.requests.find(r=>r.method==='POST')).toMatchObject({path:'/api/v1/backups',body:{},csrf:'synthetic-csrf'});
  await expect(page.getByText(/chưa kiểm tra checksum/)).toBeVisible(); await page.getByRole('button',{name:'Hướng dẫn khôi phục'}).click();
  await expect(page.getByText(/Dữ liệu và sao lưu/)).toBeVisible(); await expect(page.getByText(/Hủy/).first()).toBeVisible();
  expect(state.requests.some(r=>r.path.includes('/native/'))).toBe(false);
});
test('test_focus_and_mobile',async({page})=>{
  const state=await fixture(page); let release; state.docGate=new Promise(resolve=>release=resolve); await harness(page,'DocumentationView');
  const trigger=page.getByRole('button',{name:/Hướng dẫn sử dụng/}); await trigger.click(); await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape'); await expect(trigger).toBeFocused(); release();
  await expect(page.getByRole('dialog')).toHaveCount(0); await page.waitForTimeout(200); await expect(page.getByRole('dialog')).toHaveCount(0);
});
test('support download fetches a fresh restricted page and denies export after auth failure',async({page})=>{
  const state=await fixture(page); await harness(page,'AuditView'); const downloadPromise=page.waitForEvent('download'); await page.getByRole('button',{name:'Xuất CSV'}).click(); const download=await downloadPromise; const bytes=await readFile(await download.path());
  expect([...bytes.subarray(0,3)]).toEqual([239,187,191]); const csv=bytes.toString('utf8'); expect(csv).toContain('Thời gian'); expect(csv).toContain('dashboard_ai_provider_changed'); expect(csv).not.toContain('PRIVATE_CACHED_TEXT'); expect(csv).not.toContain(A); expect(state.requests.at(-1).search).toContain('support_export=true');
  let count=0; page.on('download',()=>count++); state.projectionStatus=401; await page.getByRole('button',{name:'Xuất CSV'}).click(); await expect.poll(()=>page.evaluate(()=>window.withdrawn)).toBe(true); expect(count).toBe(0);
});
test('worker null CPU stays unknown and zero stays measured zero',async({page})=>{
  const state=await fixture(page); await harness(page,'WorkersView'); await expect(page.locator('.ops-resource-strip').getByText('Chưa biết',{exact:true}).first()).toBeVisible(); await expect(page.getByText(/cấu hình/).first()).toBeVisible();
  state.cpu=0; await page.evaluate(()=>window.renderManagement({refreshKey:1})); await expect(page.getByText('0.0%',{exact:true})).toBeVisible();
});

test('denied quota and efficiency retain labeled drafts; server response governs accepted route',async({page})=>{
  const state=await fixture(page); await harness(page,'GroupDetailView',{chatId:A});
  await page.getByLabel('Số ngày giữ dữ liệu',{exact:true}).fill('7'); await page.getByRole('button',{name:'Lưu retention & quota'}).click();
  await expect(page.getByLabel('Số ngày giữ dữ liệu',{exact:true})).toHaveValue('7'); await expect(page.getByText(/Bản nháp chưa lưu.*Retention máy chủ: 30/)).toBeVisible();
  await page.getByRole('combobox',{name:'Preset nguồn',exact:true}).selectOption('balanced'); await page.getByRole('button',{name:'Lưu hiệu quả AI'}).click(); await expect(page.getByText(/Bản nháp chưa lưu.*Hiệu quả AI/)).toBeVisible();
  await page.route('**/api/v1/groups/*/ai-route',route=>route.fulfill({json:{...group.policy,ai_mode:'local_first'}}));
  await page.route('**/api/v1/groups/'+A,route=>route.fulfill({status:503,json:{}}));
  await page.getByLabel(/^AI mode/).selectOption('off'); await page.getByRole('button',{name:'Lưu AI route'}).click(); await expect(page.getByLabel(/^AI mode/)).toHaveValue('local_first');
  expect(state.requests.filter(r=>r.path.endsWith('/limits'))[0].body.retention_days).toBe(7);
});

for (const [status,code,message] of [[409,'backup_in_progress','Đang có bản sao lưu'],[503,'maintenance_in_progress','bảo trì'],[503,'backup_failed','Chưa tạo được'],[202,null,'Chưa xác nhận hoàn tất']]) test(`backup ${status}/${code} never claims completion`,async({page})=>{
  const state=await fixture(page); state.backupStatus=status; state.backupCode=code; await harness(page,'StorageView');
  await page.getByRole('button',{name:'Tạo bản sao lưu',exact:true}).click(); await expect(page.getByRole('alert')).toContainText(message); await expect(page.getByRole('status')).toHaveCount(0);
});
test('backup stays busy and never duplicates a pending write',async({page})=>{
  const state=await fixture(page); let release; state.backupGate=new Promise(resolve=>release=resolve); await harness(page,'StorageView');
  await page.getByRole('button',{name:'Tạo bản sao lưu',exact:true}).click(); await expect(page.getByRole('button',{name:'Đang tạo bản sao lưu…'})).toBeDisabled(); await expect(page.getByRole('status')).toHaveCount(0);
  expect(state.requests.filter(r=>r.method==='POST')).toHaveLength(1); release(); await expect(page.getByRole('status')).toContainText('Đã tạo');
});
for (const status of [403,409,429,500,0]) test(`write ${status} has safe actionable guidance without raw rejected details`,async({page})=>{
  await fixture(page); await page.route('**/api/v1/ai/provider',route=>status?route.fulfill({status,json:{detail:[{input:'PRIVATE_REJECTED_INPUT',msg:'PRIVATE_REJECTED_INPUT'}]}}):route.abort('failed'));
  await harness(page,'AiView'); await page.getByRole('radio',{name:'OPENAI',exact:true}).click(); await page.getByRole('button',{name:'Xác nhận chuyển'}).click();
  await expect(page.getByRole('alert').first()).toContainText(/tải lại|Tải lại/); await expect(page.locator('body')).not.toContainText('PRIVATE_REJECTED_INPUT'); await expect(page.locator('body')).not.toContainText('[object Object]');
});
test('models refuse busy ESC/backdrop, show failures, restore focus, and cancel through their own route',async({page})=>{
  await fixture(page); let download={id:7,status:'running',payload:{model:'test-model'}};
  const requests=[]; let release; const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/v1/ollama/**',async route=>{
    const path=new URL(route.request().url()).pathname; requests.push(path);
    if(path.endsWith('/downloads')) return route.fulfill({json:{items:[download]}});
    if(path.endsWith('/cancel')) {download={...download,status:'cancel_requested'};return route.fulfill({json:download});}
    if(path.endsWith('/activate-preview')) return route.fulfill({json:{requested:{chat_model:'test-chat',embedding_model:'test-embed'},requires_reindex:true,message:'Đổi kho vector; cần kiểm tra dữ liệu.',affected_sources:1,affected_vectors:100}});
    if(path.endsWith('/activate')) {await gate;return route.fulfill({status:409,json:{detail:{code:'model_unavailable'}}});}
    return route.fulfill({json:{items:[{name:'test-chat',capabilities:['chat']},{name:'test-embed',capabilities:['embedding']}]}});
  });
  await harness(page,'ModelsView'); await page.getByRole('button',{name:'Hủy tải',exact:true}).click(); await expect(page.getByRole('button',{name:'Đang hủy…'})).toBeDisabled(); expect(requests.filter(path=>path.endsWith('/cancel'))).toEqual(['/api/v1/ollama/downloads/7/cancel']);
  const trigger=page.getByRole('button',{name:'Xem ảnh hưởng',exact:true}); await trigger.click(); const dialog=page.getByRole('dialog'); await expect(dialog).toBeVisible();
  await dialog.getByRole('button',{name:'Đóng',exact:true}).focus(); await page.keyboard.press('Shift+Tab'); await expect(dialog.getByRole('button',{name:'Xác nhận kích hoạt'})).toBeFocused(); await page.keyboard.press('Tab'); await expect(dialog.getByRole('button',{name:'Đóng',exact:true})).toBeFocused();
  await dialog.getByRole('button',{name:'Xác nhận kích hoạt'}).click(); await page.keyboard.press('Escape'); await page.locator('.modal-backdrop').click({position:{x:3,y:3}}); await expect(dialog).toBeVisible(); await expect(dialog.getByRole('button',{name:'Đang kích hoạt…'})).toBeDisabled(); release();
  await expect(dialog.getByRole('alert')).toContainText('Windows'); await page.keyboard.press('Escape'); await expect(dialog).toHaveCount(0); await expect(trigger).toBeFocused();
});
test('late document A cannot overwrite document B',async({page})=>{
  await fixture(page); let release; const gate=new Promise(resolve=>release=resolve);
  await page.route('**/api/v1/docs/user-guide',async route=>{await gate;await route.fulfill({json:{id:'user-guide',name:'Old A',content:'Old A'}})});
  await page.route('**/api/v1/docs/second',route=>route.fulfill({json:{id:'second',name:'Current B',content:'Current B'}}));
  await harness(page,'DocumentationView'); await page.getByRole('button',{name:/Hướng dẫn sử dụng/}).click(); await page.keyboard.press('Escape'); await page.getByRole('button',{name:/Tài liệu thứ hai/}).click(); await expect(page.getByRole('dialog')).toContainText('Current B'); release(); await page.waitForTimeout(150); await expect(page.getByRole('dialog')).not.toContainText('Old A');
});
test('support ignores a late projection when filters or current authority change',async({page})=>{
  await fixture(page); let release; const gate=new Promise(resolve=>release=resolve); let started; const arrived=new Promise(resolve=>started=resolve); let count=0;page.on('download',()=>count++);
  await page.route('**/api/v1/audit*',async route=>{if(new URL(route.request().url()).searchParams.has('support_export')) {started();await gate;} await route.fulfill({json:{items:[],total:0}})});
  await harness(page,'AuditView'); await page.getByRole('button',{name:'Xuất CSV'}).click(); await arrived; await page.getByPlaceholder('Tìm action, target hoặc lý do…').fill('new filter'); release(); await expect(page.getByRole('button',{name:'Xuất CSV'})).toBeEnabled(); expect(count).toBe(0);
});
for(const width of [360,390,1280,1440]) test(`management focus and targets at ${width}`,async({page})=>{
  await page.setViewportSize({width,height:900}); const state=await fixture(page); let release; state.docGate=new Promise(resolve=>release=resolve); await harness(page,'StorageView'); await expect(page.getByRole('button',{name:'Tạo bản sao lưu',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  for(const button of await page.getByRole('region',{name:'Sao lưu và khôi phục'}).getByRole('button').all()) expect((await button.boundingBox()).height).toBeGreaterThanOrEqual(44);
  await harness(page,'DocumentationView'); const trigger=page.getByRole('button',{name:/Hướng dẫn sử dụng/}); await trigger.click(); const dialog=page.getByRole('dialog'); await expect(dialog).toBeVisible();
  const copy=dialog.getByRole('button',{name:'Sao chép tài liệu'}); await expect(copy).toBeDisabled(); release(); await expect(copy).toBeEnabled();
  await copy.focus(); await page.keyboard.press('Shift+Tab'); await expect(dialog.getByPlaceholder('Tìm trong tài liệu…')).toBeFocused(); await page.keyboard.press('Tab'); await expect(copy).toBeFocused();
  for(const button of await dialog.getByRole('button').all()) expect((await button.boundingBox()).height).toBeGreaterThanOrEqual(44);
  await page.keyboard.press('Escape'); await expect(trigger).toBeFocused();
  await harness(page,'GroupDetailView',{chatId:A});await expect(page.getByText(A,{exact:false}).first()).toBeVisible();await expect(page.getByLabel(/^AI mode/).locator('option')).toHaveCount(6);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  for(const control of await page.locator('.group-detail button,.group-detail input,.group-detail select').all()) {if(await control.isVisible()) expect(await control.evaluate(el=>(el.matches('input[type=checkbox]') ? el.closest('label') : el).getBoundingClientRect().height), await control.evaluate(el=>el.outerHTML.slice(0,160))).toBeGreaterThanOrEqual(44);}
});

async function actionFixture(page) {
  await page.addInitScript(()=>{window.EventSource=class {addEventListener() {} close() {}};});
  await fixture(page);
  await page.route('**/api/v1/auth/session',route=>route.fulfill({json:{authenticated:true,authority:'management',owner_id:'9007199254740993',profile_id:'test-profile',csrf_token:'synthetic-csrf'}}));
  const action={action_id:'test-action',status:'pending',action_type:'set_chat_permission',chat_id:A,preview:'Preview quyền; chưa áp dụng.',payload:{permission:'read_messages',enabled:true},expires_at:new Date(Date.now()+300000).toISOString()};
  await page.route('**/api/v1/pending-actions?*',route=>route.fulfill({json:{items:[action]}}));
  await harness(page,'App'); await page.getByRole('button',{name:'Công việc',exact:true}).click(); await page.getByRole('button',{name:'Xem',exact:true}).click();
  return action;
}
test('pending action network uncertainty withdraws resend while cancellation uses its real endpoint',async({page})=>{
  const action=await actionFixture(page); let confirmations=0;
  await page.route('**/api/v1/pending-actions/*/confirm',route=>{confirmations++;return route.abort('failed')});
  const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'Xác nhận thực thi'}).click();
  await expect(dialog).toContainText(/chưa rõ|Chưa rõ/);await expect(dialog.getByRole('button',{name:'Xác nhận thực thi'})).toHaveCount(0); expect(confirmations).toBe(1);
  await dialog.getByRole('button',{name:'Đóng chi tiết'}).click();
  await page.getByRole('button',{name:'Xem',exact:true}).click(); await dialog.getByRole('button',{name:'Đối chiếu hành động'}).click(); let cancellation;
  await page.route('**/api/v1/pending-actions/*/cancel',route=>{cancellation=new URL(route.request().url()).pathname;return route.fulfill({json:{...action,status:'cancelled'}})});
  await dialog.getByRole('button',{name:'Hủy yêu cầu'}).click(); await expect(dialog).toHaveCount(0);expect(cancellation).toBe('/api/v1/pending-actions/test-action/cancel');
});
test('support late success after another request withdraws auth cannot download',async({page})=>{
  await fixture(page);let release,started;const arrived=new Promise(resolve=>started=resolve),gate=new Promise(resolve=>release=resolve);let downloads=0;page.on('download',()=>downloads++);
  await page.route('**/api/v1/audit*',async route=>{if(new URL(route.request().url()).searchParams.has('support_export')) {started();await gate;}await route.fulfill({json:{items:[],total:0}})});
  await harness(page,'AuditView');await page.getByRole('button',{name:'Xuất CSV'}).click();await arrived;
  await page.route('**/api/v1/workers',route=>route.fulfill({status:401,json:{}}));await page.evaluate(()=>window.api.workers().catch(()=>{}));release();await expect(page.getByRole('button',{name:'Xuất CSV'})).toBeEnabled();expect(downloads).toBe(0);
});
test('CSV quotes every cell, neutralizes formulas, and omits extra fields',async({page})=>{
  await fixture(page);await page.route('**/api/v1/audit*',route=>route.fulfill({json:{items:[{occurred_at:'2026-10-10T00:00:00Z',action:'=SUM(1,2)',target_type:'quote"type',outcome:'\t+formula',reason:'OMIT_EXTRA_FIELD'}],total:1}}));
  await harness(page,'AuditView');const pending=page.waitForEvent('download');await page.getByRole('button',{name:'Xuất CSV'}).click();const csv=(await readFile(await (await pending).path())).toString('utf8');
  expect(csv).toContain('"\'=SUM(1,2)"');expect(csv).toContain('"quote""type"');expect(csv).toContain('"\'\t+formula"');expect(csv).not.toContain('OMIT_EXTRA_FIELD');
});
test('403 projection denial produces no support file',async({page})=>{
  const state=await fixture(page);state.projectionStatus=403;let downloads=0;page.on('download',()=>downloads++);await harness(page,'AuditView');await page.getByRole('button',{name:'Xuất CSV'}).click();await expect(page.getByRole('alert')).toContainText('quyền');expect(downloads).toBe(0);
});
test('malformed response cannot echo response content and a malformed 401 still withdraws auth',async({page})=>{
  await fixture(page);await page.route('**/api/v1/ai/provider',route=>route.fulfill({status:500,contentType:'application/json',body:'PRIVATE_INVALID_JSON'}));await harness(page,'AiView');await page.getByRole('radio',{name:'OPENAI',exact:true}).click();await page.getByRole('button',{name:'Xác nhận chuyển'}).click();
  await expect(page.locator('body')).not.toContainText('PRIVATE_INVALID_JSON');await expect(page.getByRole('alert').first()).toContainText('Windows');
  await page.route('**/api/v1/workers',route=>route.fulfill({status:401,contentType:'application/json',body:'PRIVATE_INVALID_JSON'}));await page.evaluate(()=>window.api.workers().catch(()=>{}));expect(await page.evaluate(()=>window.withdrawn)).toBe(true);
});
test('accepted pause remains pause_requested when the subsequent refresh fails',async({page})=>{
  await fixture(page); await harness(page,'KnowledgeView');await expect(page.locator('.learning-job-card').first()).toContainText('Đang chạy');
  await page.route('**/api/v1/learning-jobs?*',route=>route.fulfill({status:503,json:{}}));await page.locator('.learning-job-card').first().getByRole('button',{name:'Tạm dừng',exact:true}).click();
  await expect(page.locator('.learning-job-card').first()).toContainText('Đang yêu cầu tạm dừng');await expect(page.locator('.learning-job-card').first().getByRole('button')).toHaveCount(0);await expect(page.getByRole('alert')).toBeVisible();
});
test('missing memory/storage measurements are unknown, actual zero remains zero',async({page})=>{
  await fixture(page);await harness(page,'WorkersView');await expect(page.locator('.ops-resource-strip > div').first()).toContainText('Chưa biết');await harness(page,'StorageView');await expect(page.locator('.ops-storage-hero h2')).toHaveText('Chưa biết');
  await page.route('**/api/v1/storage',route=>route.fulfill({json:{current:{data_bytes:0,vector_bytes:0,media_bytes:0}}}));await page.evaluate(()=>window.renderManagement({refreshKey:1}));await expect(page.locator('.ops-storage-hero h2')).toHaveText('0 B');
});
test('permission write remains a pending preview without applying the permission',async({page})=>{
  await fixture(page);let sent;await page.route('**/api/v1/groups/*/permissions',route=>{sent=route.request().postDataJSON();return route.fulfill({json:{action_id:'permission-preview',status:'pending',chat_id:A,preview:'Owner cần xác nhận'}})});await harness(page,'GroupDetailView',{chatId:A});
  const permission=page.locator('.permission-matrix [role="switch"]').first();const name=await permission.getAttribute('aria-label');await permission.click();await expect(permission).toHaveAttribute('aria-checked','false');expect(sent).toEqual({permission:name,enabled:true});await expect.poll(()=>page.evaluate(()=>window.createdAction?.status)).toBe('pending');
});

for(const status of [0,500]) test(`fix1 action ${status} uncertainty survives overview reopen until authoritative reconciliation`,async({page})=>{
  const action=await actionFixture(page);await page.getByRole('dialog').getByRole('button',{name:'Đóng',exact:true}).click();await page.getByRole('button',{name:'Tổng quan',exact:true}).click();await page.getByRole('button',{name:'Xem & duyệt',exact:true}).click();
  let writes=0;await page.route('**/api/v1/pending-actions/*/confirm',route=>{writes++;return status?route.fulfill({status,json:{}}):route.abort('failed')});
  await page.route('**/api/v1/pending-actions?*',route=>route.fulfill({status:503,json:{}}));const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'Xác nhận thực thi'}).click();await expect(dialog.getByRole('button',{name:'Đóng chi tiết'})).toBeVisible();await dialog.getByRole('button',{name:'Đóng chi tiết'}).click();await page.getByRole('button',{name:'Xem & duyệt',exact:true}).click();await expect(dialog.getByRole('button',{name:'Xác nhận thực thi'})).toHaveCount(0);
  await dialog.getByRole('button',{name:'Đối chiếu hành động'}).click();await expect(dialog.getByRole('button',{name:'Đối chiếu hành động'})).toBeEnabled();await expect(dialog.getByRole('alert')).toBeVisible();await expect(dialog.getByRole('button',{name:'Xác nhận thực thi'})).toHaveCount(0);
  let release;const gate=new Promise(resolve=>release=resolve);await page.route('**/api/v1/pending-actions?*',async route=>{await gate;await route.fulfill({json:{items:[action]}})});await dialog.getByRole('button',{name:'Đối chiếu hành động'}).click();await expect(dialog.getByRole('button',{name:'Xác nhận thực thi'})).toHaveCount(0);expect(writes).toBe(1);release();await expect(dialog.getByRole('button',{name:'Xác nhận thực thi'})).toBeEnabled();
});
for(const status of [0,500]) test(`fix1 model ${status} uncertainty requires fresh reads and renewed preview`,async({page})=>{
  await fixture(page);let failRead=false,readGate=null,writes=0,previews=0;
  await page.route('**/api/v1/ollama/**',async route=>{const path=new URL(route.request().url()).pathname;if(path.endsWith('/downloads'))return route.fulfill({json:{items:[]}});if(path.endsWith('/activate-preview')){previews++;return route.fulfill({json:{requested:{chat_model:'chat',embedding_model:'embed'},requires_reindex:true,message:'Preview'}})}if(path.endsWith('/activate')){writes++;return status?route.fulfill({status,json:{}}):route.abort('failed')}if(readGate)await readGate;return route.fulfill(failRead?{status:503,json:{}}:{json:{items:[{name:'chat',capabilities:['chat']},{name:'embed',capabilities:['embedding']}]}})});
  await harness(page,'ModelsView');const trigger=page.getByRole('button',{name:'Xem ảnh hưởng',exact:true});await trigger.click();const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'Xác nhận kích hoạt'}).click();await expect(dialog.getByRole('alert')).toBeVisible();await expect(dialog.getByRole('button',{name:'Xác nhận kích hoạt'})).toBeDisabled();
  await page.keyboard.press('Escape');await expect(trigger).toBeDisabled();await expect(page.getByRole('button',{name:'Đối chiếu cấu hình model'})).toBeFocused();failRead=true;await page.getByRole('button',{name:'Đối chiếu cấu hình model'}).click();await expect(page.getByRole('button',{name:'Đối chiếu cấu hình model'})).toBeEnabled();await expect(page.getByRole('alert')).toContainText('Chưa đọc');await expect(trigger).toBeDisabled();
  failRead=false;let release;readGate=new Promise(resolve=>release=resolve);await page.getByRole('button',{name:'Đối chiếu cấu hình model'}).click();await expect(trigger).toBeDisabled();expect(writes).toBe(1);expect(previews).toBe(1);release();await expect(trigger).toBeEnabled();await expect(trigger).toBeFocused();await trigger.click();await expect(dialog.getByRole('button',{name:'Xác nhận kích hoạt'})).toBeEnabled();expect(previews).toBe(2);expect(writes).toBe(1);
});
for(const status of [0,500]) test(`fix1 learning ${status} uncertainty survives failed and delayed reads`,async({page})=>{
  const state=await fixture(page);let writes=0;await page.route('**/api/v1/learning-jobs/2/retry',route=>{writes++;return status?route.fulfill({status,json:{}}):route.abort('failed')});await harness(page,'KnowledgeView');const card=page.locator('.learning-job-card').nth(1);await card.getByRole('button',{name:'Thử lại',exact:true}).click();await expect(card.getByRole('button',{name:'Thử lại',exact:true})).toBeDisabled();await expect(card).toContainText('Chưa rõ');
  await page.route('**/api/v1/learning-jobs?*',route=>route.fulfill({status:503,json:{}}));await card.getByRole('button',{name:'Đối chiếu job'}).click();await expect(card.getByRole('button',{name:'Thử lại',exact:true})).toBeDisabled();await expect(card.getByRole('button',{name:'Đối chiếu job'})).toBeEnabled();await expect(card.getByRole('alert')).toContainText('Chưa đọc');
  let release;const gate=new Promise(resolve=>release=resolve);await page.route('**/api/v1/learning-jobs?*',async route=>{await gate;await route.fulfill({json:{items:state.jobs}})});await card.getByRole('button',{name:'Đối chiếu job'}).click();await expect(card.getByRole('button',{name:'Thử lại',exact:true})).toBeDisabled();expect(writes).toBe(1);release();await expect(card.getByRole('button',{name:'Thử lại',exact:true})).toBeEnabled();
});






test('fix1 action late reconciliation cannot reopen after session withdrawal',async({page})=>{
  const action=await actionFixture(page);const dialog=page.getByRole('dialog');await page.route('**/api/v1/pending-actions/*/confirm',route=>route.abort('failed'));await dialog.getByRole('button',{name:'Xác nhận thực thi'}).click();await expect(dialog.getByRole('button',{name:'Đối chiếu hành động'})).toBeEnabled();
  let release,started;const gate=new Promise(resolve=>release=resolve),arrived=new Promise(resolve=>started=resolve);await page.route('**/api/v1/pending-actions?*',async route=>{started();await gate;await route.fulfill({json:{items:[action]}})});await dialog.getByRole('button',{name:'Đối chiếu hành động'}).click();await arrived;
  await page.route('**/api/v1/workers',route=>route.fulfill({status:401,json:{}}));await page.evaluate(()=>window.api.workers().catch(()=>{}));release();await expect(page.getByRole('heading',{name:'Mở từ ứng dụng Windows'})).toBeVisible();await expect(dialog).toHaveCount(0);
});

test('fix1 model config failure and late current-auth read preserve uncertainty',async({page})=>{
  await fixture(page);await page.route('**/api/v1/ollama/**',route=>{const path=new URL(route.request().url()).pathname;if(path.endsWith('/downloads'))return route.fulfill({json:{items:[]}});if(path.endsWith('/activate-preview'))return route.fulfill({json:{requested:{chat_model:'chat',embedding_model:'embed'},requires_reindex:true,message:'Preview'}});if(path.endsWith('/activate'))return route.abort('failed');return route.fulfill({json:{items:[{name:'chat',capabilities:['chat']},{name:'embed',capabilities:['embedding']}]}})});
  await harness(page,'ModelsView');await page.getByRole('button',{name:'Xem ảnh hưởng',exact:true}).click();const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'Xác nhận kích hoạt'}).click();await expect(dialog.getByRole('button',{name:'Đối chiếu cấu hình model'})).toBeEnabled();await page.route('**/api/v1/ai/config',route=>route.fulfill({status:503,json:{}}));await dialog.getByRole('button',{name:'Đối chiếu cấu hình model'}).click();await expect(dialog.getByRole('button',{name:'Đối chiếu cấu hình model'})).toBeEnabled();await expect(dialog.getByRole('button',{name:'Xác nhận kích hoạt'})).toBeDisabled();
  let release,started;const gate=new Promise(resolve=>release=resolve),arrived=new Promise(resolve=>started=resolve);await page.route('**/api/v1/ai/config',async route=>{started();await gate;await route.fulfill({json:{provider:'ollama'}})});await dialog.getByRole('button',{name:'Đối chiếu cấu hình model'}).click();await arrived;
  await page.route('**/api/v1/workers',route=>route.fulfill({status:401,json:{}}));await page.evaluate(()=>window.api.workers().catch(()=>{}));release();await expect(dialog.getByRole('button',{name:'Đối chiếu cấu hình model'})).toBeEnabled();await expect(dialog.getByRole('button',{name:'Xác nhận kích hoạt'})).toBeDisabled();await expect(dialog.getByRole('alert')).toContainText('Phiên');expect(await page.evaluate(()=>window.withdrawn)).toBe(true);
});

test('fix1 learning late reconciliation after auth loss cannot restore controls',async({page})=>{
  const state=await fixture(page);await page.route('**/api/v1/learning-jobs/2/retry',route=>route.abort('failed'));await harness(page,'KnowledgeView');const card=page.locator('.learning-job-card').nth(1);await card.getByRole('button',{name:'Thử lại',exact:true}).click();await expect(card.getByRole('button',{name:'Đối chiếu job'})).toBeEnabled();await expect(page.getByRole('button',{name:'Pause tất cả'})).toBeDisabled();await expect(page.getByRole('button',{name:'Resume tất cả'})).toBeDisabled();
  let release,started;const gate=new Promise(resolve=>release=resolve),arrived=new Promise(resolve=>started=resolve);await page.route('**/api/v1/learning-jobs?*',async route=>{started();await gate;await route.fulfill({json:{items:state.jobs}})});await card.getByRole('button',{name:'Đối chiếu job'}).click();await arrived;
  await page.route('**/api/v1/workers',route=>route.fulfill({status:401,json:{}}));await page.evaluate(()=>window.api.workers().catch(()=>{}));release();await expect(card.getByRole('button',{name:'Đối chiếu job'})).toBeEnabled();await expect(card.getByRole('button',{name:'Thử lại',exact:true})).toBeDisabled();await expect(card.getByRole('alert')).toContainText('Chưa đọc');expect(await page.evaluate(()=>window.withdrawn)).toBe(true);
});

test('fix1 reconciled terminal action cannot regress through a retained overview row',async({page})=>{
  const action=await actionFixture(page);const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'Đóng',exact:true}).click();await page.getByRole('button',{name:'Tổng quan',exact:true}).click();await page.getByRole('button',{name:'Xem & duyệt',exact:true}).click();await page.route('**/api/v1/pending-actions/*/confirm',route=>route.abort('failed'));await page.route('**/api/v1/pending-actions?*',route=>route.fulfill({status:503,json:{}}));await dialog.getByRole('button',{name:'Xác nhận thực thi'}).click();await expect(dialog.getByRole('button',{name:'Đối chiếu hành động'})).toBeEnabled();
  await page.route('**/api/v1/pending-actions?*',route=>route.fulfill({json:{items:[{...action,status:'confirmed'}]}}));await dialog.getByRole('button',{name:'Đối chiếu hành động'}).click();await expect(dialog.getByRole('button',{name:'Đối chiếu hành động'})).toHaveCount(0);await dialog.getByRole('button',{name:'Đóng chi tiết'}).click();await page.getByRole('button',{name:'Xem & duyệt',exact:true}).click();await expect(dialog.getByRole('button',{name:'Xác nhận thực thi'})).toHaveCount(0);await expect(dialog.getByRole('button',{name:'Đóng chi tiết'})).toBeVisible();
});
