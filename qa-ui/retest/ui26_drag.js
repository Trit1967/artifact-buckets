// UI-26: does the drop highlight clear when a drag leaves the window or ends?
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://localhost:8096';
(async () => {
  const b = await chromium.launch(); const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await p.goto(BASE + '/login'); await p.getByLabel('Username').fill('maya'); await p.getByLabel('Password').fill('demo-password-123');
  await p.getByRole('button', { name: 'Sign in' }).click(); await p.waitForURL((u) => !u.pathname.startsWith('/login'));
  await p.goto(BASE + '/files?path=%2Fusers%2Fmaya%2Finbox'); await p.waitForTimeout(900);
  const cls = () => p.locator('section.listing').getAttribute('class');
  const over = () => p.evaluate(() => { const dt = new DataTransfer(); dt.items.add(new File(['x'], 'x.txt')); window.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true })); });
  await over(); const during = await cls();
  await p.evaluate(() => window.dispatchEvent(new DragEvent('dragleave', { dataTransfer: new DataTransfer(), bubbles: true, relatedTarget: null })));
  await p.waitForTimeout(200); const leave = await cls();
  await over(); await p.evaluate(() => window.dispatchEvent(new DragEvent('dragleave', { bubbles: true, relatedTarget: document.querySelector('header') })));
  await p.waitForTimeout(200); const inner = await cls();
  await p.evaluate(() => window.dispatchEvent(new DragEvent('dragend', { bubbles: true })));
  await p.waitForTimeout(200); const end = await cls();
  console.log({ during, afterLeaveWindow: leave, afterInnerLeave: inner, afterDragend: end });
  await b.close();
})();
