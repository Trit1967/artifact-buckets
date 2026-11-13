// Visit every page as admin, member (maya), auditor (lena); screenshot, record console errors and HTTP failures.
const { browser, ctxFor, watch, login, BASE } = require('./lib');
const fs = require('fs');
const PAGES = ['/files', '/trash', '/incoming', '/connect', '/groups', '/overview', '/users', '/storage', '/directory', '/smb', '/audit', '/nope'];
const ROLES = (process.env.ROLES || 'admin,maya,lena').split(',');
const W = +(process.env.W || 1440), H = +(process.env.H || 900);
(async () => {
  const b = await browser();
  const out = {};
  for (const u of ROLES) {
    const ctx = await ctxFor(b, { viewport: { width: W, height: H } });
    const page = await ctx.newPage();
    const bag = [];
    watch(page, bag);
    page.on('response', (r) => { if (r.status() >= 400) bag.push(`[http ${r.status()}] ${r.request().method()} ${r.url()}`); });
    await login(page, u);
    out[u] = { landed: new URL(page.url()).pathname, pages: {} };
    for (const p of PAGES) {
      bag.length = 0;
      await page.goto(BASE + p);
      await page.waitForTimeout(1200);
      const text = (await page.locator('main').innerText().catch(() => '')).slice(0, 400).replace(/\s+/g, ' ');
      const nav = await page.locator('nav[aria-label=Main] a').allInnerTexts();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      await page.screenshot({ path: `shots/${u}_${p.slice(1)}_${W}.png`, fullPage: true });
      out[u].pages[p] = { errors: [...bag], text, hOverflow: overflow, nav: p === '/files' ? nav : undefined };
    }
    await ctx.close();
  }
  fs.writeFileSync(`out_pages_${W}.json`, JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out, null, 1));
  await b.close();
})();
