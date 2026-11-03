#!/usr/bin/env node
// Attribute a Ziya tab's idle memory floor.
//
// Loads a conversation (same path as probe.mjs, including the sidebar
// fallback), then removes pieces of the page one step at a time and measures
// each renderer process's RSS after a forced GC plus a critical
// memory-pressure purge, so allocators hand freed pages back to the OS:
//
//   0 loaded            everything as rendered
//   1 no-iframes        every <iframe> removed
//   2 hidden-offscreen  all but the last KEEP messages display:none
//   3 removed-offscreen those messages removed from the DOM
//
// The page is unusable afterwards; this is measurement only.
//
// Usage: node scripts/mem_probe/baseline.mjs --port 1999 --conv <id> --title "<sidebar text>" [--keep 3]

import { createRequire } from 'node:module';
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
const puppeteer = require(path.join(ROOT, 'tests/mermaid_validator/node_modules/puppeteer-core'));

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith('--')) acc.push([a.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : 'true']);
    return acc;
}, []));
const PORT = args.port || '1999';
const CONV = args.conv;
const KEEP = Number(args.keep || 3);
const OUT = path.resolve(ROOT, args.out || '.ziya/mem-probe');
const CHROME = args.chrome || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
if (!CONV) { console.error('--conv required'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });
const run = new Date().toISOString().replace(/[:.]/g, '-');
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const sleep = ms => new Promise(r => setTimeout(r, ms));

const browser = await puppeteer.launch({
    executablePath: CHROME, headless: true,
    args: ['--no-sandbox', '--js-flags=--expose-gc', '--window-size=1600,1200', '--disable-background-timer-throttling'],
    defaultViewport: { width: 1600, height: 1100 },
});
const browserPid = browser.process()?.pid;
const page = await browser.newPage();
const cdp = await page.createCDPSession();
await cdp.send('Performance.enable');
await cdp.send('HeapProfiler.enable');

function renderers() {
    const out = execSync('ps -axo pid,ppid,rss,command', { encoding: 'utf8' });
    const rows = [];
    for (const line of out.split('\n')) {
        const m = line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/);
        if (m && Number(m[2]) === browserPid && m[4].includes('--type=renderer')) {
            rows.push({ pid: Number(m[1]), rssMB: Math.round(Number(m[3]) / 1024), extension: m[4].includes('extension') });
        }
    }
    return rows.sort((a, b) => b.rssMB - a.rssMB);
}

async function measure(label) {
    await cdp.send('HeapProfiler.collectGarbage');
    try { await cdp.send('Memory.simulatePressureNotification', { level: 'critical' }); } catch { /* older Chrome */ }
    await sleep(3000);
    await cdp.send('HeapProfiler.collectGarbage');
    await sleep(1000);
    const { metrics } = await cdp.send('Performance.getMetrics');
    const m = Object.fromEntries(metrics.map(x => [x.name, x.value]));
    const procs = renderers();
    const row = {
        label,
        heapUsedMB: +(m.JSHeapUsedSize / 1048576).toFixed(1),
        nodes: m.Nodes, documents: m.Documents, frames: m.Frames, listeners: m.JSEventListeners,
        rendererTotalMB: procs.reduce((s, p) => s + p.rssMB, 0),
        processes: procs,
    };
    log(`[${label}] renderers ${row.rendererTotalMB} MB in ${procs.length} proc(s) [${procs.map(p => p.rssMB).join(', ')}]`
        + ` heap=${row.heapUsedMB}MB nodes=${row.nodes} documents=${row.documents} frames=${row.frames}`);
    return row;
}

