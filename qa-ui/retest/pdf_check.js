// Does the PDF actually render in the viewer? Uses new-headless Chromium (which has the built-in PDF viewer)
// and compares the app's preview URL (CSP sandbox) with the same bytes served without CSP sandbox.
const { chromium } = require('playwright');
const http = require('http'); const fs = require('fs');
const BASE = process.env.BASE || 'http://localhost:8096';
(async () => {
  const srv = http.createServer((q, s) => { s.writeHead(200, { 'Content-Type': 'application/pdf' }); s.end(fs.readFileSync('fixtures/doc.pdf')); }).listen(33420);
  let b;
  try { b = await chromium.launch({ channel: 'chromium' }); } catch (e) { console.log('channel chromium unavailable:', String(e).slice(0, 200)); b = await chromium.launch(); }
  const ctx = await b.newContext({ viewport: { width: 1200, height: 800 } }); const p = await ctx.newPage();
  // control: plain PDF, top level
  await p.goto('http://127.0.0.1:33420/doc.pdf').catch((e) => console.log('control goto', String(e).slice(0, 80)));
  await p.waitForTimeout(2000);
  console.log('control top-level PDF frames:', p.frames().map((f) => f.url().slice(0, 60)));
  await p.screenshot({ path: 'shots/pdf_control.png' });
  await p.goto(BASE + '/login'); await p.getByLabel('Username').fill('maya'); await p.getByLabel('Password').fill('demo-password-123');
  await p.getByRole('button', { name: 'Sign in' }).click(); await p.waitForURL((u) => !u.pathname.startsWith('/login'));
  await p.goto(BASE + '/view?path=' + encodeURIComponent('/users/maya/inbox/viewer/doc.pdf')); await p.waitForTimeout(3000);
  console.log('viewer frames:', p.frames().map((f) => f.url().slice(0, 70)));
  for (const f of p.frames()) { const t = await f.evaluate(() => document.body?.innerText.slice(0, 120)).catch(() => '(no access)'); console.log('  frame text', f.url().slice(0, 40), JSON.stringify(t)); }
  await p.screenshot({ path: 'shots/pdf_in_viewer.png' });
  // same page, but iframe the CSP-free control PDF
  await p.evaluate(() => { const f = document.querySelector('section.stage iframe'); f.src = 'http://127.0.0.1:33420/doc.pdf'; });
  await p.waitForTimeout(3000);
  console.log('viewer with CSP-free PDF frames:', p.frames().map((f) => f.url().slice(0, 70)));
  await p.screenshot({ path: 'shots/pdf_in_viewer_control.png' });
  await b.close(); srv.close();
})();
