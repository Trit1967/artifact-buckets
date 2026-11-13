// Shared helpers for the UI QA scripts.
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://localhost:8080';
const PW = { admin: 'change-me-now-please' };
const pw = (u) => PW[u] || 'demo-password-123';

async function browser() { return chromium.launch(); }

async function ctxFor(b, opts = {}) {
  const ctx = await b.newContext({ viewport: opts.viewport || { width: 1440, height: 900 }, deviceScaleFactor: 1, ...opts.ctx });
  return ctx;
}

function watch(page, bag) {
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') bag.push(`[${m.type()}] ${page.url()} :: ${m.text()}`); });
  page.on('pageerror', (e) => bag.push(`[pageerror] ${page.url()} :: ${e.message}`));
  page.on('dialog', (d) => { bag.push(`[dialog] ${d.type()} ${d.message()}`); d.dismiss().catch(() => {}); });
}

async function login(page, user, base = BASE) {
  await page.goto(base + '/login');
  await page.getByLabel('Username').fill(user);
  await page.getByLabel('Password').fill(pw(user));
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 10000 });
}

// Fetch helper: API call from inside the page (cookie + csrf).
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

module.exports = { chromium, BASE, pw, browser, ctxFor, watch, login, apiIn };
