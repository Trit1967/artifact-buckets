// UI journeys for M3 (Link page), M4 (Connect "Computers that sync"), M8 (welcome).
//   BASE=http://127.0.0.1:8096 node qa-ui/sync/journey_sync.js
// Writes screenshots to qa-ui/sync/shots/ and results to qa-ui/sync/out_journey_sync.json.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.BASE || 'http://127.0.0.1:8096';
const ADMIN_PW = process.env.ADMIN_PW || 'admin-pass-123456';
const PW = 'journey-pass-123456';
const SHOTS = path.join(__dirname, 'shots');
fs.mkdirSync(SHOTS, { recursive: true });
const RUN = Date.now().toString(36).slice(-5);
const results = [];
const issues = [];
function check(name, ok, detail = '') { results.push({ name, ok: !!ok, detail }); if (!ok) console.log('FAIL', name, detail); else console.log('ok  ', name); }

async function api(ctx, method, url, body) {
  // ctx: a Playwright APIRequestContext that holds a session cookie
  const me = await (await ctx.get(BASE + '/api/auth/me')).json();
  const r = await ctx.fetch(BASE + url, { method, headers: { 'content-type': 'application/json', 'x-csrf-token': me.csrf || '' }, data: body ? JSON.stringify(body) : undefined });
  let j = null; try { j = await r.json(); } catch {}
  return { status: r.status(), body: j };
}

async function apiLogin(pw, user, pass) {
  const req = await pw.request.newContext();
  const r = await req.post(BASE + '/api/auth/login', { data: { username: user, password: pass } });
  if (r.status() !== 200) throw new Error('login ' + user + ' ' + r.status());
  return req;
}

async function uiLogin(page, user, pass = PW) {
  await page.getByLabel('Username').fill(user);
  await page.getByLabel('Password').fill(pass);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

async function overflow(page) {
  return page.evaluate(() => {
    const d = document.documentElement;
    const wide = [...document.querySelectorAll('body *')].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > innerWidth + 1; })
      .slice(0, 5).map((e) => `${e.tagName.toLowerCase()}.${[...e.classList].join('.')} right=${Math.round(e.getBoundingClientRect().right)}`);
    return { scroll: d.scrollWidth > d.clientWidth + 1, sw: d.scrollWidth, cw: d.clientWidth, wide };
  });
}

// WCAG contrast of an element's text against the nearest opaque background.
async function contrast(page, selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel); if (!el) return null;
    const parse = (c) => { const m = c.match(/[\d.]+/g).map(Number); return { r: m[0], g: m[1], b: m[2], a: m[3] ?? 1 }; };
    const lum = ({ r, g, b }) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    let bg = null; let n = el;
    while (n && n !== document.documentElement) { const c = parse(getComputedStyle(n).backgroundColor); if (c.a > 0.9) { bg = c; break; } n = n.parentElement; }
    bg = bg || parse(getComputedStyle(document.body).backgroundColor);
    const fg = parse(getComputedStyle(el).color);
    const [L1, L2] = [lum(fg), lum(bg)].sort((a, b) => b - a);
    return Math.round(((L1 + 0.05) / (L2 + 0.05)) * 100) / 100;
  }, selector);
}

