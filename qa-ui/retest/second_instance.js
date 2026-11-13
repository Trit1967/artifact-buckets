// Instance on :8095 with AB_TRASH_DAYS=7 and AB_OAUTH_REDIRECT_HOSTS=partner.example.
// UI-22: does the UI say 7 days? UI-1: a listed https redirect works and the consent page names the host.
const { chromium } = require('playwright');
const crypto = require('crypto');
const BASE = 'http://localhost:8095';
const b64 = (b) => b.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } }); const p = await ctx.newPage();
  await ctx.route(/^https:\/\/partner\.example\/.*/, (r) => r.fulfill({ status: 200, body: 'partner got ' + r.request().url() }));
  await p.goto(BASE + '/login'); await p.getByLabel('Username').fill('admin'); await p.getByLabel('Password').fill('change-me-now-please');
  await p.getByRole('button', { name: 'Sign in' }).click(); await p.waitForURL((u) => !u.pathname.startsWith('/login'));
  const csrf = (await p.evaluate(() => fetch('/api/auth/me').then((r) => r.json()))).csrf;
  await p.evaluate((c) => fetch('/api/fs/save-text', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': c }, body: JSON.stringify({ path: '/users/admin/inbox/t.txt', content: 'x' }) }), csrf);
  await p.goto(BASE + '/files?path=%2Fusers%2Fadmin%2Finbox&file=%2Fusers%2Fadmin%2Finbox%2Ft.txt'); await p.waitForTimeout(1000);
  await p.locator('aside').getByRole('button', { name: 'Delete' }).click();
  console.log('UI-22 delete dialog:', (await p.locator('[role=dialog]').innerText()).replace(/\n+/g, ' | '));
  await p.getByRole('button', { name: 'Move to trash' }).click(); await p.waitForTimeout(800);
  console.log('UI-22 toast:', await p.locator('.toast').last().innerText());
  await p.goto(BASE + '/trash'); await p.waitForTimeout(800);
  console.log('UI-22 trash page:', (await p.locator('main').innerText()).replace(/\n+/g, ' | ').slice(0, 300));
  for (const uri of ['https://partner.example/cb', 'https://evil.example/cb', 'http://partner.example/cb', 'https://PARTNER.example/cb']) {
    const r = await fetch(BASE + '/oauth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_name: 'Claude Code', redirect_uris: [uri] }) });
    console.log('UI-1 register', uri, r.status);
  }
  const redirect = 'https://partner.example/cb';
  const cid = (await (await fetch(BASE + '/oauth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ client_name: 'Claude Code', redirect_uris: [redirect] }) })).json()).client_id;
  const v = b64(crypto.randomBytes(32)); const ch = b64(crypto.createHash('sha256').update(v).digest());
  await p.goto(BASE + '/oauth/authorize?' + new URLSearchParams({ client_id: cid, redirect_uri: redirect, response_type: 'code', state: 'x', code_challenge: ch, code_challenge_method: 'S256' }));
  await p.waitForURL(/\/consent/); await p.waitForTimeout(500);
  console.log('UI-1 consent for listed host:', (await p.locator('form').innerText()).replace(/\n+/g, ' | ').slice(0, 260));
  console.log('UI-1 device name prefill:', await p.getByLabel('Name this computer').inputValue());
  await p.screenshot({ path: 'shots/consent_listed_host.png' });
  await b.close();
})();
