// UI-14: can an admin see and revoke another person's device/token in the web app?
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://localhost:8096';
(async () => {
  const b = await chromium.launch(); const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await p.goto(BASE + '/login'); await p.getByLabel('Username').fill('admin'); await p.getByLabel('Password').fill('change-me-now-please');
  await p.getByRole('button', { name: 'Sign in' }).click(); await p.waitForURL((u) => !u.pathname.startsWith('/login'));
  await p.goto(BASE + '/connect'); await p.waitForTimeout(1000);
  const ctl = p.getByRole('button', { name: /Everyone|everyone/ }).or(p.getByLabel(/everyone/i));
  console.log('everyone control:', await ctl.first().evaluate((e) => e.outerHTML.slice(0, 160)));
  await ctl.first().click(); await p.waitForTimeout(900);
  const rows = await p.locator('main').innerText();
  const i = rows.indexOf('three-day');
  console.log('row for maya three-day token:', rows.slice(Math.max(0, i - 80), i + 120).replace(/\n+/g, ' | '));
  const before = (await p.evaluate(() => fetch('/api/tokens?all=true').then((r) => r.json()))).tokens.filter((t) => t.name === 'three-day').length;
  const row = p.locator('tr, .row, li', { hasText: 'three-day' }).last();
  await row.getByRole('button', { name: /Revoke|Disconnect/ }).click(); await p.waitForTimeout(400);
  const confirm = p.locator('[role=dialog]').getByRole('button', { name: /Revoke|Disconnect/ });
  if (await confirm.count()) { await confirm.click(); }
  await p.waitForTimeout(900);
  const after = (await p.evaluate(() => fetch('/api/tokens?all=true').then((r) => r.json()))).tokens.filter((t) => t.name === 'three-day').length;
  console.log('maya three-day tokens before/after admin revoke:', before, after, '| toast:', await p.locator('.toast').last().innerText().catch(() => ''));
  await p.screenshot({ path: 'shots/admin_connect_everyone.png', fullPage: true });
  await b.close();
})();
