// Member file journey on the private instance (BASE=http://localhost:8098): upload, drag-drop, previews, download,
// new folder, rename, new version, versions restore, move, delete, trash restore, share/unshare, search.
const { browser, ctxFor, watch, login, apiIn } = require('./lib');
const BASE = process.env.BASE || 'http://localhost:8098';
const fs = require('fs');
const F = __dirname + '/fixtures/';
const log = [];
const step = (s, d) => { log.push([s, d]); console.log('STEP', s, typeof d === 'string' ? d : JSON.stringify(d)); };
(async () => {
  const b = await browser();
  const ctx = await ctxFor(b, { ctx: { acceptDownloads: true } });
  const page = await ctx.newPage();
  const bag = []; watch(page, bag);
  await page.addInitScript(() => { window.__msgs = []; window.addEventListener('message', (e) => window.__msgs.push(e.data)); });
  await login(page, 'maya', BASE);
  const inbox = '/users/maya/inbox';
  await page.goto(BASE + '/files?path=' + encodeURIComponent(inbox));
  await page.waitForTimeout(800);
  // upload via chooser
  await page.getByRole('button', { name: 'Upload' }).click();
  await page.locator('input[type=file]').setInputFiles(['probe.html', 'probe.svg', 'notes.md', 'data.json', 'pic.png', 'doc.pdf', 'versioned.txt'].map((f) => F + f));
  await page.waitForTimeout(2500);
  step('upload-chooser', await page.locator('section[aria-label="Files in folder"]').innerText());
  // drag and drop onto the window
  await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.items.add(new File(['dropped via drag and drop'], 'dropped.txt', { type: 'text/plain' }));
    window.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }));
    window.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
  });
  await page.waitForTimeout(1500);
  step('drag-drop', (await page.locator('section[aria-label="Files in folder"]').innerText()).includes('dropped.txt'));
  step('listing-hot-after-drop', await page.locator('section.listing').getAttribute('class'));
  await page.screenshot({ path: 'shots/j_files_uploaded.png' });
  // previews
  for (const n of ['probe.html', 'probe.svg', 'notes.md', 'data.json', 'pic.png', 'doc.pdf', 'dropped.txt']) {
    await page.locator('button.rowbtn', { hasText: n }).first().click();
    await page.waitForTimeout(2200);
    const det = page.locator('aside[aria-label="File details"]');
    const kind = await det.locator('.pv').evaluate((el) => el.firstElementChild?.tagName + ' ' + (el.firstElementChild?.getAttribute('sandbox') ?? ''));
    let inner = '';
    if (kind.startsWith('IFRAME')) {
      const fr = page.frameLocator('aside iframe');
      inner = await fr.locator('body').innerText({ timeout: 3000 }).catch((e) => 'n/a ' + e.message.slice(0, 60));
    } else inner = (await det.locator('.pv').innerText()).slice(0, 120);
    step('preview ' + n, { kind, inner });
    await page.screenshot({ path: `shots/j_preview_${n}.png` });
  }
  step('postMessages', await page.evaluate(() => window.__msgs));
  // download
  await page.locator('button.rowbtn', { hasText: 'notes.md' }).first().click();
  await page.waitForTimeout(800);
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: 'Download' }).click()]);
  const p = await dl.path();
  step('download', { name: dl.suggestedFilename(), same: fs.readFileSync(p, 'utf8') === fs.readFileSync(F + 'notes.md', 'utf8') });
  // new folder
  await page.getByRole('button', { name: 'New folder' }).click();
  await page.getByLabel('Name').fill('reports');
  await page.getByRole('button', { name: 'Create' }).click();
  await page.waitForTimeout(800);
  step('mkdir', await page.locator('.toast').allInnerTexts());
  // rename versioned.txt
  await page.locator('button.rowbtn', { hasText: 'versioned.txt' }).first().click();
  await page.waitForTimeout(600);
  await page.getByRole('button', { name: 'Rename' }).click();
  await page.getByLabel('New name').fill('renamed.txt');
  await page.locator('.dlg').getByRole('button', { name: 'Rename' }).click();
  await page.waitForTimeout(900);
  step('rename url', page.url());
  // new version by uploading same name
  fs.writeFileSync('/tmp/qa-ui/renamed.txt', 'v2 content\n');
  if (!(await page.locator('input[type=file]').count())) await page.getByRole('button', { name: 'Upload' }).click();
  await page.locator('input[type=file]').setInputFiles('/tmp/qa-ui/renamed.txt');
  await page.waitForTimeout(1500);
  await page.locator('button.rowbtn', { hasText: 'renamed.txt' }).first().click();
  await page.waitForTimeout(900);
  step('after v2 detail', await page.locator('aside[aria-label="File details"]').innerText());
  await page.getByRole('button', { name: 'Versions' }).click();
  await page.waitForTimeout(800);
  step('versions dialog', await page.locator('.dlg').innerText());
  await page.screenshot({ path: 'shots/j_versions.png' });
  await page.locator('.dlg').getByRole('button', { name: 'Restore' }).first().click();
  await page.waitForTimeout(1000);
  step('versions after restore', await page.locator('.dlg').innerText().catch(() => 'dialog gone'));
  await page.keyboard.press('Escape');
  await page.waitForTimeout(600);
  step('preview text after restore', await page.locator('aside[aria-label="File details"] .pv').innerText().catch(() => ''));
  // move
  await page.getByRole('button', { name: 'Move' }).click();
  await page.getByLabel('To folder').fill(inbox + '/reports');
  await page.locator('.dlg').getByRole('button', { name: 'Move' }).click();
  await page.waitForTimeout(900);
  step('move', { toasts: await page.locator('.toast').allInnerTexts(), url: page.url() });
  // go to reports and delete
  await page.goto(BASE + '/files?path=' + encodeURIComponent(inbox + '/reports'));
  await page.waitForTimeout(800);
  await page.locator('button.rowbtn', { hasText: 'renamed.txt' }).first().click();
  await page.waitForTimeout(600);
  await page.getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('button', { name: 'Move to trash' }).click();
  await page.waitForTimeout(900);
  step('delete', await page.locator('.toast').allInnerTexts());
  await page.goto(BASE + '/trash');
  await page.waitForTimeout(800);
  step('trash', await page.locator('main').innerText());
  await page.screenshot({ path: 'shots/j_trash.png' });
  await page.getByRole('button', { name: 'Restore' }).first().click();
  await page.waitForTimeout(900);
  step('trash restore', { toasts: await page.locator('.toast').allInnerTexts(), after: await page.locator('main section').innerText() });
  // share inbox with jonas
  await page.goto(BASE + '/files?path=' + encodeURIComponent(inbox));
  await page.waitForTimeout(800);
  await page.getByRole('button', { name: 'Share' }).click();
  await page.waitForTimeout(600);
  await page.getByLabel('Kind').selectOption('user');
  await page.getByLabel('Name', { exact: true }).fill('jonas');
  await page.getByLabel('Role').selectOption('viewer');
  await page.locator('.dlg').getByRole('button', { name: 'Share' }).click();
  await page.waitForTimeout(900);
  step('share', await page.locator('.dlg').innerText());
  await page.screenshot({ path: 'shots/j_share.png' });
  const rm = page.locator('.dlg button[aria-label^="Remove"]');
  step('remove buttons', await rm.evaluateAll((els) => els.map((e) => e.getAttribute('aria-label'))));
  await page.locator('.dlg button[aria-label="Remove jonas"]').click();
  await page.waitForTimeout(900);
  step('unshare', { toasts: await page.locator('.toast').allInnerTexts(), dlg: await page.locator('.dlg').innerText() });
  await page.keyboard.press('Escape');
  // search
  await page.getByPlaceholder('Search in this folder').fill('probe');
  await page.waitForTimeout(900);
  step('search', await page.locator('section[aria-label="Files in folder"]').innerText());
  await page.getByPlaceholder('Search in this folder').fill('zzzz');
  await page.waitForTimeout(900);
  step('search none', await page.locator('section[aria-label="Files in folder"]').innerText());
  step('console', bag);
  fs.writeFileSync('out_journey_files.json', JSON.stringify(log, null, 1));
  await b.close();
})().catch((e) => { console.error('FAILED', e); process.exit(1); });
