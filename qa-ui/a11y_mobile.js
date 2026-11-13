// Mobile detail placement, sign-out reachability at narrow widths, and keyboard focus visibility on custom controls.
const { browser, ctxFor, login } = require('./lib');
const BASE = process.env.BASE || 'http://localhost:8098';
(async () => {
  const b = await browser();
  const ctx = await ctxFor(b, { viewport: { width: 390, height: 844 } }); const page = await ctx.newPage();
  await login(page, 'maya', BASE);
  await page.goto(BASE + '/files?path=%2Fusers%2Fmaya%2Finbox'); await page.waitForTimeout(1200);
  await page.locator('button.rowbtn', { hasText: 'notes.md' }).click(); await page.waitForTimeout(1200);
  const det = await page.locator('aside[aria-label="File details"]').boundingBox();
  console.log('390 detail top y', det && Math.round(det.y), 'viewport 844, scrollY', await page.evaluate(() => scrollY));
  console.log('390 sign out visible', await page.getByRole('button', { name: /Maya/ }).isVisible().catch(() => false));
  await page.screenshot({ path: 'shots/m_390_after_click.png' });
  await page.setViewportSize({ width: 900, height: 900 });
  console.log('900 sign out visible', await page.locator('.mebtn').isVisible());
  await page.setViewportSize({ width: 901, height: 900 });
  console.log('901 sign out visible', await page.locator('.mebtn').isVisible());
  // keyboard focus
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(BASE + '/files?path=%2Fusers%2Fmaya%2Finbox'); await page.waitForTimeout(1000);
  const outlines = await page.evaluate(() => {
    const o = {};
    for (const [k, sel] of Object.entries({ rowbtn: 'button.rowbtn', mebtn: 'button.mebtn', nav: 'a.nav', leaf: 'a.leaf', btn: 'button.btn', field: 'input.field' })) {
      const el = document.querySelector(sel); if (!el) continue; el.focus();
      const cs = getComputedStyle(el); o[k] = `${cs.outlineStyle} ${cs.outlineWidth} ${cs.outlineColor} | bg ${cs.backgroundColor} | shadow ${cs.boxShadow}`;
    }
    return o;
  });
  console.log(JSON.stringify(outlines, null, 1));
  // tab sequence screenshot: tab until a rowbtn is focused
  await page.locator('body').click({ position: { x: 5, y: 5 } });
  for (let i = 0; i < 40; i++) { await page.keyboard.press('Tab'); if (await page.evaluate(() => document.activeElement?.classList.contains('rowbtn'))) break; }
  await page.keyboard.press('Tab');
  await page.screenshot({ path: 'shots/m_focus_rowbtn.png' });
  console.log('focused', await page.evaluate(() => document.activeElement.className + ' ' + document.activeElement.innerText.slice(0, 30)));
  await page.goto(BASE + '/incoming'); await page.waitForTimeout(800);
  const seg = await page.evaluate(() => { const el = document.querySelector('button.seg'); el.focus(); const cs = getComputedStyle(el); return el.matches(':focus-visible') + ' ' + cs.outlineStyle + ' ' + cs.outlineWidth; });
  console.log('seg focus', seg);
  // Audit row expand by keyboard?
  await login(page, 'admin', BASE).catch(() => {});
  await page.goto(BASE + '/audit'); await page.waitForTimeout(800);
  console.log('audit row tabbable', await page.evaluate(() => document.querySelector('tr.click')?.tabIndex));
  // modal focus trap: open Share and tab 30 times; is focus still in dialog?
  await page.goto(BASE + '/files'); await page.waitForTimeout(800);
  await page.getByRole('button', { name: 'Share' }).click(); await page.waitForTimeout(500);
  for (let i = 0; i < 25; i++) await page.keyboard.press('Tab');
  console.log('focus inside dialog after 25 tabs', await page.evaluate(() => !!document.activeElement.closest('[role=dialog]')), await page.evaluate(() => document.activeElement.outerHTML.slice(0, 80)));
  await b.close();
})();
