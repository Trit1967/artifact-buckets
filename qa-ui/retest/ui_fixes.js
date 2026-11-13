// Re-run the UI-n repros against the private instance. One step per finding; each step is isolated.
// Run: cd qa-ui/retest && env -u HTTPS_PROXY -u HTTP_PROXY BASE=http://localhost:8096 node ui_fixes.js
const { chromium } = require('playwright');
const crypto = require('crypto');
const fs = require('fs');
const BASE = process.env.BASE || 'http://localhost:8096';
const pw = (u) => (u === 'admin' ? 'change-me-now-please' : 'demo-password-123');
const OUT = {};
const log = (k, v) => { OUT[k] = v; console.log(k, '=>', typeof v === 'string' ? v : JSON.stringify(v)); };
const shot = (p, n) => p.screenshot({ path: `shots/${n}.png` });

async function login(page, user) {
  await page.goto(BASE + '/login');
  await page.getByLabel('Username').fill(user);
  await page.getByLabel('Password').fill(pw(user));
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 10000 });
}
async function apiIn(page, method, url, body) {
  return page.evaluate(async ([method, url, body]) => {
    const me = await (await fetch('/api/auth/me')).json();
    const h = { 'Content-Type': 'application/json' };
    if (me.csrf) h['X-CSRF-Token'] = me.csrf;
    const r = await fetch(url, { method, headers: h, body: body ? JSON.stringify(body) : undefined });
    let t = await r.text(); try { t = JSON.parse(t); } catch {}
    return { status: r.status, body: t };
  }, [method, url, body]);
}
const b64 = (b) => b.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
async function register(redirect, name = 'Claude Code') {
  const r = await fetch(BASE + '/oauth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_name: name, redirect_uris: [redirect] }) });
  return (await r.json()).client_id;
}
function authUrl(cid, redirect, state) {
  const verifier = b64(crypto.randomBytes(32));
  const challenge = b64(crypto.createHash('sha256').update(verifier).digest());
  const q = new URLSearchParams({ client_id: cid, redirect_uri: redirect, response_type: 'code', state, code_challenge: challenge, code_challenge_method: 'S256' });
  return { url: BASE + '/oauth/authorize?' + q, verifier };
}
async function step(name, fn) {
  try { await fn(); } catch (e) { log(name + ' ERROR', String(e).slice(0, 300)); }
}

(async () => {
  const b = await chromium.launch();
  const errors = [];
  const mk = async (vp = { width: 1440, height: 900 }, extra = {}) => {
    const ctx = await b.newContext({ viewport: vp, deviceScaleFactor: 1, ...extra });
    const page = await ctx.newPage();
    page.on('pageerror', (e) => errors.push(`[pageerror] ${page.url()} ${e.message}`));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(`[console] ${page.url()} ${m.text()}`); });
    return { ctx, page };
  };

  // ---------- UI-2 Enter on consent, UI-25 bare consent, UI-1 foreign host display ----------
  await step('UI-2', async () => {
    const { ctx, page } = await mk();
    const hits = [];
    await ctx.route(/^http:\/\/127\.0\.0\.1:33418\/.*/, (r) => { hits.push(r.request().url()); r.fulfill({ status: 200, body: 'cb ok' }); });
    await login(page, 'jonas');
    const redirect = 'http://127.0.0.1:33418/callback';
    const cid = await register(redirect);
    const a = authUrl(cid, redirect, 'st-C');
    await page.goto(a.url); await page.waitForURL(/\/consent/);
    log('UI-2 consent text', (await page.locator('form').innerText()).replace(/\n+/g, ' | ').slice(0, 300));
    await shot(page, 'consent_loopback');
    const f = page.getByLabel('Name this computer');
    await f.fill('Edited name'); await f.press('Enter');
    await page.waitForTimeout(1200);
    const u = hits.length ? new URL(hits[hits.length - 1]) : null;
    log('UI-2 Enter result', u ? { code: !!u.searchParams.get('code'), error: u.searchParams.get('error'), state: u.searchParams.get('state') } : 'no callback hit');
    // visual order: Cancel left of Connect?
    // UI-25
    await page.goto(BASE + '/consent'); await page.waitForTimeout(600);
    log('UI-25 bare /consent', (await page.locator('main, body').first().innerText()).slice(0, 200).replace(/\n+/g, ' | '));
    // UI-1 UI side: a consent URL pointing at a foreign https host (registration is refused, but the page must still warn)
    const q = new URLSearchParams({ client_id: cid, redirect_uri: 'https://attacker.example/cb', response_type: 'code', state: 's', code_challenge: 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM', code_challenge_method: 'S256', client_name: 'Claude Code' });
    await page.goto(BASE + '/consent?' + q); await page.waitForTimeout(600);
    log('UI-1 consent with foreign redirect', (await page.locator('form').innerText()).replace(/\n+/g, ' | ').slice(0, 300));
    await shot(page, 'consent_foreign');
    await page.getByRole('button', { name: 'Connect' }).click(); await page.waitForTimeout(800);
    log('UI-1 approve with unregistered foreign redirect ->', { url: page.url(), body: (await page.locator('body').innerText()).slice(0, 150) });
    await ctx.close();
  });

  // ---------- UI-24 next=/\evil ----------
  await step('UI-24', async () => {
    const { ctx, page } = await mk();
    await page.goto(BASE + '/login?next=' + encodeURIComponent('/\\evil.example'));
    await page.getByLabel('Username').fill('maya'); await page.getByLabel('Password').fill(pw('maya'));
    await page.getByRole('button', { name: 'Sign in' }).click(); await page.waitForTimeout(1500);
    log('UI-24 after login with next=/\\evil.example', page.url());
    await page.goto(BASE + '/logout').catch(() => {});
    await ctx.close();
  });

  // ---------- member (maya) pages ----------
  const M = await mk();
  await login(M.page, 'maya');
  const page = M.page;

  await step('UI-4', async () => {
    const r = await apiIn(page, 'POST', '/api/fs/save-text', { path: '/users/maya/inbox/link-target.md', content: '# Link target\n\nHello.' });
    await page.goto(BASE + '/view?path=' + encodeURIComponent('/users/maya/inbox/link-target.md')); await page.waitForTimeout(1200);
    log('UI-4 /view of saved file', { save: r.status, h1: await page.locator('h1').first().innerText(), body: (await page.locator('article.md').innerText().catch(() => 'no article')).slice(0, 60) });
    // the exact link format MCP returns (unencoded slashes)
    await page.goto(BASE + '/view?path=/users/maya/inbox/link-target.md'); await page.waitForTimeout(1000);
    log('UI-4 /view raw link format', await page.locator('h1').first().innerText());
    // old-style broken link /files?path=<file>
    await page.goto(BASE + '/files?path=' + encodeURIComponent('/users/maya/inbox/link-target.md')); await page.waitForTimeout(1200);
    log('UI-4 old /files?path=<file> link', { url: page.url(), text: (await page.locator('main').innerText()).slice(0, 120).replace(/\n+/g, ' | ') });
  });

  await step('UI-9', async () => {
    await apiIn(page, 'POST', '/api/fs/save-text', { path: '/users/maya/inbox/dropped.txt', content: 'version one\n' });
    await page.goto(BASE + '/files?path=%2Fusers%2Fmaya%2Finbox&file=%2Fusers%2Fmaya%2Finbox%2Fdropped.txt'); await page.waitForTimeout(1200);
    const before = await page.locator('aside[aria-label="File details"] .note').first().innerText();
    fs.mkdirSync('/tmp/qa-retest/up', { recursive: true });
    fs.writeFileSync('/tmp/qa-retest/up/dropped.txt', 'new content for the next version\n');
    await page.getByRole('button', { name: 'Upload' }).click();
    await page.locator('input[type=file]').setInputFiles('/tmp/qa-retest/up/dropped.txt'); await page.waitForTimeout(2000);
    const after = await page.locator('aside[aria-label="File details"] .note').first().innerText();
    const pv = (await page.locator('aside .pv').innerText()).trim();
    log('UI-9 detail before/after upload', { before, after, preview: pv.slice(0, 50) });
    // restore v1 via Versions dialog
    await page.getByRole('button', { name: 'Versions' }).click(); await page.waitForTimeout(800);
    const restore = page.locator('[role=dialog]').getByRole('button', { name: /Restore/ }).first();
    await restore.click(); await page.waitForTimeout(1500);
    await page.keyboard.press('Escape'); await page.waitForTimeout(400);
    log('UI-9 after restore', { note: await page.locator('aside[aria-label="File details"] .note').first().innerText(), preview: (await page.locator('aside .pv').innerText()).trim().slice(0, 50) });
  });

  await step('UI-10', async () => {
    await page.goto(BASE + '/files?path=%2Fusers%2Fmaya%2Finbox'); await page.waitForTimeout(1000);
    await page.locator('body').click({ position: { x: 700, y: 5 } });
    const res = {};
    for (const [k, sel] of Object.entries({ rowbtn: 'button.rowbtn', mebtn: 'button.mebtn', btn: 'button.btn', leaf: 'a.leaf' })) {
      for (let i = 0; i < 60; i++) {
        await page.keyboard.press('Tab');
        const ok = await page.evaluate((s) => document.activeElement?.matches(s), sel);
        if (ok) { res[k] = await page.evaluate(() => { const cs = getComputedStyle(document.activeElement); return `${cs.outlineStyle} ${cs.outlineWidth} ${cs.boxShadow.slice(0, 40)}`; }); break; }
      }
      await page.locator('body').click({ position: { x: 700, y: 5 } });
    }
    log('UI-10 focus styles via Tab', res);
    await page.locator('body').click({ position: { x: 700, y: 5 } });
    for (let i = 0; i < 60; i++) { await page.keyboard.press('Tab'); if (await page.evaluate(() => document.activeElement?.classList.contains('rowbtn'))) break; }
    await shot(page, 'focus_rowbtn');
    await page.goto(BASE + '/incoming'); await page.waitForTimeout(800);
    await page.locator('body').click({ position: { x: 700, y: 5 } });
    let seg = 'no seg reached';
    for (let i = 0; i < 40; i++) { await page.keyboard.press('Tab'); if (await page.evaluate(() => document.activeElement?.classList.contains('seg'))) { seg = await page.evaluate(() => { const cs = getComputedStyle(document.activeElement); return cs.outlineStyle + ' ' + cs.outlineWidth; }); break; } }
    log('UI-10 .seg focus', seg);
  });

  await step('UI-11/12/43 mobile', async () => {
    const { ctx, page: p } = await mk({ width: 390, height: 844 });
    await login(p, 'maya');
    await p.goto(BASE + '/files?path=%2Fusers%2Fmaya%2Finbox'); await p.waitForTimeout(1200);
    const hdr = await p.locator('aside.side').boundingBox();
    log('UI-43 mobile header height', Math.round(hdr.height));
    await shot(p, 'm390_files');
    await p.locator('button.rowbtn', { hasText: 'link-target.md' }).click(); await p.waitForTimeout(1200);
    log('UI-12 tap file at 390', { url: p.url(), h1: await p.locator('h1').first().innerText().catch(() => '') });
    await shot(p, 'm390_viewer');
    await p.getByRole('button', { name: 'Menu' }).click(); await p.waitForTimeout(400);
    const me = p.locator('button.mebtn');
    log('UI-11 account button visible in drawer (member)', await me.isVisible());
    await me.click(); await p.waitForTimeout(300);
    const so = p.getByRole('button', { name: 'Sign out' });
    log('UI-11 sign out visible/in viewport', { visible: await so.isVisible(), box: await so.boundingBox() });
    await shot(p, 'm390_drawer');
    await ctx.close();
    const A = await mk({ width: 390, height: 700 });
    await login(A.page, 'admin');
    await A.page.goto(BASE + '/overview'); await A.page.waitForTimeout(1200);
    log('UI-32 admin overview 390 horizontal overflow', await A.page.evaluate(() => document.documentElement.scrollWidth - innerWidth));
    await shot(A.page, 'a390_overview');
    await A.page.getByRole('button', { name: 'Menu' }).click(); await A.page.waitForTimeout(400);
    const me2 = A.page.locator('button.mebtn');
    const bb = await me2.boundingBox();
    await A.page.mouse.wheel(0, 2000); await A.page.waitForTimeout(300);
    const bb2 = await me2.boundingBox();
    log('UI-11 admin drawer at 390x700: account button box before/after scroll', { bb, bb2, vh: 700 });
    await shot(A.page, 'a390_drawer');
    await me2.click({ timeout: 3000 }).then(() => log('UI-11 admin account click', 'ok')).catch((e) => log('UI-11 admin account click', 'FAILED ' + String(e).slice(0, 120)));
    await A.ctx.close();
    const T = await mk({ width: 900, height: 900 });
    await login(T.page, 'maya');
    await T.page.getByRole('button', { name: 'Menu' }).click(); await T.page.waitForTimeout(300);
    log('UI-11 900px account visible in drawer', await T.page.locator('button.mebtn').isVisible());
    await T.ctx.close();
  });

  await step('UI-22 trash text', async () => {
    await page.goto(BASE + '/trash'); await page.waitForTimeout(800);
    log('UI-22 trash subtitle', (await page.locator('main').innerText()).slice(0, 160).replace(/\n+/g, ' | '));
  });

  await step('UI-26 drag', async () => {
    await page.goto(BASE + '/files?path=%2Fusers%2Fmaya%2Finbox'); await page.waitForTimeout(800);
    await page.getByRole('button', { name: 'Upload' }).click();
    await page.evaluate(() => { const dt = new DataTransfer(); dt.items.add(new File(['x'], 'x.txt')); window.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true })); });
    const during = await page.locator('section.listing').getAttribute('class');
    await page.evaluate(() => { const dt = new DataTransfer(); window.dispatchEvent(new DragEvent('dragleave', { dataTransfer: dt, bubbles: true, relatedTarget: null })); });
    await page.waitForTimeout(300);
    log('UI-26 listing class during/after drag leaves window', { during, after: await page.locator('section.listing').getAttribute('class') });
  });

  await step('UI-30 quota card', async () => {
    await page.goto(BASE + '/files'); await page.waitForTimeout(800);
    log('UI-30 member sidebar card', (await page.locator('.meter-card').innerText().catch(() => 'none')).replace(/\n+/g, ' | '));
  });

  await step('UI-36/38 detail overflow & titles', async () => {
    await apiIn(page, 'POST', '/api/fs/mkdir', { path: '/users/maya/inbox/fold-er-' + 'deep-'.repeat(30) });
    // tags + project via MCP-like save with token is backend-only; use save-text then check meta layout of an MCP-saved file from seed
    const r = await apiIn(page, 'GET', '/api/fs/search?q=&folder=/users/maya');
    const withTags = (r.body.results || []).find((f) => f.tags && f.tags.length) || (r.body.results || [])[0];
    await page.goto(BASE + '/files?path=' + encodeURIComponent(withTags.path.split('/').slice(0, -1).join('/')) + '&file=' + encodeURIComponent(withTags.path)); await page.waitForTimeout(1200);
    const over = await page.evaluate(() => { const a = document.querySelector('aside[aria-label="File details"]'); if (!a) return 'no panel'; const ar = a.getBoundingClientRect(); return [...a.querySelectorAll('*')].filter((e) => e.getBoundingClientRect().right > ar.right + 1).map((e) => e.tagName + '.' + e.className).slice(0, 5); });
    log('UI-36 elements overflowing the detail panel', { file: withTags.path, tags: withTags.tags, over });
    await page.goto(BASE + '/files?path=%2Fusers%2Fmaya%2Finbox'); await page.waitForTimeout(900);
    log('UI-38 long folder row title attr', await page.locator('button.rowbtn', { hasText: 'fold-er-deep' }).getAttribute('title'));
  });

  await step('UI-41 markdown', async () => {
    await page.goto(BASE + '/files?path=%2Fusers%2Fmaya%2Finbox&file=' + encodeURIComponent('/users/maya/inbox/link-target.md')); await page.waitForTimeout(1200);
    log('UI-41 detail panel markdown rendered', await page.locator('aside .mdmini h1').count());
  });

  await step('UI-16 owner add rule', async () => {
    const A = await mk(); await login(A.page, 'admin');
    const g = await apiIn(A.page, 'POST', '/api/fs/share', { path: '/shared/platform-eng', kind: 'user', name: 'maya', role: 'owner' });
    log('UI-16 grant maya owner on /shared/platform-eng', g.status + ' ' + JSON.stringify(g.body).slice(0, 100));
    await A.ctx.close();
    await page.goto(BASE + '/incoming'); await page.waitForTimeout(1000);
    const btn = page.getByRole('button', { name: 'Add rule' });
    log('UI-16 Add rule visible for team Owner', await btn.isVisible());
    if (await btn.isVisible()) {
      await btn.click(); await page.waitForTimeout(400);
      const dlg = page.locator('[role=dialog]');
      const inputs = dlg.locator('input');
      log('UI-16 rule dialog fields', await dlg.innerText().then((t) => t.replace(/\n+/g, ' | ').slice(0, 200)));
      await inputs.nth(0).fill('github.com/acme/owner-rule*');
      await inputs.nth(1).fill('/shared/platform-eng/projects/{project}');
      await dlg.getByRole('button', { name: 'Add rule' }).click(); await page.waitForTimeout(900);
      log('UI-16 owner add rule result', (await page.locator('.toast').innerText().catch(() => '')) + ' | rules: ' + (await page.locator('main').innerText()).includes('owner-rule'));
      // an owner trying a target they do not own
      await btn.click(); await page.waitForTimeout(300);
      await inputs.nth(0).fill('github.com/acme/foreign*');
      await inputs.nth(1).fill('/shared/finance/x');
      await dlg.getByRole('button', { name: 'Add rule' }).click(); await page.waitForTimeout(900);
      log('UI-16 owner add rule to foreign team', await page.locator('.toast').last().innerText().catch(() => 'no toast'));
      await page.keyboard.press('Escape');
    }
  });

  await step('UI-21 connect text', async () => {
    await page.goto(BASE + '/connect'); await page.waitForTimeout(800);
    const t = await page.locator('main').innerText();
    log('UI-21/UI-3 connect page', { marketplace: (t.match(/\/plugin marketplace add [^\n]+/) || [''])[0], authenticate: /Authenticate/.test(t), browserOpensOnFirstUse: /Your browser opens/i.test(t) && !/mcp/.test(t) });
  });

  await M.ctx.close();

  // ---------- admin / auditor ----------
  const A = await mk({ width: 1440, height: 900 }, { acceptDownloads: true });
  await login(A.page, 'admin');
  const ap = A.page;

  await step('UI-13', async () => {
    await apiIn(ap, 'POST', '/api/tokens', { name: 'expiring', scope: 'read', days: 2 });
    await ap.goto(BASE + '/overview'); await ap.waitForTimeout(1200);
    const link = ap.locator('a', { hasText: /expire this week/ });
    log('UI-13 attention item present', await link.count());
    if (await link.count()) { await link.first().click(); await ap.waitForTimeout(800); log('UI-13 attention link ->', { url: ap.url(), h1: await ap.locator('h1').first().innerText() }); }
  });

  await step('UI-14', async () => {
    await ap.goto(BASE + '/connect'); await ap.waitForTimeout(900);
    const t = await ap.locator('main').innerText();
    const ctl = ap.getByRole('button', { name: /Everyone|everyone|All people/ }).or(ap.getByLabel(/everyone/i));
    log('UI-14 admin everyone control count', await ctl.count());
    if (await ctl.count()) { await ctl.first().click(); await ap.waitForTimeout(900); }
    const t2 = await ap.locator('main').innerText();
    log('UI-14 other people tokens visible', { jonas: /jonas|Jonas/.test(t2), maya: /maya|Maya/.test(t2), snippet: t2.slice(t2.indexOf('Device') > 0 ? t2.indexOf('Device') : 0).slice(0, 200).replace(/\n+/g, ' | ') });
    await ap.screenshot({ path: 'shots/admin_connect_everyone.png', fullPage: true });
  });

  await step('UI-15', async () => {
    await ap.goto(BASE + '/groups'); await ap.waitForTimeout(900);
    await ap.getByLabel('Folder path').fill('/shared/platform-eng/projects');
    await ap.getByLabel('Person').fill('maya');
    await ap.getByRole('button', { name: 'Check' }).click(); await ap.waitForTimeout(900);
    const box = await ap.locator('main').innerText();
    const i = box.indexOf('maya');
    log('UI-15 access check with person', box.slice(Math.max(0, box.indexOf('Who can reach')), Math.max(0, box.indexOf('Who can reach')) + 500).replace(/\n+/g, ' | '));
    await ap.getByLabel('Person').fill('diego');
    await ap.getByRole('button', { name: 'Check' }).click(); await ap.waitForTimeout(900);
    const box2 = await ap.locator('main').innerText();
    log('UI-15 diego (not a member)', box2.slice(Math.max(0, box2.indexOf('Who can reach')), Math.max(0, box2.indexOf('Who can reach')) + 300).replace(/\n+/g, ' | '));
    await ap.screenshot({ path: 'shots/admin_access_check.png', fullPage: true });
  });

  await step('UI-18 SMB', async () => {
    await ap.goto(BASE + '/smb'); await ap.waitForTimeout(800);
    const t = await ap.locator('main').innerText();
    log('UI-18 SMB audit claim', (t.match(/[^\n]*(record|logged|audit)[^\n]*/gi) || []).slice(0, 4));
  });

  await step('UI-31/34 audit', async () => {
    await ap.goto(BASE + '/users'); await ap.waitForTimeout(800);
    const r = await apiIn(ap, 'GET', '/api/users');
    const priya = r.body.users.find((u) => u.username === 'priya');
    await apiIn(ap, 'PATCH', `/api/users/${priya.id}`, { status: 'suspended' });
    await apiIn(ap, 'PATCH', `/api/users/${priya.id}`, { status: 'active' });
    await apiIn(ap, 'PATCH', `/api/users/${priya.id}`, { quota_bytes: 107374182 });
    await ap.goto(BASE + '/audit'); await ap.waitForTimeout(1000);
    await ap.locator('select').first().selectOption('user.*'); await ap.getByRole('button', { name: 'Search' }).click(); await ap.waitForTimeout(900);
    const rows = await ap.locator('tbody tr.click').allInnerTexts();
    log('UI-34 audit sentences user.*', rows.slice(0, 4).map((x) => x.replace(/\s+/g, ' ').slice(0, 120)));
    log('UI-31 through options', await ap.locator('select').nth(1).locator('option').allInnerTexts());
    await ap.locator('select').first().selectOption('');
    await ap.getByRole('button', { name: 'Search' }).click(); await ap.waitForTimeout(900);
    const shown = await ap.locator('tbody tr.click').count();
    const [dl] = await Promise.all([ap.waitForEvent('download'), ap.getByRole('link', { name: 'Export CSV' }).click()]);
    const csv = fs.readFileSync(await dl.path(), 'utf8');
    log('UI-31 rows shown vs exported', { shown, exported: csv.trim().split('\n').length - 1 });
    log('UI-10 audit row tabIndex', await ap.evaluate(() => document.querySelector('tr.click')?.tabIndex));
    // keyboard: can a row be expanded?
    await ap.locator('body').click({ position: { x: 700, y: 5 } });
    let reached = false;
    for (let i = 0; i < 80; i++) { await ap.keyboard.press('Tab'); if (await ap.evaluate(() => !!document.activeElement?.closest('tbody'))) { reached = true; break; } }
    log('UI-10 keyboard reaches audit table rows', reached);
  });

  await step('UI-33/44/37 storage', async () => {
    await ap.goto(BASE + '/storage'); await ap.waitForTimeout(1000);
    const t = await ap.locator('main').innerText();
    log('UI-44 volume cards label', (t.match(/[^\n]*(host disk|GB used)[^\n]*/gi) || []).slice(0, 4));
    await ap.screenshot({ path: 'shots/admin_storage.png', fullPage: true });
    const card = ap.locator('.meter-card');
    const ch = await card.boundingBox().catch(() => null);
    log('UI-37 sidebar pool card', { text: (await card.innerText().catch(() => 'none')).replace(/\n/g, ' | '), height: ch && Math.round(ch.height) });
  });

  await step('UI-39 glitches', async () => {
    await ap.goto(BASE + '/groups'); await ap.waitForTimeout(900);
    const t = await ap.locator('main').innerText();
    log('UI-39 "· group of"/"# " glitches', { groupOf: (t.match(/\S+· group of \d+/g) || []).slice(0, 2), ruleSpace: /rule # \d/.test(t) });
    await ap.goto(BASE + '/incoming'); await ap.waitForTimeout(900);
    const r = await apiIn(ap, 'POST', '/api/routing/preview', { git_remote: 'git@github.com:acme/platform-api.git', project: 'platform-api' });
    log('UI-39 routing preview reason', JSON.stringify(r.body).slice(0, 200));
    await ap.goto(BASE + '/trash'); await ap.waitForTimeout(800);
    log('UI-39 trash deleted-by', (await ap.locator('main').innerText()).slice(0, 300).replace(/\n+/g, ' | '));
  });

  await A.ctx.close();

  await step('UI-35 auditor', async () => {
    const L = await mk();
    const bad = [];
    L.page.on('response', (r) => { if (r.status() >= 400) bad.push(r.status() + ' ' + r.url()); });
    await login(L.page, 'lena');
    await L.page.goto(BASE + '/files'); await L.page.waitForTimeout(1200);
    log('UI-35 auditor /files failing requests', bad);
    log('UI-35 auditor files page', (await L.page.locator('main').innerText()).slice(0, 150).replace(/\n+/g, ' | '));
    await L.ctx.close();
  });

  log('console/page errors', errors);
  fs.writeFileSync('out_ui_fixes.json', JSON.stringify(OUT, null, 1));
  await b.close();
})();
