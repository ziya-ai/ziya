#!/usr/bin/env node
// Headless memory probe for a running Ziya frontend.
//
// Drives the system Chrome against a live Ziya server, opens a conversation,
// and records heap/DOM counters plus a V8 sampling allocation profile during
// (a) an idle window and (b) optionally a real streamed turn.  Frames in the
// allocation profile are resolved through the served source maps so the
// output names real source files, not minified bundle offsets.
//
// Usage:
//   node scripts/mem_probe/probe.mjs --port 1989 --conv <id> [--idle 100]
//        [--stream "prompt text"] [--out .ziya/mem-probe]
//
// Output (under --out):
//   <run>.samples.jsonl   one line per second: heap/DOM counters
//   <run>.alloc.<phase>.json   top allocation sites by self size, source-mapped
//   <run>.summary.json    phase floors/peaks and DOM counters after a forced GC

import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..');
const puppeteer = require(path.join(ROOT, 'tests/mermaid_validator/node_modules/puppeteer-core'));
const { SourceMapConsumer } = require(path.join(ROOT, 'frontend/node_modules/source-map'));

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith('--')) acc.push([a.slice(2), arr[i + 1] && !arr[i + 1].startsWith('--') ? arr[i + 1] : 'true']);
    return acc;
}, []));
const PORT = args.port || '1989';
const CONV = args.conv;
const IDLE_S = Number(args.idle || 100);
const STREAM_PROMPT = args.stream && args.stream !== 'true' ? args.stream : null;
const STREAM_MAX_S = Number(args['stream-max'] || 240);
const OUT = path.resolve(ROOT, args.out || '.ziya/mem-probe');
const CHROME = args.chrome || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
if (!CONV) { console.error('--conv <conversationId> is required'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });
const run = new Date().toISOString().replace(/[:.]/g, '-');
const samplesPath = path.join(OUT, `${run}.samples.jsonl`);
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

