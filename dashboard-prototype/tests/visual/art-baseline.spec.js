import { expect } from "@playwright/test";
import { test } from './browser-errors.js';
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";

const base = process.env.ART_BASE_URL || "http://127.0.0.1:5174";
const phase = process.env.ART_PHASE || "after";
const evidence = process.env.ART_EVIDENCE_DIR
  ? path.resolve(process.env.ART_EVIDENCE_DIR)
  : path.resolve("../docs/handoff/evidence/u01", phase);
mkdirSync(evidence, { recursive: true });
// CI uses Playwright's installed Chromium; local evidence keeps actual Chrome.
if (!process.env.CI) test.use({ channel: process.env.ART_BROWSER_CHANNEL || 'chrome' });

// An isolated rendering of production primitives, never an operational dashboard.
const fixture = `<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/src/styles.css"></head><body><div id="fixture"></div><script type="module">
window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
const {default:React}=await import('/node_modules/.vite/deps/react.js');
import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
const {IconButton,Toggle,Badge,PanelHeader}=await import('/src/ui.jsx');
import {useDialogA11y} from '/src/useDialogA11y.js';
function Art(){const [on,setOn]=React.useState(false),[open,setOpen]=React.useState(false);const ref=useDialogA11y(open,()=>setOpen(false));return React.createElement('main',{style:{padding:20,maxWidth:880,margin:'auto'}},
React.createElement('h1',{className:'art-title',style:{fontFamily:'var(--font-display)'}},'Telegram AI'),
React.createElement('section',{className:'panel'},React.createElement(PanelHeader,{eyebrow:'KIỂM TRA ART · KHÔNG PHẢI DỮ LIỆU VẬN HÀNH',title:'Điều khiển và bàn phím'}),React.createElement('div',{style:{padding:20,display:'flex',flexWrap:'wrap',gap:18}},
React.createElement('button',{className:'button button--primary button--small',onClick:()=>setOpen(true)},'Mở hộp thoại'),React.createElement(IconButton,{label:'Trợ giúp'},'?'),React.createElement(Toggle,{active:on,onChange:setOn,label:'Bật điều khiển'}),...['teal','magenta','yellow'].map(t=>React.createElement(Badge,{key:t,tone:t},t)))),
open&&React.createElement('div',{className:'modal-backdrop'},React.createElement('section',{className:'modal modal-shell',role:'dialog','aria-modal':true,'aria-label':'Xác nhận art',tabIndex:-1,ref},React.createElement('header',{className:'modal-header'},React.createElement('h2',null,'Xác nhận art'),React.createElement(IconButton,{label:'Đóng',onClick:()=>setOpen(false)},'×')),React.createElement('div',{className:'modal-body'},'Đây là câu tiếng Việt dài để kiểm tra bố cục: hãy xem nội dung, dùng bàn phím để di chuyển giữa các điều khiển và đóng hộp thoại trước khi tiếp tục.'),React.createElement('footer',{className:'modal-footer'},React.createElement('button',{className:'button button--outline',onClick:()=>setOpen(false)},'Quay lại')))))}
ReactDOM.createRoot(document.getElementById('fixture')).render(React.createElement(Art));</script></body></html>`;

