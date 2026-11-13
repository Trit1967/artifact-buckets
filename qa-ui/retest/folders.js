// Feature 2 / UI-5: folder rename, move and delete from the Files page "⋯" menu.
// Run: env -u HTTPS_PROXY -u HTTP_PROXY BASE=http://localhost:8096 node folders.js
const { chromium } = require('playwright');
const fs = require('fs');
const BASE = process.env.BASE || 'http://localhost:8096';
const OUT = {};
const log = (k, v) => { OUT[k] = v; console.log(k, '=>', typeof v === 'string' ? v : JSON.stringify(v)); };
const F = (p) => BASE + '/files?path=' + encodeURIComponent(p);
async function login(page, user, pw = 'demo-password-123') {
  await page.goto(BASE + '/login'); await page.getByLabel('Username').fill(user); await page.getByLabel('Password').fill(pw);
  await page.getByRole('button', { name: 'Sign in' }).click(); await page.waitForURL((u) => !u.pathname.startsWith('/login'));
}
async function apiIn(page, method, url, body) {
  return page.evaluate(async ([method, url, body]) => {
    const me = await (await fetch('/api/auth/me')).json();
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf || '' }, body: body ? JSON.stringify(body) : undefined });
    let t = await r.text(); try { t = JSON.parse(t); } catch {} return { status: r.status, body: t };
  }, [method, url, body]);
}
async function step(name, fn) { try { await fn(); } catch (e) { log(name + ' ERROR', String(e).slice(0, 300)); } }
const menuBtn = (p) => p.getByRole('button', { name: 'Folder actions' });

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } }); const page = await ctx.newPage();
  const errs = []; page.on('pageerror', (e) => errs.push(e.message));
  await login(page, 'maya');
  const base = '/users/maya/projects/fold-qa';
  await apiIn(page, 'POST', '/api/fs/mkdir', { path: base });
  for (let i = 0; i < 3; i++) await apiIn(page, 'POST', '/api/fs/save-text', { path: `${base}/sub/f${i}.md`, content: '# ' + i });
  for (let i = 0; i < 55; i++) await apiIn(page, 'POST', '/api/fs/save-text', { path: `${base}/big/f${i}.txt`, content: String(i) });

  await step('menu visibility', async () => {
    const r = {};
    for (const p of ['/users/maya', '/users/maya/inbox', '/users/maya/projects', base, '/shared/platform-eng', '/shared/platform-eng/projects', '/users/maya/inbox/viewer']) {
      await page.goto(F(p)); await page.waitForTimeout(900);
      r[p] = await menuBtn(page).isVisible();
    }
    log('⋯ menu shown on', r);
  });

  await step('rename', async () => {
    await page.goto(F(`${base}/sub`)); await page.waitForTimeout(900);
    await menuBtn(page).click();
    log('menu items', await page.getByRole('menuitem').allInnerTexts());
    await page.getByRole('menuitem', { name: 'Rename folder' }).click();
    const inp = page.locator('[role=dialog] input'); log('rename prefill', await inp.inputValue());
    await inp.fill('sub-renamed'); await inp.press('Enter'); await page.waitForTimeout(1200);
    log('after folder rename', { url: decodeURIComponent(page.url()), h1: await page.locator('h1.here').innerText(), files: await page.locator('button.rowbtn').count() });
  });

  await step('move', async () => {
    await menuBtn(page).click(); await page.getByRole('menuitem', { name: 'Move folder' }).click();
    const inp = page.locator('[role=dialog] input'); log('move prefill', await inp.inputValue());
    await inp.fill('/users/maya/inbox'); await inp.press('Enter'); await page.waitForTimeout(1200);
    log('after folder move', { url: decodeURIComponent(page.url()), h1: await page.locator('h1.here').innerText(), files: await page.locator('button.rowbtn').count() });
    // move into itself
    await menuBtn(page).click(); await page.getByRole('menuitem', { name: 'Move folder' }).click();
    await page.locator('[role=dialog] input').fill('/users/maya/inbox/sub-renamed/deeper'); await page.locator('[role=dialog] input').press('Enter'); await page.waitForTimeout(900);
    log('move into itself', await page.locator('.toast').last().innerText().catch(() => 'no toast'));
    await page.keyboard.press('Escape');
    // move to another person's folder (no access)
    await menuBtn(page).click(); await page.getByRole('menuitem', { name: 'Move folder' }).click();
    await page.locator('[role=dialog] input').fill('/users/jonas'); await page.locator('[role=dialog] input').press('Enter'); await page.waitForTimeout(900);
    log('move to /users/jonas', await page.locator('.toast').last().innerText().catch(() => 'no toast'));
    await page.keyboard.press('Escape');
  });

  await step('delete small', async () => {
    await page.goto(F('/users/maya/inbox/sub-renamed')); await page.waitForTimeout(900);
    await menuBtn(page).click(); await page.getByRole('menuitem', { name: 'Delete folder' }).click();
    log('delete dialog (3 files)', (await page.locator('[role=dialog]').innerText()).replace(/\n+/g, ' | '));
    await page.getByRole('button', { name: 'Move to trash' }).click(); await page.waitForTimeout(1200);
    log('after delete', { url: decodeURIComponent(page.url()), toast: await page.locator('.toast').last().innerText().catch(() => '') });
  });

  await step('delete big', async () => {
    await page.goto(F(`${base}/big`)); await page.waitForTimeout(900);
    await menuBtn(page).click(); await page.getByRole('menuitem', { name: 'Delete folder' }).click();
    log('delete dialog (55 files)', (await page.locator('[role=dialog]').innerText()).replace(/\n+/g, ' | '));
    await page.getByRole('button', { name: 'Move to trash' }).click(); await page.waitForTimeout(1500);
    log('after big delete', { url: decodeURIComponent(page.url()), toast: await page.locator('.toast').last().innerText().catch(() => '') });
    await page.goto(BASE + '/trash'); await page.waitForTimeout(900);
    log('trash shows', (await page.locator('main').innerText()).slice(0, 300).replace(/\n+/g, ' | '));
    const restore = page.getByRole('button', { name: /Restore/ }).first();
    await restore.click(); await page.waitForTimeout(1000);
    log('restore big folder', await page.locator('.toast').last().innerText().catch(() => ''));
  });

  await step('system folders', async () => {
    await page.goto(F('/users/maya/inbox')); await page.waitForTimeout(900);
    if (await menuBtn(page).isVisible()) {
      await menuBtn(page).click(); await page.getByRole('menuitem', { name: 'Rename folder' }).click();
      await page.locator('[role=dialog] input').fill('inbox-gone'); await page.locator('[role=dialog] input').press('Enter'); await page.waitForTimeout(1200);
      log('rename /users/maya/inbox via UI', { url: decodeURIComponent(page.url()), toast: await page.locator('.toast').last().innerText().catch(() => '') });
      await page.goto(F('/users/maya/inbox')); await page.waitForTimeout(900);
      log('sidebar Inbox link after renaming inbox', (await page.locator('main').innerText()).slice(0, 120).replace(/\n+/g, ' | '));
      // put it back
      const r = await apiIn(page, 'POST', '/api/fs/move', { src: '/users/maya/inbox-gone', dst: '/users/maya/inbox' });
      log('restore inbox name', r.status);
    }
  });

  await step('keyboard + escape', async () => {
    await page.goto(F(base)); await page.waitForTimeout(900);
    await menuBtn(page).focus(); await page.keyboard.press('Enter'); await page.waitForTimeout(200);
    const open1 = await page.getByRole('menu').isVisible();
    await page.keyboard.press('ArrowDown'); const fa = await page.evaluate(() => document.activeElement?.getAttribute('role'));
    await page.keyboard.press('Tab'); const fb = await page.evaluate(() => document.activeElement?.innerText?.slice(0, 20));
    await page.keyboard.press('Escape'); await page.waitForTimeout(200);
    log('menu keyboard', { openedWithEnter: open1, arrowDownFocusRole: fa, tabFocus: fb, closedOnEsc: !(await page.getByRole('menu').isVisible().catch(() => false)), focusAfterEsc: await page.evaluate(() => document.activeElement?.getAttribute('aria-label')) });
  });

  await step('editor non-owner on shared folder with own sharing', async () => {
    // admin: give jonas editor on /shared/research and create a subfolder with its own grant
    const A = await (await b.newContext()).newPage(); await login(A, 'admin', 'change-me-now-please');
    await apiIn(A, 'POST', '/api/fs/mkdir', { path: '/shared/research/locked' });
    await apiIn(A, 'POST', '/api/fs/share', { path: '/shared/research/locked', kind: 'user', name: 'priya', role: 'viewer' });
    const J = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage(); await login(J, 'jonas');
    await J.goto(F('/shared/research/locked')); await J.waitForTimeout(900);
    const vis = await menuBtn(J).isVisible();
    let toast = '';
    if (vis) {
      await menuBtn(J).click(); await J.getByRole('menuitem', { name: 'Delete folder' }).click();
      await J.getByRole('button', { name: 'Move to trash' }).click(); await J.waitForTimeout(900);
      toast = await J.locator('.toast').last().innerText().catch(() => '');
    }
    log('jonas (editor via group) on folder with its own sharing', { role: await J.locator('h1.here + *').innerText().catch(() => ''), menu: vis, deleteToast: toast });
  });

  log('page errors', errs);
  fs.writeFileSync('out_folders.json', JSON.stringify(OUT, null, 1));
  await b.close();
})();