// ── source-map resolution ───────────────────────────────────────────────
const mapCache = new Map();
async function consumerFor(url) {
    if (!url || !url.startsWith('http')) return null;
    if (mapCache.has(url)) return mapCache.get(url);
    let c = null;
    try {
        const r = await fetch(url + '.map');
        if (r.ok) c = new SourceMapConsumer(await r.json());
    } catch { /* no map */ }
    mapCache.set(url, c);
    return c;
}
async function resolveFrame(cf) {
    const c = await consumerFor(cf.url);
    if (!c) return `${cf.functionName || '(anon)'} ${cf.url ? path.basename(cf.url) : ''}:${cf.lineNumber + 1}`;
    const pos = c.originalPositionFor({ line: cf.lineNumber + 1, column: cf.columnNumber });
    if (!pos.source) return `${cf.functionName || '(anon)'} ${path.basename(cf.url)}:${cf.lineNumber + 1}`;
    const src = pos.source.replace(/^webpack:\/\/[^/]*\//, '').replace(/^\.\//, '');
    return `${pos.name || cf.functionName || '(anon)'} ${src}:${pos.line}`;
}

// Aggregate a sampling heap profile: self size per leaf frame, and inclusive
// size per "nearest application frame" (first frame walking up the stack
// whose resolved source is under src/ rather than node_modules).
async function summarizeProfile(profile, label) {
    const selfBy = new Map();
    const appBy = new Map();
    let total = 0;
    const walk = async (node, stack) => {
        const frame = await resolveFrame(node.callFrame);
        const s = [...stack, frame];
        if (node.selfSize > 0) {
            total += node.selfSize;
            selfBy.set(frame, (selfBy.get(frame) || 0) + node.selfSize);
            // Served maps name app sources relative to src/ ("components/X.tsx:12").
            const app = s.slice().reverse().find(f => /\.(tsx?|jsx?):\d+$/.test(f) && !/node_modules/.test(f)) || '(library/runtime only)';
            appBy.set(app, (appBy.get(app) || 0) + node.selfSize);
        }
        for (const ch of node.children || []) await walk(ch, s);
    };
    await walk(profile.head, []);
    const top = (m, n) => [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n)
        .map(([k, v]) => ({ site: k, mb: +(v / 1048576).toFixed(1), pct: +(100 * v / total).toFixed(1) }));
    const out = { label, sampledTotalMB: +(total / 1048576).toFixed(1), topSelf: top(selfBy, 40), topAppFrame: top(appBy, 40) };
    fs.writeFileSync(path.join(OUT, `${run}.alloc.${label}.json`), JSON.stringify(out, null, 2));
    log(`[${label}] sampled allocations ${out.sampledTotalMB} MB; top app frames:`);
    for (const r of out.topAppFrame.slice(0, 12)) log(`   ${String(r.pct).padStart(5)}%  ${String(r.mb).padStart(7)} MB  ${r.site}`);
    return out;
}

// Aggregate a CPU profile (Profiler domain): self and inclusive milliseconds
// per resolved function.  A frame's key is its DEFINITION site, so an
// anonymous useMemo callback is identified by file:line of the arrow.
// Inclusive time counts a frame once per stack (recursion is not doubled).
async function summarizeCpu(profile, label) {
    const byId = new Map(profile.nodes.map(n => [n.id, n]));
    const nSamples = profile.samples?.length || 1;
    const usPerHit = (profile.endTime - profile.startTime) / nSamples;
    const selfBy = new Map();
    const inclBy = new Map();
    let totalMs = 0;
    const visit = async (id, stack) => {
        const node = byId.get(id);
        const frame = await resolveFrame(node.callFrame);
        const s = [...stack, frame];
        const ms = (node.hitCount || 0) * usPerHit / 1000;
        if (ms > 0) {
            totalMs += ms;
            selfBy.set(frame, (selfBy.get(frame) || 0) + ms);
            for (const f of new Set(s)) inclBy.set(f, (inclBy.get(f) || 0) + ms);
        }
        for (const ch of node.children || []) await visit(ch, s);
    };
    await visit(profile.nodes[0].id, []);
    const top = (m, n, filt = () => true) => [...m.entries()].filter(([k]) => filt(k))
        .sort((a, b) => b[1] - a[1]).slice(0, n)
        .map(([k, v]) => ({ site: k, ms: Math.round(v), pct: +(100 * v / totalMs).toFixed(1) }));
    const out = {
        label, totalMs: Math.round(totalMs),
        topSelf: top(selfBy, 50),
        // Served maps name app sources relative to src/ ("components/X.tsx"),
        // so "app" is any .ts/.tsx/.js frame outside node_modules.
        topInclusiveApp: top(inclBy, 80, k => /\.(tsx?|jsx?):\d+$/.test(k) && !/node_modules/.test(k)),
        topInclusiveAll: top(inclBy, 60, k => k !== '(root) ' && !/^\(root\)/.test(k)),
    };
    fs.writeFileSync(path.join(OUT, `${run}.cpu.${label}.json`), JSON.stringify(out, null, 2));
    log(`[${label}] CPU ${out.totalMs} ms sampled; top inclusive app frames:`);
    for (const r of out.topInclusiveApp.slice(0, 15)) log(`   ${String(r.pct).padStart(5)}%  ${String(r.ms).padStart(7)} ms  ${r.site}`);
    return out;
}

// ── main ────────────────────────────────────────────────────────────────
const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ['--no-sandbox', '--js-flags=--expose-gc', '--window-size=1600,1200', '--disable-background-timer-throttling'],
    defaultViewport: { width: 1600, height: 1100 },
});
const page = await browser.newPage();
page.on('pageerror', e => log('PAGEERROR', e.message.slice(0, 200)));
const cdp = await page.createCDPSession();
await cdp.send('Performance.enable');
await cdp.send('HeapProfiler.enable');

