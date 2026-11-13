// UI-33/UI-44/UI-37: storage page after a drain, and the sidebar pool card.
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://localhost:8096';
(async () => {
  const b = await chromium.launch(); const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await p.goto(BASE + '/login'); await p.getByLabel('Username').fill('admin'); await p.getByLabel('Password').fill('change-me-now-please');
  await p.getByRole('button', { name: 'Sign in' }).click(); await p.waitForURL((u) => !u.pathname.startsWith('/login'));
  await p.goto(BASE + '/storage'); await p.waitForTimeout(1200);
  const t = await p.locator('main').innerText();
  console.log('vol-a card:', t.slice(t.indexOf('vol-a'), t.indexOf('vol-a') + 220).replace(/\n+/g, ' | '));
  await p.screenshot({ path: 'shots/admin_storage_drained.png', fullPage: true });
  await p.locator('.meter-card').screenshot({ path: 'shots/sidebar_pool_card.png' });
  await b.close();
})();
