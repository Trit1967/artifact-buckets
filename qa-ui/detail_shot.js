// Screenshot the file detail panel for given files (maya), and measure what overflows horizontally.
const { browser, ctxFor, login } = require('./lib');
const BASE = process.env.BASE || 'http://localhost:8098';
const files = process.argv.slice(2);
(async () => {
  const b = await browser(); const ctx = await ctxFor(b); const page = await ctx.newPage();
  await login(page, process.env.U || 'maya', BASE);
  for (const [i, f] of files.entries()) {
    const dir = f.split('/').slice(0, -1).join('/');
    await page.goto(`${BASE}/files?path=${encodeURIComponent(dir)}&file=${encodeURIComponent(f)}`); await page.waitForTimeout(1500);
    const wide = await page.evaluate(() => [...document.querySelectorAll('main *')].filter((e) => e.getBoundingClientRect().right > innerWidth + 1).slice(0, 5).map((e) => e.tagName + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().right) + ' ' + (e.innerText || '').slice(0, 50)));
    console.log(f.slice(0, 60), JSON.stringify(wide));
    await page.screenshot({ path: `shots/d_${i}.png`, fullPage: true });
  }
  await b.close();
})();
