// UI-32: what sticks out past 390 px on the admin overview?
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://localhost:8096';
(async () => {
  const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 390, height: 844 } }); const p = await ctx.newPage();
  await p.goto(BASE + '/login'); await p.getByLabel('Username').fill('admin'); await p.getByLabel('Password').fill('change-me-now-please');
  await p.getByRole('button', { name: 'Sign in' }).click(); await p.waitForURL((u) => !u.pathname.startsWith('/login'));
  for (const path of ['/overview', '/files', '/users', '/audit', '/storage', '/incoming', '/groups', '/connect', '/view?path=%2Fusers%2Fmaya%2Finbox%2Flink-target.md']) {
    await p.goto(BASE + path); await p.waitForTimeout(1200);
    const r = await p.evaluate(() => ({ over: document.documentElement.scrollWidth - innerWidth,
      culprits: [...document.querySelectorAll('body *')].filter((e) => e.getBoundingClientRect().right > innerWidth + 1 && !e.closest('.scroll-x,.csvwrap,.codeview,table')).slice(0, 4).map((e) => e.tagName + '.' + String(e.className).slice(0, 30) + ' "' + e.textContent.slice(0, 50) + '" parent=' + e.parentElement.className.slice(0,20) + ' r=' + Math.round(e.getBoundingClientRect().right)) }));
    console.log(path, JSON.stringify(r));
  }
  await p.goto(BASE + '/overview'); await p.waitForTimeout(1000); await p.screenshot({ path: 'shots/a390_overview_full.png', fullPage: true });
  await b.close();
})();