// ── load (mirrors probe.mjs) ───────────────────────────────────────────
await page.goto(`http://localhost:${PORT}/`, { waitUntil: 'networkidle2', timeout: 90_000 });
await sleep(12_000);
await page.evaluate(id => sessionStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', id), CONV);
await page.reload({ waitUntil: 'networkidle2', timeout: 90_000 });
await sleep(12_000);
let msgCount = await page.evaluate(() => document.querySelectorAll('.message-content').length);
if (msgCount === 0 && args.title) {
    const tab = await page.evaluateHandle(() => [...document.querySelectorAll('button, [role="tab"], div, span')]
        .find(e => e.children.length === 0 && e.textContent.trim() === 'Chats'));
    if (tab && tab.asElement()) { await tab.asElement().click(); await sleep(3000); }
    await page.evaluate(needle => {
        const all = [...document.querySelectorAll('div, span, p')].filter(e => e.textContent.includes(needle));
        all.sort((a, b) => b.textContent.length - a.textContent.length).pop()?.click();
    }, args.title);
    await sleep(10_000);
    msgCount = await page.evaluate(() => document.querySelectorAll('.message-content').length);
}
log(`messages rendered: ${msgCount}`);
// Scroll through once so every lazily-mounted message mounts, as it would
// for a user who has read the conversation.
await page.evaluate(async () => {
    const sc = document.querySelector('.conversation-messages-container')?.closest('[style*="overflow"], .chat-container') || document.scrollingElement;
    const els = [...document.querySelectorAll('[data-message-index]')];
    for (const el of els) { el.scrollIntoView({ block: 'center' }); await new Promise(r => setTimeout(r, 60)); }
    els.at(-1)?.scrollIntoView({ block: 'end' });
});
await sleep(8000);

// ── inventory ─────────────────────────────────────────────────────────
const inventory = await page.evaluate(() => {
    const msgs = [...document.querySelectorAll('[data-message-index]')];
    const perMsg = msgs.map(el => {
        const n = el.getElementsByTagName('*').length + 1;
        const kinds = {
            diffLines: el.querySelectorAll('.diff-line').length,
            tokenSpans: el.querySelectorAll('span.token').length,
            iframes: el.querySelectorAll('iframe').length,
            svgNodes: [...el.querySelectorAll('svg')].reduce((s, x) => s + x.getElementsByTagName('*').length, 0),
            toolBlocks: el.querySelectorAll('pre').length,
            textChars: (el.textContent || '').length,
        };
        return { idx: el.getAttribute('data-message-index'), nodes: n, ...kinds };
    });
    const frames = [...document.querySelectorAll('iframe')].map(f => {
        const host = f.closest('[data-message-index]');
        const owner = f.parentElement?.closest('[class]');
        const r = f.getBoundingClientRect();
        return {
            msg: host ? host.getAttribute('data-message-index') : null,
            sandbox: f.getAttribute('sandbox'),
            src: (f.getAttribute('src') || '').slice(0, 80),
            srcdocChars: (f.getAttribute('srcdoc') || '').length,
            w: Math.round(r.width), h: Math.round(r.height),
            ownerClass: owner ? String(owner.className).slice(0, 80) : '',
            title: f.getAttribute('title') || '',
        };
    });
    return {
        totalNodes: document.getElementsByTagName('*').length,
        inMessages: perMsg.reduce((s, m) => s + m.nodes, 0),
        messageCount: msgs.length,
        sums: Object.fromEntries(['diffLines', 'tokenSpans', 'iframes', 'svgNodes', 'toolBlocks', 'textChars']
            .map(k => [k, perMsg.reduce((s, m) => s + m[k], 0)])),
        topMessages: [...perMsg].sort((a, b) => b.nodes - a.nodes).slice(0, 12),
        frames,
    };
});
log(`DOM: ${inventory.totalNodes} elements, ${inventory.inMessages} inside ${inventory.messageCount} messages`);
log('sums:', JSON.stringify(inventory.sums));
log(`iframes: ${inventory.frames.length}`);
const byOwner = {};
for (const f of inventory.frames) {
    const k = `${f.ownerClass.split(' ')[0] || '?'} sandbox=${f.sandbox} src=${f.src ? 'yes' : 'no'}`;
    byOwner[k] = (byOwner[k] || 0) + 1;
}
for (const [k, v] of Object.entries(byOwner)) log(`   ${v} × ${k}`);

// ── steps ─────────────────────────────────────────────────────────────
// Chrome's memory-infra dump: per-process sizes by allocator (malloc,
// partition_alloc, v8, blink_gc, cc, skia, gpu, font_caches, ...).  This is
// what attributes RSS that is neither JS heap nor DOM count.
async function memDump(label, { gc = true } = {}) {
    if (gc) await cdp.send('HeapProfiler.collectGarbage');
    const done = new Promise(res => cdp.once('Tracing.tracingComplete', res));
    await cdp.send('Tracing.start', {
        transferMode: 'ReturnAsStream',
        traceConfig: {
            includedCategories: ['disabled-by-default-memory-infra'],
            excludedCategories: ['*'],
            memoryDumpConfig: { triggers: [] },
        },
    });
    await sleep(500);
    const r = await cdp.send('Tracing.requestMemoryDump', { levelOfDetail: 'detailed' });
    await cdp.send('Tracing.end');
    const { stream } = await done;
    let text = '';
    for (;;) {
        const chunk = await cdp.send('IO.read', { handle: stream, size: 1 << 20 });
        text += chunk.base64Encoded ? Buffer.from(chunk.data, 'base64').toString('utf8') : chunk.data;
        if (chunk.eof) break;
    }
    await cdp.send('IO.close', { handle: stream });
    const parsed = JSON.parse(text);
    const events = Array.isArray(parsed) ? parsed : parsed.traceEvents;
    const rendererPids = new Set(renderers().map(p => p.pid));
    const perPid = {};
    for (const e of events) {
        if (e.ph !== 'v' || !e.args?.dumps?.allocators) continue;
        if (!rendererPids.has(e.pid)) continue;
        const al = e.args.dumps.allocators;
        const top = {};
        for (const [name, v] of Object.entries(al)) {
            const sz = v.attrs?.size?.value;
            if (!sz) continue;
            const depth = name.split('/').length;
            const bytes = parseInt(sz, 16);
            // top-level allocators plus a few informative children
            if (depth === 1 || /^(partition_alloc\/partitions\/[^/]+|v8\/main\/heap\/[^/]+|blink_gc\/main\/heap\/[^/]+|malloc\/partitions\/[^/]+|cc\/[^/]+|skia\/[^/]+)$/.test(name)) {
                top[name] = Math.round(bytes / 1048576);
            }
        }
        perPid[e.pid] = top;
    }
    const pids = Object.keys(perPid);
    log(`[${label}] memory-infra: ${pids.length} renderer dump(s), success=${r.success}`);
    for (const pid of pids) {
        const top = perPid[pid];
        const roots = Object.entries(top).filter(([k]) => !k.includes('/')).sort((a, b) => b[1] - a[1]);
        log(`   pid ${pid}: ` + roots.map(([k, v]) => `${k}=${v}`).join(' '));
        const kids = Object.entries(top).filter(([k, v]) => k.includes('/') && v >= 5).sort((a, b) => b[1] - a[1]).slice(0, 12);
        for (const [k, v] of kids) log(`        ${String(v).padStart(5)} MB  ${k}`);
    }
    return perPid;
}

const steps = [];
const dumps = {};

// --stream "<prompt>": attribute the RSS that grows while a turn streams.
// Dumps before, every --every seconds during (no GC, so garbage stays
// visible), and after (with GC + pressure purge).  Exits after.
if (args.stream && args.stream !== 'true') {
    const every = Number(args.every || 45) * 1000;
    steps.push(await measure('pre-stream'));
    dumps.pre = await memDump('pre-stream');
    const input = await page.$('[contenteditable="true"][data-placeholder]');
    if (!input) throw new Error('chat input not found');
    await input.focus();
    await page.keyboard.type(args.stream);
    await page.keyboard.press('Enter');
    const t0 = Date.now();
    let started = false, lastDump = Date.now(), n = 0, peak = 0;
    while (Date.now() - t0 < Number(args['stream-max'] || 400) * 1000) {
        await sleep(1000);
        const tot = renderers().reduce((s, p) => s + p.rssMB, 0);
        peak = Math.max(peak, tot);
        const streaming = await page.evaluate(() => {
            const el = document.querySelector('[contenteditable="true"][data-placeholder]');
            return !!el && /feedback for running tools/i.test(el.getAttribute('data-placeholder') || '');
        });
        if (streaming) started = true; else if (started && Date.now() - t0 > 5000) break;
        if (started && Date.now() - lastDump > every) {
            n++; lastDump = Date.now();
            log(`mid-stream ${n}: renderers ${tot} MB`);
            dumps[`mid${n}`] = await memDump(`mid-stream ${n} (no GC)`, { gc: false });
        }
    }
    log(`stream done in ${Math.round((Date.now() - t0) / 1000)} s, peak renderers ${peak} MB`);
    dumps.endNoGc = await memDump('end (no GC)', { gc: false });
    steps.push(await measure('post-stream'));
    dumps.post = await memDump('post-stream');
    const file = path.join(OUT, `${run}.baseline-stream.json`);
    fs.writeFileSync(file, JSON.stringify({ conv: CONV, prompt: args.stream, steps, dumps }, null, 2));
    log('→', file);
    await browser.close();
    process.exit(0);
}
steps.push(await measure('0 loaded'));
dumps.loaded = await memDump('0 loaded');

await page.evaluate(() => document.querySelectorAll('iframe').forEach(f => f.remove()));
steps.push(await measure('1 no-iframes'));

await page.evaluate(keep => {
    const msgs = [...document.querySelectorAll('[data-message-index]')];
    msgs.slice(0, Math.max(0, msgs.length - keep)).forEach(el => { el.style.display = 'none'; });
}, KEEP);
steps.push(await measure('2 hidden-offscreen'));

await page.evaluate(keep => {
    const msgs = [...document.querySelectorAll('[data-message-index]')];
    msgs.slice(0, Math.max(0, msgs.length - keep)).forEach(el => el.remove());
}, KEEP);
steps.push(await measure('3 removed-offscreen'));

// Clean comparison: same tab, reloaded onto an id that does not exist, so
// the app shell renders with no conversation mounted.  loaded − this is
// what the conversation itself costs.
await page.evaluate(() => sessionStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', 'no-such-conversation'));
await page.reload({ waitUntil: 'networkidle2', timeout: 90_000 });
await sleep(15_000);
log('empty-shell messages rendered:', await page.evaluate(() => document.querySelectorAll('.message-content').length));
steps.push(await measure('4 empty-shell'));
dumps.emptyShell = await memDump('4 empty-shell');

const result = { port: PORT, conv: CONV, keep: KEEP, inventory, steps, dumps };
const file = path.join(OUT, `${run}.baseline.json`);
fs.writeFileSync(file, JSON.stringify(result, null, 2));
log('→', file);
for (let i = 1; i < steps.length; i++) {
    log(`Δ ${steps[i].label}: renderers ${steps[i].rendererTotalMB - steps[i - 1].rendererTotalMB} MB, nodes ${steps[i].nodes - steps[i - 1].nodes}, heap ${(steps[i].heapUsedMB - steps[i - 1].heapUsedMB).toFixed(1)} MB`);
}
await browser.close();