(async () => {
  const pw = require('playwright');
  const browser = await chromium.launch();
  const admin = await apiLogin(pw, 'admin', ADMIN_PW);
  const users = { wanda: `wanda${RUN}`, nico: `nico${RUN}`, lena: `lena${RUN}`, otto: `otto${RUN}` };
  for (const u of Object.values(users)) {
    const r = await api(admin, 'POST', '/api/users', { username: u, password: PW, system_role: 'member', display_name: u[0].toUpperCase() + u.slice(1) + ' Q. Tester' });
    if (r.status !== 200 && r.status !== 201) throw new Error('create ' + u + ' ' + r.status);
  }
  const anon = await pw.request.newContext();
  const startLink = async (computer) => (await (await anon.post(BASE + '/api/sync/link/start', { data: { computer } })).json());
  const poll = async (p) => (await (await anon.post(BASE + '/api/sync/link/poll', { data: { poll: p } })).json());
  const putFile = async (tok, rel, where = 'projects', project = 'quarterly-report', body = 'x') => {
    const h = { authorization: `Bearer ${tok}`, 'x-sync-where': where, 'x-sync-path': Buffer.from(rel).toString('base64') };
    if (where === 'projects') h['x-sync-project'] = Buffer.from(project).toString('base64');
    const r = await anon.fetch(BASE + '/api/sync/file', { method: 'PUT', headers: h, data: Buffer.from(body) });
    return r.status();
  };

  for (const vp of [{ w: 1440, h: 900 }, { w: 900, h: 900 }, { w: 390, h: 844 }]) {
    const tag = vp.w;
    const ctx = await browser.newContext({ viewport: { width: vp.w, height: vp.h }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    const errs = [];
    page.on('pageerror', (e) => errs.push(e.message));
    page.on('dialog', (d) => { errs.push('dialog: ' + d.message()); d.dismiss(); });

    // ---- M8: first sign-in lands on the welcome --------------------------------------------
    const wu = `${users.wanda}${tag}`;
    await api(admin, 'POST', '/api/users', { username: wu, password: PW, system_role: 'member', display_name: 'Wanda Wellington-Featherstonehaugh' });
    await page.goto(BASE + '/');
    await uiLogin(page, wu);
    await page.waitForURL(/\/welcome/, { timeout: 8000 }).catch(() => {});
    check(`[${tag}] first sign-in goes to /welcome`, page.url().includes('/welcome'), page.url());
    for (const step of ['hello', 'connect', 'work', 'land']) {
      await page.goto(`${BASE}/welcome?step=${step}`);
      await page.waitForTimeout(700);
      const o = await overflow(page);
      check(`[${tag}] welcome ${step}: no horizontal overflow`, !o.scroll, JSON.stringify(o));
      await page.screenshot({ path: `${SHOTS}/welcome-${step}-${tag}.png`, fullPage: true });
    }
    // live arrivals while on step 3
    await page.goto(`${BASE}/welcome?step=work`);
    const wctx = await apiLogin(pw, wu, PW);
    const longName = 'WANDAS-EXTREMELY-LONG-MACBOOK-PRO-WITH-A-NAME-THAT-NEVER-ENDS-1234567890';
    const s = await startLink(longName);
    await api(wctx, 'POST', `/api/sync/link/${s.code}/allow`);
    const tok = (await poll(s.poll)).token;
    await putFile(tok, 'docs/very/deep/folder/structure/that/keeps/going/and/going/final summary of the quarter v3 (approved).md');
    await putFile(tok, 'notes.txt', 'inbox');
    let live = false;
    for (let i = 0; i < 12 && !live; i++) { await page.waitForTimeout(500); live = (await page.locator('.feed li').count()) >= 2; }
    check(`[${tag}] welcome step 3 shows arriving files live`, live);
    check(`[${tag}] welcome step 3 shows the linked computer`, (await page.locator('.ar-hd b').textContent().catch(() => '')).includes('WANDAS'));
    const o3 = await overflow(page);
    check(`[${tag}] welcome step 3 with long names: no overflow`, !o3.scroll, JSON.stringify(o3));
    await page.screenshot({ path: `${SHOTS}/welcome-work-live-${tag}.png`, fullPage: true });
    await page.goto(`${BASE}/welcome?step=land`);
    await page.waitForTimeout(800);
    check(`[${tag}] where-things-land shows the real project folder`, (await page.locator('.tree').textContent()).includes('quarterly-report'));
    await page.screenshot({ path: `${SHOTS}/welcome-land-live-${tag}.png`, fullPage: true });
    // keyboard: the steps rail and buttons are reachable
    await page.keyboard.press('Tab');
    const f1 = await page.evaluate(() => document.activeElement?.textContent?.trim().slice(0, 30));
    check(`[${tag}] welcome: first Tab reaches a control`, !!f1, f1);
    // Skip marks it seen
    await page.getByRole('button', { name: /Skip for now|Close/ }).click();
    await page.waitForURL(/\/files/, { timeout: 5000 }).catch(() => {});
    const me = await (await wctx.get(BASE + '/api/auth/me')).json();
    check(`[${tag}] skip/close marks the welcome seen`, me.welcome === false && page.url().includes('/files'), page.url());
    // sign in again: no welcome
    const p2 = await (await browser.newContext({ viewport: { width: vp.w, height: vp.h } })).newPage();
    await p2.goto(BASE + '/login'); await uiLogin(p2, wu);
    await p2.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 8000 }).catch(() => {});
    check(`[${tag}] second sign-in skips the welcome`, !p2.url().includes('/welcome'), p2.url());
    // Getting started in the account menu
    if (vp.w <= 1000) { const burger = p2.locator('button[aria-label*="enu" i]').first(); if (await burger.count()) await burger.click().catch(() => {}); }
    await p2.getByRole('button', { name: 'Account' }).click().catch(() => {});
    const gs = p2.getByRole('link', { name: 'Getting started' });
    const hasGs = await gs.count();
    check(`[${tag}] account menu has Getting started`, hasGs > 0);
    if (hasGs) { await gs.click(); await p2.waitForURL(/\/welcome/, { timeout: 4000 }).catch(() => {}); check(`[${tag}] Getting started opens /welcome`, p2.url().includes('/welcome'), p2.url()); }
    await p2.screenshot({ path: `${SHOTS}/getting-started-${tag}.png` });
    await p2.context().close();

    // ---- welcome must not hijack /link and /oauth next targets --------------------------------
    const nu = `${users.nico}${tag}`;
    await api(admin, 'POST', '/api/users', { username: nu, password: PW, system_role: 'member', display_name: 'Nico' });
    const s2 = await startLink('NICO-PC');
    const c2 = await browser.newContext({ viewport: { width: vp.w, height: vp.h } });
    const p3 = await c2.newPage();
    await p3.goto(`${BASE}/link?code=${s2.code}`);
    await p3.waitForURL(/\/login/, { timeout: 5000 }).catch(() => {});
    check(`[${tag}] /link signed out goes to sign-in with next`, p3.url().includes('/login?next='), p3.url());
    await uiLogin(p3, nu);
    await p3.waitForURL(/\/link/, { timeout: 5000 }).catch(() => {});
    check(`[${tag}] first sign-in from a link page goes to /link, not /welcome`, p3.url().includes('/link?code=' + s2.code), p3.url());
    await c2.close();
    const ou = `${users.otto}${tag}`;
    await api(admin, 'POST', '/api/users', { username: ou, password: PW, system_role: 'member', display_name: 'Otto' });
    const c3 = await browser.newContext({ viewport: { width: vp.w, height: vp.h } });
    const p4 = await c3.newPage();
    const reg = await (await anon.post(BASE + '/oauth/register', { data: { client_name: 'Claude Code', redirect_uris: ['http://127.0.0.1:33418/callback'] } })).json();
    const authz = `/oauth/authorize?client_id=${reg.client_id}&redirect_uri=${encodeURIComponent('http://127.0.0.1:33418/callback')}&response_type=code&state=s&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256`;
    await p4.goto(`${BASE}/login?next=${encodeURIComponent(authz)}`);
    await uiLogin(p4, ou);
    await p4.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 8000 }).catch(() => {});
    await p4.waitForTimeout(800);
    check(`[${tag}] first sign-in for Claude Code goes to consent, not /welcome`, /\/(consent|oauth)/.test(new URL(p4.url()).pathname), p4.url());
    await p4.screenshot({ path: `${SHOTS}/oauth-first-signin-${tag}.png` });
    await c3.close();

    // ---- M3: Link page states ----------------------------------------------------------------
    const lu = `${users.lena}${tag}`;
    await api(admin, 'POST', '/api/users', { username: lu, password: PW, system_role: 'member', display_name: 'Lena' });
    const c4 = await browser.newContext({ viewport: { width: vp.w, height: vp.h }, deviceScaleFactor: 1 });
    const p5 = await c4.newPage();
    p5.on('dialog', (d) => { errs.push('dialog: ' + d.message()); d.dismiss(); });
    await p5.goto(BASE + '/login'); await uiLogin(p5, lu);
    await p5.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 8000 });
    await p5.evaluate(async () => { const me = await (await fetch('/api/auth/me')).json(); await fetch('/api/sync/welcome/done', { method: 'POST', headers: { 'x-csrf-token': me.csrf } }); });
    const hostile = '<img src=x onerror=alert(1)>ＭａｃＢｏｏｋ‐ＰｒｏＭａｃＢｏｏｋ‐ＰｒｏＭａｃＢｏｏｋ‐Ｐｒｏ‐ＸＸＸＸＸＸＸＸＸＸ';
    const s3 = await startLink(hostile);
    await p5.goto(`${BASE}/link?code=${s3.code.toLowerCase()}`);
    await p5.getByRole('button', { name: 'Allow', exact: true }).waitFor({ timeout: 5000 });
    const shown = (await p5.locator('.code .ch').allTextContents()).join('');
    check(`[${tag}] link page shows the same code`, shown === s3.code.replace('-', ''), shown);
    check(`[${tag}] hostile computer name is shown as text`, (await p5.locator('h1').textContent()).includes('<img'));
    const o5 = await overflow(p5);
    check(`[${tag}] link page with a long name: no overflow`, !o5.scroll, JSON.stringify(o5));
    await p5.screenshot({ path: `${SHOTS}/link-ask-${tag}.png`, fullPage: true });
    // keyboard: Tab order reaches Don't allow then Allow, with a visible focus indicator
    await p5.keyboard.press('Tab'); await p5.keyboard.press('Tab');
    let order = [];
    for (let i = 0; i < 8; i++) { order.push(await p5.evaluate(() => document.activeElement?.textContent?.trim().slice(0, 20) || document.activeElement?.tagName)); await p5.keyboard.press('Tab'); }
    check(`[${tag}] link page: Allow reachable by keyboard`, order.includes('Allow'), order.join(' | '));
    await p5.getByRole('button', { name: 'Allow', exact: true }).focus();
    const ring = await p5.evaluate(() => { const s = getComputedStyle(document.activeElement); return { o: s.outlineStyle + ' ' + s.outlineWidth, sh: s.boxShadow }; });
    check(`[${tag}] link page: focused Allow has a visible focus style`, ring.o !== 'none 0px' || (ring.sh && ring.sh !== 'none'), JSON.stringify(ring));
    await p5.screenshot({ path: `${SHOTS}/link-focus-${tag}.png` });
    const cr = await contrast(p5, '.txt p.muted');
    check(`[${tag}] link page body text contrast >= 4.5`, cr >= 4.5, String(cr));
    const crn = await contrast(p5, '.note');
    check(`[${tag}] link page note contrast >= 4.5`, crn >= 4.5, String(crn));
    await p5.getByRole('button', { name: 'Allow', exact: true }).click();
    await p5.getByText('is linked').waitFor({ timeout: 5000 }).catch(() => {});
    check(`[${tag}] Allow shows linked state`, await p5.getByText('is linked').count() > 0);
    await p5.screenshot({ path: `${SHOTS}/link-approved-${tag}.png`, fullPage: true });
    const pl = await poll(s3.poll);
    check(`[${tag}] computer receives the token once`, pl.status === 'approved' && (await poll(s3.poll)).status === 'done');
    // used code
    await p5.goto(`${BASE}/link?code=${s3.code}`);
    await p5.getByText('This code was already used').waitFor({ timeout: 5000 }).catch(() => {});
    check(`[${tag}] used code shows "already used"`, await p5.getByText('This code was already used').count() > 0);
    await p5.screenshot({ path: `${SHOTS}/link-used-${tag}.png` });
    // expired/unknown code, missing code
    await p5.goto(`${BASE}/link?code=ZZZZ-ZZZZ`);
    await p5.getByText('This code has expired').waitFor({ timeout: 5000 }).catch(() => {});
    check(`[${tag}] unknown code shows expired`, await p5.getByText('This code has expired').count() > 0);
    await p5.screenshot({ path: `${SHOTS}/link-expired-${tag}.png` });
    await p5.goto(`${BASE}/link`);
    await p5.waitForTimeout(500);
    check(`[${tag}] missing code message`, await p5.getByText('no code in this link').count() > 0);
    await p5.screenshot({ path: `${SHOTS}/link-missing-${tag}.png` });
    // deny
    const s4 = await startLink('DENY-ME');
    await p5.goto(`${BASE}/link?code=${s4.code}`);
    await p5.getByRole('button', { name: 'Don’t allow' }).click();
    await p5.getByText('Not linked').waitFor({ timeout: 5000 }).catch(() => {});
    check(`[${tag}] Don't allow shows Not linked; computer told denied`, (await p5.getByText('Not linked').count()) > 0 && (await poll(s4.poll)).status === 'denied');
    await p5.screenshot({ path: `${SHOTS}/link-denied-${tag}.png` });
    // Connect page: computers that sync, Turn off
    const s5 = await startLink('ANOTHER-VERY-LONG-COMPUTER-NAME-WITHOUT-ANY-SPACES-AT-ALL-0123456789');
    await api(await apiLogin(pw, lu, PW), 'POST', `/api/sync/link/${s5.code}/allow`);
    const t5 = (await poll(s5.poll)).token;
    await putFile(t5, 'a.md');
    await p5.goto(`${BASE}/connect#sync`);
    await p5.locator('#sync .dev').first().waitFor({ timeout: 5000 });
    const devs = await p5.locator('#sync .dev').count();
    check(`[${tag}] Connect lists computers that sync`, devs === 2, String(devs));
    const o6 = await overflow(p5);
    check(`[${tag}] Connect with long computer names: no overflow`, !o6.scroll, JSON.stringify(o6));
    await p5.locator('#sync').screenshot({ path: `${SHOTS}/connect-sync-${tag}.png` });
    await p5.locator('#sync .dev', { hasText: 'ANOTHER-VERY' }).getByRole('button', { name: 'Turn off' }).click();
    await p5.getByRole('dialog').getByRole('button', { name: 'Turn off' }).click();  // confirm in the app's dialog
    await p5.waitForTimeout(800);
    check(`[${tag}] Turn off removes it and the token stops`, (await p5.locator('#sync .dev').count()) === 1 && (await putFile(t5, 'b.md')) === 401);
    check(`[${tag}] no page errors or dialogs (XSS)`, errs.length === 0, errs.join(' | '));
    await c4.close();
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(__dirname, 'out_journey_sync.json'), JSON.stringify({ results, issues }, null, 2));
  const bad = results.filter((r) => !r.ok);
  console.log(`\n${results.length - bad.length}/${results.length} checks passed`);
  process.exit(bad.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
