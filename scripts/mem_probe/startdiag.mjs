#!/usr/bin/env node
// Startup diagnostic: reproduce probe.mjs's "set saved conversation id, reload"
// and record which startup branches run and what the tab ends up showing.
//
// Usage: node scripts/mem_probe/startdiag.mjs <port> <conversationId> [waitMs]
import { createRequire } from 'node:module';
import path from 'node:path';
const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
const puppeteer = require(path.join(ROOT, 'tests/mermaid_validator/node_modules/puppeteer-core'));
const [port = '1999', conv = '', wait = '15000'] = process.argv.slice(2);
const KEEP = /FORCE LOAD|ORPHAN|Lazy-loaded|Setting conversation shells|PROJECT_SWITCH|SWITCH\[|ProjectContext:|PRELOAD|hydrat|Hydrat|Startup GC|server fallback|INIT|POST_SYNC|RESTORED|Saved conversation ID|ACTIVE_CONV/;
const browser = await puppeteer.launch({
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: true, args: ['--no-sandbox'], defaultViewport: { width: 1600, height: 1100 },
});
const page = await browser.newPage();
let phase = 'first-load';
const t0 = Date.now();
page.on('console', m => {
    const t = m.text();
    if (KEEP.test(t)) console.log(`[${phase} +${((Date.now() - t0) / 1000).toFixed(1)}s] ${t.slice(0, 260)}`);
});
page.on('pageerror', e => console.log('PAGEERROR', e.message.slice(0, 300)));
const state = () => page.evaluate(() => ({
    sessConv: sessionStorage.getItem('ZIYA_CURRENT_CONVERSATION_ID'),
    lastProjectKeys: Object.fromEntries(Object.keys(localStorage).filter(k => /LAST_PROJECT|PROJECT_CONV/i.test(k)).map(k => [k, localStorage.getItem(k).slice(0, 80)])),
    msgNodes: document.querySelectorAll('.message-content').length,
    emptyBanner: /No messages in this conversation yet/.test(document.body.innerText),
}));
await page.goto(`http://localhost:${port}/`, { waitUntil: 'networkidle2', timeout: 90_000 });
await new Promise(r => setTimeout(r, 12_000));
console.log('STATE after first load', JSON.stringify(await state()));
if (conv) {
    await page.evaluate((id) => sessionStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', id), conv);
    phase = 'reload';
    await page.reload({ waitUntil: 'networkidle2', timeout: 90_000 });
    // Sample every 5 s so the moment the view fills in (if it does) is visible.
    for (let waited = 0; waited < Number(wait); waited += 5000) {
        await new Promise(r => setTimeout(r, 5000));
        const s = await state();
        console.log(`STATE reload +${(waited + 5000) / 1000}s msgNodes=${s.msgNodes} empty=${s.emptyBanner}`);
    }
    console.log('STATE after reload', JSON.stringify(await state()));
}
await browser.close();