async function render(page) {
  if (process.env.ART_STYLE_OVERRIDE) {
    await page.route(`${base}/src/styles.css`, route => route.fulfill({
      contentType: 'text/css', body: readFileSync(process.env.ART_STYLE_OVERRIDE, 'utf8'),
    }));
  }
  await page.route(`${base}/__art_fixture`, route => route.fulfill({ contentType: "text/html", body: fixture }));
  await page.goto(`${base}/__art_fixture`);
  await expect(page.getByRole("heading", { name: "Điều khiển và bàn phím" })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

for (const width of [360, 390, 1280, 1440]) {
  test(`art geometry and targets at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await render(page);
    await page.screenshot({ path: path.join(evidence, `browser-components-${width}.png`), fullPage: true });
    await expect(page.locator('.panel')).toHaveCSS('background-color', 'rgb(255, 253, 245)');
    await expect(page.locator('.panel')).toHaveCSS('border-top-width', '3px');
    await expect(page.locator('.panel')).toHaveCSS('border-radius', '0px');
    await expect(page.locator('.panel')).toHaveCSS('box-shadow', 'rgb(9, 9, 9) 7px 7px 0px 0px');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const button of await page.getByRole('button').all()) {
      const box = await button.boundingBox();
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    const root = await page.evaluate(() => {
      const s = getComputedStyle(document.documentElement);
      return ['--paper','--ink','--teal','--magenta','--yellow'].map(k=>s.getPropertyValue(k).trim());
    });
    expect(root).toEqual(['#f3efdf','#090909','#00c8c8','#ef00c8','#ffd51f']);
    await expect(page.locator('.panel-heading h2')).toHaveCSS('font-family', /Darley Sans/);
    await expect(page.getByRole('button',{name:'Trợ giúp'})).toHaveAccessibleName('Trợ giúp');
    await expect(page.getByRole('switch')).toHaveAccessibleName('Bật điều khiển');
    for (const control of await page.locator('button').all()) {
      await expect(control).toHaveCSS('font-family', /Darley Sans/);
    }
    expect(await page.evaluate(() => document.fonts.check('16px "Darley Sans"') && document.fonts.check('16px "Peter Obscure"'))).toBe(true);
    await expect(page.getByRole('heading',{name:'Telegram AI'})).toHaveCSS('font-family', /Peter Obscure/);
    await expect(page.getByRole('switch')).toHaveAttribute('aria-checked','false');
    await page.getByRole('switch').click();
    await expect(page.getByRole('switch')).toHaveAttribute('aria-checked','true');
    await expect(page.getByRole('switch')).toHaveText('ON');
  });
  test(`login reference at ${width}px`, async ({page}) => {
    await page.setViewportSize({ width, height: 900 });
    // Exercise the real unauthenticated screen without credentials or backend data.
    // Reject the isolated fixture's API fetch in memory. Unlike route.abort(),
    // this does not create browser resource errors that could mask a real error.
    await page.addInitScript(() => {
      const fetch = window.fetch.bind(window);
      window.fetch = (input, options) => {
        const url = typeof input === 'string' ? input : input.url;
        return new URL(url, location.href).pathname.startsWith('/api/v1/')
          ? Promise.reject(new TypeError('Isolated unauthenticated art fixture'))
          : fetch(input, options);
      };
    });
    await page.route('**/api/v1/**', route => route.abort());
    await page.goto(base);
    const nativeHeading = page.getByRole('heading', { name: 'Mở từ ứng dụng Windows' });
    const retry = page.getByRole('button', { name: 'Kiểm tra lại phiên' });
    await expect(nativeHeading).toBeVisible();
    await expect(page.locator('.login-form > p:not(.eyebrow)')).toContainText('bấm “Mở dashboard”');
    await expect(page.locator('.login-form > p:not(.eyebrow)')).toContainText('dùng một lần và hết hạn sau 30 giây');
    await expect(page.locator('.login-form > small')).toHaveText('Không nhập API key, OTP Telegram hoặc mật khẩu vào trình duyệt.');
    await expect(page.locator('input,textarea,select')).toHaveCount(0);
    await expect(retry).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({path:path.join(evidence,`browser-login-${width}.png`),fullPage:true});
    await expect(page.locator('.login-shell')).toHaveCSS('background-color','rgb(243, 239, 223)');
    await expect(page.locator('.login-form')).toHaveCSS('background-color','rgb(255, 253, 245)');
    await expect(page.locator('.login-form')).toHaveCSS('border-top-width','3px');
    await expect(page.locator('.login-form')).toHaveCSS('border-top-color','rgb(9, 9, 9)');
    await expect(page.locator('.login-form')).toHaveCSS('border-radius','0px');
    await expect(page.locator('.login-form')).toHaveCSS('box-shadow','rgb(9, 9, 9) 7px 7px 0px 0px');
    await expect(page.locator('.login-poster')).toHaveCSS('background-image','none');
    await expect(page.locator('.login-poster')).toHaveCSS('background-color','rgb(9, 9, 9)');
    await expect(page.locator('.login-copy h1')).toHaveCSS('font-family', /Peter Obscure/);
    await expect(page.locator('.login-brand strong')).toHaveCSS('font-family', /Peter Obscure/);
    for (const copy of await page.locator('.login-form h2,.login-form p,.login-form small,.login-copy p,.login-security-strip span').all()) {
      await expect(copy).toHaveCSS('font-family', /Darley Sans/);
    }
    const loadedFonts = await page.evaluate(() => [...document.fonts].filter(font => font.status === 'loaded').map(font => font.family.replaceAll('"', '')));
    expect(loadedFonts).toEqual(expect.arrayContaining(['Darley Sans', 'Peter Obscure']));
    for (const control of await page.getByRole('button').all()) {
      const box = await control.boundingBox();
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      await expect(control).toHaveCSS('font-family', /Darley Sans/);
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.keyboard.press('Tab');
    await expect(retry).toBeFocused();
    await expect(retry).toHaveCSS('outline-width','3px');
    await expect(retry).toHaveCSS('outline-style','solid');
    await expect(retry).toHaveCSS('outline-color','rgb(9, 9, 9)');
    await Promise.all([page.waitForEvent('load'), page.keyboard.press('Enter')]);
    await expect(nativeHeading).toBeVisible();
    await expect(page.locator('input,textarea,select')).toHaveCount(0);
  });
}

test('keyboard focus, modal trap, Escape and restoration', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await render(page);
  await page.keyboard.press('Tab');
  const opener=page.getByRole('button',{name:'Mở hộp thoại'});
  await expect(opener).toBeFocused();
  await expect(opener).toHaveCSS('outline-width','3px');
  await page.keyboard.press('Enter');
  const close=page.getByRole('button',{name:'Đóng',exact:true});
  await expect(close).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(page.getByRole('button',{name:'Quay lại'})).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(close).toBeFocused();
  // Wait for the real entrance animations before measuring final occlusion.
  await page.locator('.modal').evaluate(async element => {
    await Promise.all(element.getAnimations({subtree:true}).map(animation=>animation.finished));
    await Promise.all(element.parentElement.getAnimations().map(animation=>animation.finished));
  });
  const modalLayout = await page.getByRole('dialog').evaluate(dialog => {
    const box=dialog.getBoundingClientRect();
    const body=dialog.querySelector('.modal-body').getBoundingClientRect();
    const footer=dialog.querySelector('.modal-footer').getBoundingClientRect();
    const style=getComputedStyle(dialog);
    return {background:style.backgroundColor,opacity:style.opacity,top:box.top,bottom:box.bottom,bodyBottom:body.bottom,footerTop:footer.top,footerBottom:footer.bottom,
      topElementInside:dialog.contains(document.elementFromPoint(box.left+10,box.top+10))};
  });
  expect(modalLayout.background).toBe('rgb(255, 253, 245)');
  expect(modalLayout.opacity).toBe('1');
  expect(modalLayout.topElementInside).toBe(true);
  expect(modalLayout.top).toBeGreaterThanOrEqual(0);
  expect(modalLayout.bottom).toBeLessThanOrEqual(844);
  expect(modalLayout.bodyBottom).toBeLessThanOrEqual(modalLayout.footerTop);
  expect(modalLayout.footerBottom).toBeLessThanOrEqual(modalLayout.bottom);
  await page.screenshot({path:path.join(evidence,'browser-dialog-390.png')});
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test('unexpected JavaScript exception fails the fixture', async ({page}) => {
  test.fail(true, 'The browser error gate must reject an injected page exception.');
  await page.setContent('<script>throw new Error("Q01 injected page exception")</script>');
});

test('unexpected console error fails the fixture', async ({page}) => {
  test.fail(true, 'The browser error gate must reject an injected console error.');
  await page.setContent('<script>console.error("Q01 injected console error")</script>');
});