const base = `http://localhost:${PORT}/`;
log('loading', base);
await page.goto(base, { waitUntil: 'networkidle2', timeout: 90_000 });
// First load on a fresh profile only pulls shells; the saved active id is a
// deferred shell and renders empty.  Wait for the sync, then reload so the
// active-conversation hydration path runs against populated IDB.
await new Promise(r => setTimeout(r, 12_000));
await page.evaluate((id) => { sessionStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', id); }, CONV);
await page.reload({ waitUntil: 'networkidle2', timeout: 90_000 });
await new Promise(r => setTimeout(r, 12_000));
const msgCount = await page.evaluate(() => document.querySelectorAll('.message-content').length);
log(`reloaded with conversation ${CONV}; rendered .message-content nodes: ${msgCount}`);
let linkCount = msgCount;
if (msgCount === 0) {
    // Open it the way a user following a ziya:// link would: ZiyaLinkNavigator
    // listens for clicks on such anchors anywhere in the document, locates the
    // owning project on the server, switches to it and loads the conversation.
    // Unlike the sidebar fallback this works across projects and needs no title.
    await page.evaluate((id) => {
        const a = document.createElement('a');
        a.href = `ziya://chat/${id}`;
        a.textContent = 'probe-link';
        document.body.appendChild(a);
        a.click();
        a.remove();
    }, CONV);
    for (let i = 0; i < 30 && linkCount === 0; i++) {
        await new Promise(r => setTimeout(r, 1000));
        linkCount = await page.evaluate(() => document.querySelectorAll('.message-content').length);
    }
    log('ziya:// link fallback; rendered .message-content nodes now:', linkCount);
    if (linkCount > 0) await new Promise(r => setTimeout(r, 8000)); // let lazy renders settle
}
if (linkCount === 0 && args.title) {
    // Fall back to the sidebar: open the Chats panel and click the row whose
    // title contains --title.  Rows carry no id attribute, so match by text.
    const tab = await page.evaluateHandle(() => [...document.querySelectorAll('button, [role="tab"], div, span')]
        .find(e => e.children.length === 0 && e.textContent.trim() === 'Chats'));
    if (tab && tab.asElement()) { await tab.asElement().click(); await new Promise(r => setTimeout(r, 3000)); }
    const clicked = await page.evaluate((needle) => {
        const all = [...document.querySelectorAll('div, span, p')].filter(e => e.textContent.includes(needle));
        const deepest = all.sort((a, b) => b.textContent.length - a.textContent.length).pop();
        if (!deepest) return null;
        deepest.click();
        return deepest.textContent.slice(0, 80);
    }, args.title);
    log('sidebar fallback clicked:', clicked);
    await new Promise(r => setTimeout(r, 10_000));
    log('rendered .message-content nodes now:', await page.evaluate(() => document.querySelectorAll('.message-content').length));
}

log('visibilityState:', await page.evaluate(() => document.visibilityState));
let phase = 'load';
const sampleStream = fs.createWriteStream(samplesPath);
async function sample() {
    const { metrics } = await cdp.send('Performance.getMetrics');
    const m = Object.fromEntries(metrics.map(x => [x.name, x.value]));
    const row = {
        t: Date.now(), phase,
        heapUsedMB: +(m.JSHeapUsedSize / 1048576).toFixed(1),
        heapTotalMB: +(m.JSHeapTotalSize / 1048576).toFixed(1),
        nodes: m.Nodes, listeners: m.JSEventListeners, documents: m.Documents,
        layouts: m.LayoutCount, styleRecalcs: m.RecalcStyleCount,
        rendererRssMB: rendererRssMB(),
    };
    sampleStream.write(JSON.stringify(row) + '\n');
    return row;
}
const samplerTimer = setInterval(() => sample().catch(() => {}), 1000);

// Renderer RSS (Blink + V8 + everything), sampled from the OS — V8 metrics
// miss DOM/layout memory, which is where a node-count spike actually lands.
import { execSync } from 'node:child_process';
const browserPid = browser.process()?.pid;
function rendererRssMB() {
    try {
        const out = execSync('ps -axo pid,ppid,rss,command', { encoding: 'utf8' });
        let total = 0;
        for (const line of out.split('\n')) {
            const m = line.match(/^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/);
            if (m && Number(m[2]) === browserPid && m[4].includes('--type=renderer')) total += Number(m[3]);
        }
        return +(total / 1024).toFixed(0);
    } catch { return null; }
}
// (rendererRssMB is recorded inside sample())

async function forcedGcFloor(label) {
    await cdp.send('HeapProfiler.collectGarbage');
    await new Promise(r => setTimeout(r, 500));
    const row = await sample();
    log(`[${label}] after forced GC: heapUsed=${row.heapUsedMB}MB nodes=${row.nodes} listeners=${row.listeners} documents=${row.documents}`);
    return row;
}

const summary = { port: PORT, conv: CONV, phases: {} };

// Phase: settle + floor
await new Promise(r => setTimeout(r, 8000));
summary.phases.afterLoad = await forcedGcFloor('afterLoad');

// Phase: idle (covers 30s sync cycles)
phase = 'idle';
await cdp.send('HeapProfiler.startSampling', { samplingInterval: 32768 });
const idleRows = [];
const idleEnd = Date.now() + IDLE_S * 1000;
while (Date.now() < idleEnd) { await new Promise(r => setTimeout(r, 1000)); idleRows.push(await sample()); }
const idleProfile = (await cdp.send('HeapProfiler.stopSampling')).profile;
summary.phases.idle = {
    seconds: IDLE_S,
    heapUsedMinMB: Math.min(...idleRows.map(r => r.heapUsedMB)),
    heapUsedMaxMB: Math.max(...idleRows.map(r => r.heapUsedMB)),
    nodesStart: idleRows[0].nodes, nodesEnd: idleRows.at(-1).nodes,
    listenersStart: idleRows[0].listeners, listenersEnd: idleRows.at(-1).listeners,
    alloc: await summarizeProfile(idleProfile, 'idle'),
};
summary.phases.afterIdle = await forcedGcFloor('afterIdle');

// Phase: stream a real turn
if (STREAM_PROMPT) {
    phase = 'stream';
    const input = await page.$('[contenteditable="true"][data-placeholder]');
    if (!input) throw new Error('chat input not found');
    await input.focus();
    await page.keyboard.type(STREAM_PROMPT);
    // --garbage: also record objects later collected, so short-lived and
    // promoted-then-dead allocations are attributed (the default records
    // only what is still alive when sampling stops).
    await cdp.send('HeapProfiler.startSampling', {
        samplingInterval: 32768,
        ...(args.garbage ? { includeObjectsCollectedByMajorGC: true, includeObjectsCollectedByMinorGC: true } : {}),
    });
    if (args.cpu) {
        await cdp.send('Profiler.enable');
        await cdp.send('Profiler.setSamplingInterval', { interval: 500 });
        await cdp.send('Profiler.start');
    }
    if (args.domtrace) {
        // 100 ms in-page trace split by subtree, so a node spike can be
        // attributed: committed messages (Conversation) vs the live bubble
        // (StreamedContent's .message.assistant outside that container).
        await page.evaluate(() => {
            const w = window;
            w.__domtrace = [];
            const count = (el) => el ? el.getElementsByTagName('*').length + 1 : 0;
            // Which committed messages get subtrees replaced, and how big.
            // Removed-node sizes are measured at callback time (detached but
            // intact), so a remount shows as matching removed/added totals.
            w.__mut = [];
            const sizeOf = (n) => n.nodeType === 1 ? n.getElementsByTagName('*').length + 1 : 0;
            const convEl = document.querySelector('.conversation-messages-container');
            if (convEl) {
                w.__mutObs = new MutationObserver((recs) => {
                    const t = Date.now();
                    const byMsg = new Map();
                    for (const r of recs) {
                        const host = r.target.nodeType === 1 ? r.target.closest('[data-message-index]') : null;
                        const k = host ? host.getAttribute('data-message-index') : '(none)';
                        const e = byMsg.get(k) || { added: 0, removed: 0, cls: r.target.className && String(r.target.className).slice(0, 60) };
                        for (const n of r.addedNodes) e.added += sizeOf(n);
                        for (const n of r.removedNodes) e.removed += sizeOf(n);
                        byMsg.set(k, e);
                    }
                    for (const [k, e] of byMsg) if (e.added + e.removed >= 50) w.__mut.push({ t, msg: k, ...e });
                });
                w.__mutObs.observe(convEl, { childList: true, subtree: true });
            }
            w.__domtraceTimer = setInterval(() => {
                const conv = document.querySelector('.conversation-messages-container');
                let live = 0;
                for (const m of document.querySelectorAll('.message.assistant')) {
                    if (!conv || !conv.contains(m)) live += count(m);
                }
                w.__domtrace.push({
                    t: Date.now(),
                    total: document.getElementsByTagName('*').length,
                    conv: count(conv),
                    convMsgs: conv ? conv.querySelectorAll('.message-content').length : 0,
                    live,
                    liveBubbles: [...document.querySelectorAll('.message.assistant')].filter(m => !conv || !conv.contains(m)).length,
                });
            }, 100);
        });
    }
    await page.keyboard.press('Enter');
    log('sent prompt; waiting for stream to finish');
    const t0 = Date.now();
    const streamRows = [];
    let started = false;
    while (Date.now() - t0 < STREAM_MAX_S * 1000) {
        await new Promise(r => setTimeout(r, 1000));
        streamRows.push(await sample());
        const streaming = await page.evaluate(() => {
            const el = document.querySelector('[contenteditable="true"][data-placeholder]');
            return !!el && /feedback for running tools/i.test(el.getAttribute('data-placeholder') || '');
        });
        if (streaming) started = true;
        else if (started && Date.now() - t0 > 5000) break;
    }
    const streamProfile = (await cdp.send('HeapProfiler.stopSampling')).profile;
    const cpuProfile = args.cpu ? (await cdp.send('Profiler.stop')).profile : null;
    // Raw profile, loadable in DevTools Performance panel, for re-aggregation.
    if (cpuProfile) fs.writeFileSync(path.join(OUT, `${run}.stream.cpuprofile`), JSON.stringify(cpuProfile));
    const streamed = await page.evaluate(() => {
        const nodes = document.querySelectorAll('.message-content');
        const last = nodes[nodes.length - 1];
        return last ? last.textContent.length : 0;
    });
    summary.phases.stream = {
        seconds: Math.round((Date.now() - t0) / 1000), started, lastMessageChars: streamed,
        heapUsedMinMB: Math.min(...streamRows.map(r => r.heapUsedMB)),
        heapUsedMaxMB: Math.max(...streamRows.map(r => r.heapUsedMB)),
        nodesStart: streamRows[0].nodes, nodesEnd: streamRows.at(-1).nodes,
        listenersStart: streamRows[0].listeners, listenersEnd: streamRows.at(-1).listeners,
        alloc: await summarizeProfile(streamProfile, 'stream'),
        cpu: cpuProfile ? await summarizeCpu(cpuProfile, 'stream') : undefined,
    };
    await new Promise(r => setTimeout(r, 5000));
    if (args.domtrace) {
        const trace = await page.evaluate(() => { clearInterval(window.__domtraceTimer); return window.__domtrace; });
        fs.writeFileSync(path.join(OUT, `${run}.domtrace.jsonl`), trace.map(r => JSON.stringify(r)).join('\n') + '\n');
        const muts = await page.evaluate(() => { window.__mutObs?.disconnect(); return window.__mut || []; });
        fs.writeFileSync(path.join(OUT, `${run}.mutations.jsonl`), muts.map(r => JSON.stringify(r)).join('\n') + '\n');
        log(`[domtrace] ${muts.length} message-subtree mutation records (>=50 nodes)`);
        const peak = trace.reduce((a, b) => (b.total > a.total ? b : a), trace[0]);
        const dual = trace.filter(r => r.liveBubbles > 0 && r.convMsgs > (trace[0]?.convMsgs ?? 0)).length;
        summary.phases.stream.domtrace = { samples: trace.length, peak, dualBubbleSamples: dual,
            first: trace[0], last: trace.at(-1) };
        log(`[domtrace] ${trace.length} samples; peak total=${peak.total} conv=${peak.conv} live=${peak.live} convMsgs=${peak.convMsgs}; samples with both bubbles: ${dual}`);
    }
    summary.phases.afterStream = await forcedGcFloor('afterStream');
}

clearInterval(samplerTimer);
sampleStream.end();
try {
    const dom = await cdp.send('Memory.getDOMCounters');
    summary.domCounters = dom;
} catch { /* not available in this Chrome */ }
fs.writeFileSync(path.join(OUT, `${run}.summary.json`), JSON.stringify(summary, null, 2));
log('summary →', path.join(OUT, `${run}.summary.json`));
log('samples →', samplesPath);
await browser.close();
