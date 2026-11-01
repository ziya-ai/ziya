#!/usr/bin/env node
// End-to-end check: panels opened from the Bench paint IN FRONT of it.
//
// Opens the Bench on a running Ziya, clicks "Find more" (and, separately,
// "MCP servers"), and asks the browser what is painted at the centre of the
// screen.  Exits 1 if the Bench is still on top.
//
// Usage: node scripts/ui_probe/bench_registry_front.mjs [port]
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
const puppeteer = require(path.join(ROOT, 'tests/mermaid_validator/node_modules/puppeteer-core'));
const port = process.argv[2] || '6969';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ['--no-sandbox'],
    defaultViewport: { width: 1600, height: 1100 } });
const topTitle = (page) => page.evaluate(() => {
    const el = document.elementFromPoint(800, 400);
    return el?.closest('.ant-modal-wrap')?.querySelector('.ant-modal-title')?.textContent?.trim() ?? null;
});

let failed = false;
for (const [button, expect] of [['bench-find-more', /MCP Registry/], ['bench-mcp-servers', /MCP Server Status/]]) {
    const page = await browser.newPage();
    await page.goto(`http://localhost:${port}/`, { waitUntil: 'networkidle2', timeout: 90_000 });
    await page.waitForSelector('[data-testid="bench-button"]', { timeout: 30_000 });
    await page.click('[data-testid="bench-button"]');
    await page.waitForSelector(`[data-testid="${button}"]`, { timeout: 30_000 });
    const before = await topTitle(page);
    await page.click(`[data-testid="${button}"]`);
    await new Promise(r => setTimeout(r, 3000));
    const after = await topTitle(page);
    const ok = /BENCH/.test(before || '') && expect.test(after || '');
    console.log(`${ok ? 'PASS' : 'FAIL'} ${button}: on top before=${JSON.stringify(before)} after=${JSON.stringify(after)}`);
    failed ||= !ok;
    await page.close();
}
await browser.close();
process.exit(failed ? 1 : 0);
