// View hostile data (seeded by mcp_seed_hostile.py and journey_admin.js) on every page that shows it; report if any payload ran.
const { browser, ctxFor, watch, login } = require('./lib');
const BASE = process.env.BASE || 'http://localhost:8098';
(async () => {
  const b = await browser();
  const res = {};
  for (const u of ['maya', 'admin']) {
    const ctx = await ctxFor(b); const page = await ctx.newPage(); const bag = []; watch(page, bag);
    await login(page, u, BASE);
    const visits = u === 'maya'
      ? ['/files?path=%2Fusers%2Fmaya%2Finbox', '/files?path=%2Fusers%2Fmaya%2Finbox&file=%2Fusers%2Fmaya%2Finbox%2Fmeta-xss.md',
         "/files?path=%2Fusers%2Fmaya%2Finbox&file=" + encodeURIComponent("/users/maya/inbox/x'onmouseover='window.__xss=3'.html"),
         '/files?path=%2Fusers%2Fmaya%2Finbox&file=' + encodeURIComponent('/users/maya/inbox/big-preview.txt'), '/incoming', '/connect']
      : ['/overview', '/users', '/groups?id=5', '/audit', '/incoming', '/files?path=%2Fusers%2Fmaya%2Finbox', '/directory'];
    for (const v of visits) {
      await page.goto(BASE + v); await page.waitForTimeout(1500);
      await page.mouse.move(700, 300); await page.mouse.move(700, 400);
      const nm = v.replace(/[^a-z0-9]+/gi, '_').slice(0, 60);
      await page.screenshot({ path: `shots/x_${u}_${nm}.png`, fullPage: true });
      res[`${u} ${v.slice(0, 70)}`] = { xss: await page.evaluate(() => window.__xss || null), hOverflow: await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1) };
    }
    if (u === 'maya') {
      await page.goto(BASE + visits[0]); await page.waitForTimeout(800);
      await page.getByPlaceholder('Search in this folder').fill('meta');
      await page.waitForTimeout(800);
      res['maya search'] = await page.locator('section[aria-label="Files in folder"]').innerText();
      await page.screenshot({ path: 'shots/x_maya_search.png' });
    }
    res[u + ' console'] = bag;
    await ctx.close();
  }
  console.log(JSON.stringify(res, null, 1));
  await b.close();
})();
