// Feature 1: full-page viewer at /view?path=... Exercises each file kind, hostile Markdown, Preview/Source,
// versions rail, Esc, Open in Claude Code, Rename/Move/Delete, odd paths, and a viewer-only person.
// Run: env -u HTTPS_PROXY -u HTTP_PROXY BASE=http://localhost:8096 node viewer.js
const { chromium } = require('playwright');
const fs = require('fs');
const BASE = process.env.BASE || 'http://localhost:8096';
const OUT = {};
const log = (k, v) => { OUT[k] = v; console.log(k, '=>', typeof v === 'string' ? v : JSON.stringify(v)); };
const D = '/users/maya/inbox/viewer';
const V = (p) => BASE + '/view?path=' + encodeURIComponent(p);
async function login(page, user, pw = 'demo-password-123') {
  await page.goto(BASE + '/login'); await page.getByLabel('Username').fill(user); await page.getByLabel('Password').fill(pw);
  await page.getByRole('button', { name: 'Sign in' }).click(); await page.waitForURL((u) => !u.pathname.startsWith('/login'));
}
async function apiIn(page, method, url, body) {
  return page.evaluate(async ([method, url, body]) => {
    const me = await (await fetch('/api/auth/me')).json();
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf || '' }, body: body ? JSON.stringify(body) : undefined });
    let t = await r.text(); try { t = JSON.parse(t); } catch {} return { status: r.status, body: t };
  }, [method, url, body]);
}
async function step(name, fn) { try { await fn(); } catch (e) { log(name + ' ERROR', String(e).slice(0, 300)); } }

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(`[pageerror] ${page.url()} ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`[console] ${page.url()} ${m.text().slice(0, 160)}`); });
  page.on('dialog', (d) => { errors.push('[dialog] ' + d.message()); d.dismiss(); });
  await login(page, 'maya');
  await page.evaluate(() => { window.__xss = null; });

  // ---- each kind ----
  const files = ['hostile.md', 'data.csv', 'data.json', 'script.py', 'page.html', 'pic.svg', 'pic.png', 'doc.pdf', 'big.csv', 'huge.txt', 'blob.bin', 'noext', 'bad.json', 'empty.md'];
  for (const f of files) {
    await step('kind ' + f, async () => {
      await page.goto(V(`${D}/${f}`)); await page.waitForTimeout(1500);
      const r = await page.evaluate(() => {
        const st = document.querySelector('section.stage');
        if (!st) return { stage: null, text: document.querySelector('main')?.innerText.slice(0, 120) };
        const ifr = st.querySelector('iframe');
        return {
          article: !!st.querySelector('article.md'), csvRows: st.querySelectorAll('table.csvt tbody tr').length,
          codeLines: st.querySelectorAll('.codeview tr').length, img: !!st.querySelector('img'),
          iframe: ifr ? { sandbox: ifr.getAttribute('sandbox'), src: ifr.getAttribute('src').slice(0, 30) } : null,
          placeholder: st.querySelector('.ph')?.innerText.replace(/\n+/g, ' | ') || null,
          toggles: [...document.querySelectorAll('.segs button')].map((x) => x.innerText),
          firstText: st.innerText.slice(0, 80).replace(/\n+/g, ' | '),
        };
      });
      log('kind ' + f, r);
      await page.screenshot({ path: `shots/view_${f.replace(/\W/g, '_')}.png` });
    });
  }

  // ---- hostile markdown deep check ----
  await step('hostile md', async () => {
    await page.goto(V(`${D}/hostile.md`)); await page.waitForTimeout(1500);
    const r = await page.evaluate(() => {
      const a = document.querySelector('article.md');
      const all = [...a.querySelectorAll('*')];
      return {
        xss: window.__xss,
        scripts: a.querySelectorAll('script,iframe,img,svg,object,embed').length,
        onAttrs: all.filter((e) => [...e.attributes].some((x) => /^on/i.test(x.name))).map((e) => e.outerHTML.slice(0, 80)),
        hrefs: [...a.querySelectorAll('a')].map((x) => x.getAttribute('href')),
        tags: [...new Set(all.map((e) => e.tagName))].join(','),
        blockquotes: a.querySelectorAll('blockquote blockquote').length,
        quoteText: a.querySelector('blockquote')?.innerText.slice(0, 200),
        tableCells: [...a.querySelectorAll('td')].map((t) => t.innerHTML.slice(0, 80)),
        emInsideAttr: [...a.querySelectorAll('a')].map((x) => x.getAttribute('href')).filter((h) => /<|em>/.test(h)),
        para: [...a.querySelectorAll('p')].map((p) => p.innerText).filter((t) => /Literal|star in url|attr break/.test(t)),
      };
    });
    log('hostile md', r);
    // click every link and see if anything ran
    for (const h of await page.locator('article.md a').all()) await h.click({ modifiers: [] }).catch(() => {});
    await page.waitForTimeout(500);
    log('hostile md after clicking links: __xss', await page.evaluate(() => window.__xss));
    await page.screenshot({ path: 'shots/view_hostile_full.png', fullPage: true });
    // the detail-panel mini preview on the Files page
    await page.goto(BASE + `/files?path=${encodeURIComponent(D)}&file=${encodeURIComponent(D + '/hostile.md')}`); await page.waitForTimeout(1200);
    log('hostile md in Files side panel: __xss / dangerous nodes', await page.evaluate(() => [window.__xss, document.querySelectorAll('aside .mdmini script, aside .mdmini img, aside .mdmini iframe').length]));
  });

  // ---- Source toggle ----
  await step('toggle', async () => {
    for (const f of ['hostile.md', 'data.csv', 'data.json', 'page.html']) {
      await page.goto(V(`${D}/${f}`)); await page.waitForTimeout(1200);
      await page.getByRole('button', { name: 'Source' }).click(); await page.waitForTimeout(400);
      const src = await page.evaluate(() => ({ lines: document.querySelectorAll('.codeview tr').length, first: document.querySelector('.codeview .lc')?.textContent.slice(0, 60) }));
      const json = f === 'data.json' ? await page.locator('.codeview').innerText() : null;
      await page.getByRole('button', { name: /Preview|Formatted/ }).click(); await page.waitForTimeout(300);
      log('toggle ' + f, { src, back: await page.evaluate(() => !!document.querySelector('article.md, table.csvt, iframe') || document.querySelectorAll('.codeview tr').length) });
      if (json) log('json source contains big int verbatim', json.includes('12345678901234567890'));
    }
    await page.goto(V(`${D}/data.json`)); await page.waitForTimeout(1200);
    const fmt = await page.locator('.codeview').innerText();
    log('json formatted view big int', { exact: fmt.includes('12345678901234567890'), shown: (fmt.match(/"big": [^\n,]+/) || [''])[0], float: (fmt.match(/"f": [^\n,]+/) || [''])[0] });
    // does mode persist when navigating to another file?
    await page.getByRole('button', { name: 'Source' }).click();
    await page.goto(V(`${D}/hostile.md`)); await page.waitForTimeout(1000);
    log('mode after navigating to another file', await page.evaluate(() => document.querySelector('.segs .on')?.innerText));
  });

  // ---- HTML / SVG sandbox ----
  await step('sandbox', async () => {
    await page.goto(V(`${D}/page.html`)); await page.waitForTimeout(2000);
    const fr = page.frames().find((f) => f.url().includes('/preview/'));
    log('html iframe content', { title: await page.title(), inner: fr ? await fr.evaluate(() => document.body.innerText).catch((e) => 'eval err ' + e) : 'no frame' });
    await page.goto(V(`${D}/pic.svg`)); await page.waitForTimeout(1500);
    log('svg', { title: await page.title() });
  });

  // ---- PDF ----
  await step('pdf', async () => {
    await page.goto(V(`${D}/doc.pdf`)); await page.waitForTimeout(1500);
    const src = await page.locator('section.stage iframe').getAttribute('src');
    const r = await page.request.get(BASE + src);
    log('pdf preview response', { status: r.status(), type: r.headers()['content-type'], csp: (r.headers()['content-security-policy'] || '').slice(0, 30), iframeSandboxAttr: await page.locator('section.stage iframe').getAttribute('sandbox') });
  });

  // ---- versions rail ----
  await step('versions', async () => {
    await page.goto(V(`${D}/script.py`)); await page.waitForTimeout(1500);
    const rail = await page.locator('aside.rail').innerText();
    log('versions rail', rail.slice(rail.indexOf('History'), rail.indexOf('History') + 200).replace(/\n+/g, ' | '));
    await page.getByRole('button', { name: 'All versions' }).click(); await page.waitForTimeout(800);
    await page.locator('[role=dialog]').getByRole('button', { name: /Restore/ }).last().click(); await page.waitForTimeout(1500);
    await page.keyboard.press('Escape'); await page.waitForTimeout(500);
    log('after restoring oldest from viewer', { url: page.url(), ver: await page.locator('.vhead .tag').first().innerText(), code: (await page.locator('.codeview').innerText()).slice(0, 60) });
  });

  // ---- Esc ----
  await step('esc', async () => {
    await page.goto(V(`${D}/data.csv`)); await page.waitForTimeout(1200);
    await page.keyboard.press('Escape'); await page.waitForTimeout(1000);
    log('Esc from viewer ->', { url: decodeURIComponent(page.url()), selected: await page.locator('aside[aria-label="File details"] .dname').innerText().catch(() => 'no panel') });
    await page.goto(V(`${D}/data.csv`)); await page.waitForTimeout(1200);
    await page.getByRole('button', { name: /Who has access|Share/ }).click(); await page.waitForTimeout(500);
    await page.keyboard.press('Escape'); await page.waitForTimeout(600);
    log('Esc with share dialog open -> stays on viewer', page.url().includes('/view'));
    await page.getByRole('button', { name: 'Rename' }).click(); await page.waitForTimeout(300);
    await page.keyboard.press('Escape'); await page.waitForTimeout(600);
    log('Esc in rename dialog input -> dialog closed, still on viewer', { url: page.url().includes('/view'), dialog: await page.locator('[role=dialog]').count() });
    // focus inside the HTML iframe
    await page.goto(V(`${D}/page.html`)); await page.waitForTimeout(1500);
    await page.locator('section.stage iframe').click(); await page.keyboard.press('Escape'); await page.waitForTimeout(600);
    log('Esc with focus inside the HTML preview iframe', page.url().includes('/view') ? 'stays on viewer' : 'returned');
    // browser back after Esc
    await page.goto(V(`${D}/data.csv`)); await page.waitForTimeout(1000);
    await page.keyboard.press('Escape'); await page.waitForTimeout(800); await page.goBack(); await page.waitForTimeout(800);
    log('Back after Esc', decodeURIComponent(page.url()));
  });

  // ---- Open in Claude Code ----
  await step('claude', async () => {
    await page.goto(V(`${D}/hostile.md`)); await page.waitForTimeout(1200);
    await page.getByRole('button', { name: 'Open in Claude Code' }).click(); await page.waitForTimeout(500);
    log('clipboard after Open in Claude Code', await page.evaluate(() => navigator.clipboard.readText()));
    log('toast', await page.locator('.toast').last().innerText().catch(() => ''));
    // a path with spaces / quotes
    await apiIn(page, 'POST', '/api/fs/save-text', { path: `${D}/my report (final) 'v2'.md`, content: '# x' });
    await page.goto(V(`${D}/my report (final) 'v2'.md`)); await page.waitForTimeout(1200);
    await page.getByRole('button', { name: 'Open in Claude Code' }).click(); await page.waitForTimeout(500);
    log('clipboard for a path with spaces', await page.evaluate(() => navigator.clipboard.readText()));
  });

  // ---- Rename / Move / Delete from the viewer ----
  await step('rename/move/delete', async () => {
    await apiIn(page, 'POST', '/api/fs/save-text', { path: `${D}/ops.md`, content: '# ops' });
    await page.goto(V(`${D}/ops.md`)); await page.waitForTimeout(1200);
    await page.getByRole('button', { name: 'Rename' }).click();
    await page.locator('[role=dialog] input').fill('ops-renamed.md'); await page.locator('[role=dialog] input').press('Enter'); await page.waitForTimeout(1200);
    log('viewer rename ->', { url: decodeURIComponent(page.url()), h1: await page.locator('h1').first().innerText() });
    await page.getByRole('button', { name: 'Move' }).click();
    await page.locator('[role=dialog] input').fill('/users/maya/projects'); await page.locator('[role=dialog] input').press('Enter'); await page.waitForTimeout(1200);
    log('viewer move ->', { url: decodeURIComponent(page.url()), h1: await page.locator('h1').first().innerText() });
    // rename to an existing name
    await page.getByRole('button', { name: 'Rename' }).click();
    await page.locator('[role=dialog] input').fill('../escape.md'); await page.locator('[role=dialog] input').press('Enter'); await page.waitForTimeout(1000);
    log('viewer rename to ../escape.md', { toast: await page.locator('.toast').last().innerText().catch(() => ''), url: decodeURIComponent(page.url()) });
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Delete' }).click(); await page.waitForTimeout(300);
    log('viewer delete dialog', await page.locator('[role=dialog]').innerText());
    await page.getByRole('button', { name: 'Move to trash' }).click(); await page.waitForTimeout(1200);
    log('viewer delete ->', { url: decodeURIComponent(page.url()), toast: await page.locator('.toast').last().innerText().catch(() => '') });
    // back button after delete
    await page.goBack(); await page.waitForTimeout(1000);
    log('Back after delete lands on', { url: decodeURIComponent(page.url()), text: (await page.locator('main').innerText()).slice(0, 100).replace(/\n+/g, ' | ') });
  });

  // ---- odd paths ----
  await step('odd paths', async () => {
    for (const p of ['', '/users/maya/inbox/viewer', '/users/jonas/inbox/secret.md', '/users/maya/inbox/viewer/nope.md', '/../../etc/passwd']) {
      await page.goto(p ? V(p) : BASE + '/view'); await page.waitForTimeout(1000);
      log('view ' + (p || '(no path)'), (await page.locator('main').innerText()).slice(0, 120).replace(/\n+/g, ' | '));
    }
  });

  // ---- viewer-only person ----
  await step('viewer role', async () => {
    const s = await apiIn(page, 'POST', '/api/fs/share', { path: D, kind: 'user', name: 'jonas', role: 'viewer' });
    const c2 = await b.newContext({ viewport: { width: 1440, height: 900 } }); const p2 = await c2.newPage();
    await login(p2, 'jonas');
    await p2.goto(V(`${D}/data.csv`)); await p2.waitForTimeout(1500);
    log('jonas (viewer) on viewer page', { share: s.status, buttons: await p2.locator('button').allInnerTexts(), rows: await p2.locator('table.csvt tbody tr').count() });
    await c2.close();
  });

  // ---- narrow ----
  await step('narrow', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const f of ['hostile.md', 'data.csv', 'pic.png', 'page.html']) {
      await page.goto(V(`${D}/${f}`)); await page.waitForTimeout(1200);
      log('390 ' + f, await page.evaluate(() => ({ over: document.documentElement.scrollWidth - innerWidth, stageH: Math.round(document.querySelector('section.stage')?.getBoundingClientRect().height || 0) })));
      await page.screenshot({ path: `shots/view390_${f.replace(/\W/g, '_')}.png` });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  log('errors', errors);
  fs.writeFileSync('out_viewer.json', JSON.stringify(OUT, null, 1));
  await b.close();
})();
