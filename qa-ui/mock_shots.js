const { chromium } = require('playwright');
const fs = require('fs');
const D = '/tmp/claude-0/-home-claude/fe340d4b-e7f9-5494-b610-80daac9e8acc/scratchpad/ab/project/';
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 1040 }, deviceScaleFactor: 1 });
  for (const f of fs.readdirSync(D).filter((f) => f.endsWith('.html'))) {
    await p.goto('file://' + D + f).catch(() => {}); await p.waitForTimeout(500);
    await p.screenshot({ path: 'shots/mock_' + f.replace('.dc.html', '') + '.png', fullPage: true });
  }
  await b.close();
})();
