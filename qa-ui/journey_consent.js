// Claude Code consent flow in a real browser: login redirect, Allow, Cancel, Enter key, https redirect display, device list + disconnect.
const { browser, ctxFor, watch, login } = require('./lib');
const BASE = process.env.BASE || 'http://localhost:8098';
const crypto = require('crypto');
const fs = require('fs');
const log = [];
const step = (s, d) => { log.push([s, d]); console.log('STEP', s, typeof d === 'string' ? d.slice(0, 900) : JSON.stringify(d).slice(0, 900)); };
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
(async () => {
  const b = await browser();
  const ctx = await ctxFor(b);
  const page = await ctx.newPage();
  const bag = []; watch(page, bag);
  const hits = { get length() { return this.all().length; }, all() { try { return require('fs').readFileSync('/tmp/qa-ui/cb_hits.txt','utf8').trim().split('\n').filter(Boolean); } catch { return []; } } };
  const last = () => { const a = hits.all(); return a[a.length - 1]; };
    await ctx.route(/^https:\/\/evil\.example\/.*/, (route) => { hits.push(route.request().url()); route.fulfill({ status: 200, body: 'evil got it' }); });
  const redirect = 'http://127.0.0.1:33418/callback';
  // A. not signed in, Allow
  let cid = await register(redirect);
  let a = authUrl(cid, redirect, 'st-A');
  await page.goto(a.url); await page.waitForTimeout(800);
  step('A login page', { url: page.url(), h1: await page.locator('h1').innerText() });
  await page.screenshot({ path: 'shots/c_login_from_claude.png' });
  await page.getByLabel('Username').fill('jonas'); await page.getByLabel('Password').fill('demo-password-123');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL(/\/consent/); await page.waitForTimeout(600);
  step('A consent', { url: page.url().slice(0, 120), text: await page.locator('form').innerText() });
  await page.screenshot({ path: 'shots/c_consent.png' });
  await page.getByLabel('Name this computer').fill('QA laptop <img src=x onerror=window.__xss=9>');
  await page.getByRole('button', { name: 'Connect' }).click();
  await page.waitForTimeout(1500);
  step('A after connect', { url: page.url(), bag: [...bag], body: (await page.locator('body').innerText()).slice(0, 300) });
  await page.screenshot({ path: 'shots/c_after_connect.png' });
  const u = new URL(last());
  step('A callback', { url: u.origin + u.pathname, state: u.searchParams.get('state'), hasCode: !!u.searchParams.get('code') });
  const tr = await fetch(BASE + '/oauth/token', { method: 'POST', body: new URLSearchParams({ grant_type: 'authorization_code', code: u.searchParams.get('code'), client_id: cid, redirect_uri: redirect, code_verifier: a.verifier }) });
  const tj = await tr.json();
  step('A token exchange', { status: tr.status, keys: Object.keys(tj) });
  fs.writeFileSync('/tmp/qa-ui/jonas_oauth.json', JSON.stringify({ ...tj, client_id: cid }));
  // B. signed in, Cancel
  cid = await register(redirect);
  a = authUrl(cid, redirect, 'st-B');
  await page.goto(a.url); await page.waitForTimeout(800);
  step('B consent direct', page.url().includes('/consent'));
  await page.getByRole('button', { name: 'Cancel' }).click(); await page.waitForTimeout(800);
  step('B cancel callback', last());
  // C. press Enter in the device name field
  cid = await register(redirect);
  a = authUrl(cid, redirect, 'st-C');
  await page.goto(a.url); await page.waitForTimeout(800);
  await page.getByLabel('Name this computer').fill('Typed then Enter');
  await page.getByLabel('Name this computer').press('Enter'); await page.waitForTimeout(800);
  step('C enter key result', last());
  // D. https redirect to another site: what does the consent page tell the person?
  const evil = 'https://evil.example/cb';
  cid = await register(evil, 'Claude Code');
  a = authUrl(cid, evil, 'st-D');
  await page.goto(a.url); await page.waitForTimeout(800);
  step('D consent text for https redirect', await page.locator('form').innerText());
  await page.screenshot({ path: 'shots/c_consent_evil.png' });
  await page.getByRole('button', { name: 'Connect' }).click(); await page.waitForTimeout(800);
  step('D after connect', last().replace(/code=[^&]+/, 'code=<redacted>'));
  // E. consent page with no params
  await page.goto(BASE + '/consent'); await page.waitForTimeout(600);
  step('E bare consent', await page.locator('body').innerText());
  await page.getByRole('button', { name: 'Connect' }).click(); await page.waitForTimeout(800);
  step('E submit bare', { url: page.url(), body: (await page.locator('body').innerText()).slice(0, 200) });
  // F. devices list and disconnect
  await page.goto(BASE + '/connect'); await page.waitForTimeout(900);
  step('F devices', await page.locator('section', { hasText: 'Your connected computers' }).innerText());
  await page.screenshot({ path: 'shots/c_connect_devices.png', fullPage: true });
  const mcpCall = async (tok) => (await fetch(BASE + '/mcp', { method: 'POST', headers: { Authorization: 'Bearer ' + tok, 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }) })).status;
  step('F mcp before disconnect', await mcpCall(tj.access_token));
  await page.locator('.dev', { hasText: 'QA laptop' }).getByRole('button', { name: 'Disconnect' }).click(); await page.waitForTimeout(900);
  step('F after disconnect', { toast: await page.locator('.toast').allInnerTexts(), list: await page.locator('section', { hasText: 'Your connected computers' }).innerText(), mcp: await mcpCall(tj.access_token) });
  // G. narrow consent
  await page.setViewportSize({ width: 390, height: 844 });
  cid = await register(redirect); a = authUrl(cid, redirect, 'st-G');
  await page.goto(a.url); await page.waitForTimeout(800);
  await page.screenshot({ path: 'shots/c_consent_390.png', fullPage: true });
  // H. sign out from the menu, then visit a page -> login with next, then back
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(BASE + '/files'); await page.waitForTimeout(700);
  await page.locator('.mebtn').click(); await page.getByRole('button', { name: 'Sign out' }).click(); await page.waitForTimeout(800);
  step('H after sign out', page.url());
  const me = await page.evaluate(async () => (await (await fetch('/api/auth/me')).json()).user);
  step('H me after sign out', me);
  await page.goto(BASE + '/groups?id=1'); await page.waitForTimeout(600);
  step('H redirect to login', page.url());
  await page.getByLabel('Username').fill('jonas'); await page.getByLabel('Password').fill('wrong-password-xx');
  await page.getByRole('button', { name: 'Sign in' }).click(); await page.waitForTimeout(800);
  step('H wrong password', await page.locator('.err').innerText());
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByRole('button', { name: 'Sign in' }).click(); await page.waitForTimeout(1000);
  step('H back after sign in', page.url());
  // I. open redirect attempt via next
  await page.locator('.mebtn').click(); await page.getByRole('button', { name: 'Sign out' }).click(); await page.waitForTimeout(600);
  for (const n of ['//evil.example/x', 'https://evil.example', '/\\evil.example', '/oauth/../\\evil.example']) {
    await page.goto(BASE + '/login?next=' + encodeURIComponent(n)); await page.waitForTimeout(400);
    await page.getByLabel('Username').fill('jonas'); await page.getByLabel('Password').fill('demo-password-123');
    await page.getByRole('button', { name: 'Sign in' }).click(); await page.waitForTimeout(1200);
    step('I next=' + n, page.url());
    await page.goto(BASE + '/files'); await page.waitForTimeout(400);
    await page.locator('.mebtn').click(); await page.getByRole('button', { name: 'Sign out' }).click(); await page.waitForTimeout(600);
  }
  step('xss', await page.evaluate(() => window.__xss || null));
  step('console', bag);
  fs.writeFileSync('out_journey_consent.json', JSON.stringify(log, null, 1));
  await b.close();
})().catch((e) => { console.error('FAILED', e); process.exit(1); });
