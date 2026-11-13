// UI-2: press Enter in "Name this computer" on the consent page; which decision reaches the callback?
const { chromium } = require('playwright');
const crypto = require('crypto');
const BASE = process.env.BASE || 'http://localhost:8096';
const b64 = (b) => b.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext(); const page = await ctx.newPage();
  const navs = []; page.on('framenavigated', (f) => { if (f === page.mainFrame()) navs.push(f.url()); });
  await page.goto(BASE + '/login'); await page.getByLabel('Username').fill('jonas'); await page.getByLabel('Password').fill('demo-password-123');
  await page.getByRole('button', { name: 'Sign in' }).click(); await page.waitForURL((u) => !u.pathname.startsWith('/login'));
  for (const mode of ['enter', 'click-connect', 'click-cancel']) {
    const redirect = 'http://127.0.0.1:33419/callback';
    const cid = (await (await fetch(BASE + '/oauth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_name: 'Claude Code', redirect_uris: [redirect] }) })).json()).client_id;
    const v = b64(crypto.randomBytes(32)); const ch = b64(crypto.createHash('sha256').update(v).digest());
    await page.goto(BASE + '/oauth/authorize?' + new URLSearchParams({ client_id: cid, redirect_uri: redirect, response_type: 'code', state: mode, code_challenge: ch, code_challenge_method: 'S256' }));
    await page.waitForURL(/\/consent/);
    const order = await page.evaluate(() => [...document.querySelectorAll('form button')].map((x) => `${x.value}@x=${Math.round(x.getBoundingClientRect().x)}`));
    navs.length = 0;
    if (mode === 'enter') { const f = page.getByLabel('Name this computer'); await f.fill('Edited name'); await f.press('Enter'); }
    else if (mode === 'click-connect') await page.getByRole('button', { name: 'Connect' }).click();
    else await page.getByRole('button', { name: 'Cancel' }).click();
    await page.waitForTimeout(1500);
    const cb = navs.find((u) => u.startsWith('http://127.0.0.1:33419')) || page.url();
    const u = new URL(cb);
    console.log(mode, 'buttons(DOM order, x):', order.join(' '), '-> callback', u.origin + u.pathname, 'code?', !!u.searchParams.get('code'), 'error=', u.searchParams.get('error'), 'state=', u.searchParams.get('state'));
  }
  await b.close();
})();
