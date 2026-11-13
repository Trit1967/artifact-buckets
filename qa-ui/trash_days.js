// With AB_TRASH_DAYS=7 (instance on 8097), what retention does the UI promise?
const { browser, ctxFor, login, apiIn } = require('./lib');
const BASE = 'http://localhost:8097';
(async () => {
  const b = await browser(); const page = await (await ctxFor(b)).newPage();
  await login(page, 'admin', BASE);
  await apiIn(page, 'POST', '/api/fs/save-text', { path: '/users/admin/inbox/t.txt', content: 'x' }).then((r) => console.log('save', r.status));
  await page.goto(BASE + '/files?path=%2Fusers%2Fadmin%2Finbox&file=%2Fusers%2Fadmin%2Finbox%2Ft.txt'); await page.waitForTimeout(1000);
  await page.getByRole('button', { name: 'Delete' }).click();
  console.log('dialog:', await page.locator('.dlg').innerText());
  await page.getByRole('button', { name: 'Move to trash' }).click(); await page.waitForTimeout(800);
  console.log('toast:', await page.locator('.toast').innerText());
  await page.goto(BASE + '/trash'); await page.waitForTimeout(800);
  console.log('trash:', await page.locator('main').innerText());
  console.log('api:', JSON.stringify((await apiIn(page, 'GET', '/api/trash')).body).slice(0, 300));
  await b.close();
})();
