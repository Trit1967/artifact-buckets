// UI-36 recheck with a file that has long tags: Files detail panel and the new viewer rail.
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://localhost:8096';
const P = '/users/maya/inbox/viewer/data.csv';
(async () => {
  const b = await chromium.launch(); const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await p.goto(BASE + '/login'); await p.getByLabel('Username').fill('maya'); await p.getByLabel('Password').fill('demo-password-123');
  await p.getByRole('button', { name: 'Sign in' }).click(); await p.waitForURL((u) => !u.pathname.startsWith('/login'));
  const check = (sel) => p.evaluate((sel) => { const a = document.querySelector(sel); const r = a.getBoundingClientRect();
    return [...a.querySelectorAll('.tag, span, a')].filter((e) => { const q = e.getBoundingClientRect(); return q.width && (q.right > r.right + 1 || q.left < r.left - 1); })
      .map((e) => `${e.className.split(' ')[0]} "${e.textContent.slice(0, 20)}" L${Math.round(e.getBoundingClientRect().left)}-R${Math.round(e.getBoundingClientRect().right)} (box ${Math.round(r.left)}-${Math.round(r.right)})`).slice(0, 4); }, sel);
  await p.goto(BASE + '/files?path=' + encodeURIComponent('/users/maya/inbox/viewer') + '&file=' + encodeURIComponent(P)); await p.waitForTimeout(1500);
  console.log('Files detail panel overflow:', await check('aside[aria-label="File details"]'));
  await p.locator('aside[aria-label="File details"]').screenshot({ path: 'shots/detail_tags.png' });
  const sub = await p.evaluate(() => { const r = document.querySelector('button.rowbtn.on'); const n = r?.querySelector('.note'); return n ? { row: Math.round(r.getBoundingClientRect().height), sub: n.textContent, subH: Math.round(n.getBoundingClientRect().height) } : null; });
  console.log('row sub-line:', sub);
  await p.goto(BASE + '/view?path=' + encodeURIComponent(P)); await p.waitForTimeout(1500);
  console.log('Viewer rail overflow:', await check('aside.rail'));
  console.log('Viewer rail history:', (await p.locator('.vers').innerText()).replace(/\n+/g, ' | '));
  await p.locator('aside.rail').screenshot({ path: 'shots/viewer_rail_tags.png' });
  await b.close();
})();
