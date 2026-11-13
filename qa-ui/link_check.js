// Open the `link` that save_artifact returns (BASE/files?path=<file path>) as the saver, and see what the Files page shows.
const { browser, ctxFor, login } = require('./lib');
const BASE = process.env.BASE || 'http://localhost:8098';
(async () => {
  const b = await browser(); const ctx = await ctxFor(b); const page = await ctx.newPage();
  await login(page, 'maya', BASE);
  await page.goto(BASE + '/files?path=' + encodeURIComponent('/shared/platform-eng/projects/scratch/qa-skill-check.md')); await page.waitForTimeout(1200);
  console.log(await page.locator('section[aria-label="Files in folder"]').innerText());
  await page.screenshot({ path: 'shots/m_save_link.png' });
  await b.close();
})();
