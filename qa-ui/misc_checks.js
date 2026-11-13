// CSV export formula cells, session expiry redirect, Overview "Add person" link, drag-leave state, stale detail after new version.
const { browser, ctxFor, watch, login, apiIn } = require('./lib');
const BASE = process.env.BASE || 'http://localhost:8098';
const fs = require('fs');
(async () => {
  const b = await browser(); const ctx = await ctxFor(b, { ctx: { acceptDownloads: true } }); const page = await ctx.newPage();
  await login(page, 'admin', BASE);
  await page.goto(BASE + '/audit'); await page.waitForTimeout(1000);
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Export CSV' }).click()]);
  const csv = fs.readFileSync(await dl.path(), 'utf8'); fs.writeFileSync('out_audit_formula.csv', csv);
  console.log('formula cells:', csv.split('\n').filter((l) => /,"[=@+\-]/.test(l)).map((l) => l.slice(0, 160)));
  await page.goto(BASE + '/overview'); await page.waitForTimeout(800);
  await page.getByRole('link', { name: 'Add person' }).click(); await page.waitForTimeout(800);
  console.log('add person link ->', page.url(), 'dialog:', await page.locator('[role=dialog]').getAttribute('aria-label').catch(() => 'none'));
  await page.keyboard.press('Escape');
  // session expiry: end the session behind the UI's back, then navigate in-app
  await apiIn(page, 'POST', '/api/auth/logout');
  await page.getByRole('link', { name: 'Storage' }).first().click(); await page.waitForTimeout(1200);
  console.log('after server-side logout, in-app nav ->', page.url());
  await page.screenshot({ path: 'shots/m_session_expired.png' });
  // maya: drag-leave and stale detail
  await login(page, 'maya', BASE);
  await page.goto(BASE + '/files?path=%2Fusers%2Fmaya%2Finbox&file=%2Fusers%2Fmaya%2Finbox%2Fdropped.txt'); await page.waitForTimeout(1200);
  console.log('detail before:', (await page.locator('aside .note').first().innerText()));
  await page.evaluate(() => { const dt = new DataTransfer(); dt.items.add(new File(['x'], 'x.txt')); window.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true })); window.dispatchEvent(new DragEvent('dragleave', { dataTransfer: dt, bubbles: true })); document.dispatchEvent(new DragEvent('dragend', { bubbles: true })); });
  await page.waitForTimeout(500);
  console.log('listing classes after drag left the window:', await page.locator('section.listing').getAttribute('class'));
  await page.screenshot({ path: 'shots/m_drag_left.png' });
  fs.writeFileSync('/tmp/qa-ui/dropped.txt', 'new content for version two\n');
  await page.getByRole('button', { name: 'Upload' }).click();
  await page.locator('input[type=file]').setInputFiles('/tmp/qa-ui/dropped.txt'); await page.waitForTimeout(1800);
  console.log('detail after uploading v2 of the open file:', await page.locator('aside .note').first().innerText(), '| preview:', (await page.locator('aside .pv').innerText()).trim());
  await page.screenshot({ path: 'shots/m_stale_detail.png' });
  await b.close();
})();
