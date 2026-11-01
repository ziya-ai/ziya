#!/usr/bin/env node
// Stream-end jerk trace for a running Ziya frontend.
//
// Records, on every animation frame from before the stream ends until the
// page settles: scroll position and height, the on-screen position of an
// anchor message, the heights of the final message's top-level blocks, and
// whether that message is a deferred placeholder.  Also records which
// subtrees are replaced (MutationObserver), long tasks, and the page's own
// console lines.  Read-only: changes nothing in the app.
//
// Usage:
//   node scripts/mem_probe/streamend.mjs --port 1999 --conv <id>
//        --where bottom|reading [--stream "prompt"] [--settle 8]
//
//   bottom  : stay pinned at the end while the reply streams (follow mode).
//   reading : once content arrives, wheel up ~700px and read; the anchor is
//             the message under the viewport centre at that moment.
//
// Output: .ziya/mem-probe/<run>.streamend.<where>.json (raw frames, mutations,
// long tasks, console) and a printed timeline relative to the stream end.

import { createRequire } from 'node:module';
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
const WHERE = args.where || 'bottom';
const SETTLE_S = Number(args.settle || 8);
const STREAM_MAX_S = Number(args['stream-max'] || 240);
const PROMPT = args.stream && args.stream !== 'true' ? args.stream :
    'Reply with: one short paragraph; a mermaid flowchart of 5 nodes; a vega-lite bar chart of 5 values; ' +
    'a 20-line python code block; and a 4-row markdown table. No tools.';
const OUT = path.resolve(ROOT, args.out || '.ziya/mem-probe');
const CHROME = args.chrome || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
if (!CONV) { console.error('--conv <conversationId> is required'); process.exit(2); }
if (!['bottom', 'reading'].includes(WHERE)) { console.error('--where bottom|reading'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });
const run = new Date().toISOString().replace(/[:.]/g, '-');
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ['--no-sandbox', '--window-size=1600,1200', '--disable-background-timer-throttling'],
    defaultViewport: { width: 1600, height: 1100 },
});
const page = await browser.newPage();
// Count IndexedDB reads (fix #5: no full-store getAll during a turn).
// Installed before any app script runs; records store name, method and
// whether the read was unbounded (no key/range argument).
await page.evaluateOnNewDocument(() => {
    window.__idb = [];
    const wrap = (proto, kind) => {
        for (const m of ['getAll', 'get', 'getAllKeys']) {
            const orig = proto[m];
            if (typeof orig !== 'function') continue;
            proto[m] = function (...a) {
                try {
                    const store = kind === 'index' ? `${this.objectStore.name}#${this.name}` : this.name;
                    window.__idb.push({ t: Date.now(), m, store, unbounded: a.length === 0 || a[0] == null });
                } catch {}
                return orig.apply(this, a);
            };
        }
    };
    wrap(IDBObjectStore.prototype, 'store');
    wrap(IDBIndex.prototype, 'index');
});
const consoleLines = [];
page.on('console', m => consoleLines.push({ t: Date.now(), type: m.type(), text: m.text().slice(0, 240) }));
page.on('pageerror', e => consoleLines.push({ t: Date.now(), type: 'pageerror', text: e.message.slice(0, 240) }));

// ── open the conversation (same path as probe.mjs) ─────────────────────
const base = `http://localhost:${PORT}/`;
log('loading', base);
await page.goto(base, { waitUntil: 'networkidle2', timeout: 90_000 });
await sleep(12_000);
await page.evaluate((id) => { sessionStorage.setItem('ZIYA_CURRENT_CONVERSATION_ID', id); }, CONV);
await page.reload({ waitUntil: 'networkidle2', timeout: 90_000 });
await sleep(12_000);
let n = await page.evaluate(() => document.querySelectorAll('.message-content').length);
if (n === 0) {
    await page.evaluate((id) => {
        const a = document.createElement('a');
        a.href = `ziya://chat/${id}`; a.textContent = 'probe-link';
        document.body.appendChild(a); a.click(); a.remove();
    }, CONV);
    for (let i = 0; i < 30 && n === 0; i++) {
        await sleep(1000);
        n = await page.evaluate(() => document.querySelectorAll('.message-content').length);
    }
}
log('rendered .message-content nodes:', n);
if (n === 0) { log('conversation did not render; aborting'); await browser.close(); process.exit(1); }
await sleep(8000); // let lazy mounts settle before measuring

// Pin to the end first (both scenarios start there, as a user would).
await page.evaluate(() => {
    const c = document.querySelector('.chat-container');
    if (c) c.scrollTop = c.scrollHeight;
});
await sleep(1500);

// ── in-page trace ───────────────────────────────────────────────────────
await page.evaluate(() => {
    const w = window;
    const T = w.__se = { frames: [], muts: [], longtasks: [], anchorIdx: null, endAt: null };
    const chat = () => document.querySelector('.chat-container');
    const conv = () => document.querySelector('.conversation-messages-container');
    const liveBubble = () => {
        const cv = conv();
        return [...document.querySelectorAll('.message.assistant')].find(m => !cv || !cv.contains(m)) || null;
    };
    const lastCommitted = () => {
        const cv = conv();
        if (!cv) return null;
        const all = cv.querySelectorAll('[data-message-index]');
        return all.length ? all[all.length - 1] : null;
    };
    // LazyMarkdownRenderer's deferred shell: a div whose only text is '…'
    // with an inline min-height (Conversation.tsx).  No class to match on.
    const isShell = (el) => el && el.childElementCount === 0 && el.textContent === '…' && !!el.style.minHeight;
    const shellsIn = (root) => root ? [...root.querySelectorAll('div')].filter(isShell).length : 0;
    const blockHeights = (msgEl) => {
        const mc = msgEl && msgEl.querySelector('.message-content');
        if (!mc) return null;
        // MarkdownRenderer wraps blocks one or two levels down; take the
        // first level that has more than one child.
        let lvl = mc;
        for (let i = 0; i < 4 && lvl.childElementCount === 1; i++) lvl = lvl.firstElementChild;
        return [...lvl.children].slice(0, 40).map(c => Math.round(c.getBoundingClientRect().height));
    };
    const anchorEl = () => T.anchorIdx == null ? null
        : document.querySelector(`.conversation-messages-container [data-message-index="${T.anchorIdx}"]`);
    let prevBlocks = '';
    let prevLive = !!liveBubble();
    const frame = () => {
        const c = chat();
        const live = liveBubble();
        const lc = lastCommitted();
        const target = live || lc;
        const bh = blockHeights(target);
        const bhKey = bh ? bh.join(',') : '';
        const a = anchorEl();
        const row = {
            t: performance.timeOrigin + performance.now(),
            st: c ? Math.round(c.scrollTop) : null,
            sh: c ? c.scrollHeight : null,
            ch: c ? c.clientHeight : null,
            live: !!live,
            liveH: live ? Math.round(live.getBoundingClientRect().height) : 0,
            lastIdx: lc ? lc.getAttribute('data-message-index') : null,
            lastH: lc ? Math.round(lc.getBoundingClientRect().height) : 0,
            lastShell: lc ? shellsIn(lc) : 0,
            shells: shellsIn(conv()),
            anchorTop: a ? Math.round(a.getBoundingClientRect().top) : null,
            nodes: document.getElementsByTagName('*').length,
        };
        if (bhKey !== prevBlocks) { row.blocks = bh; prevBlocks = bhKey; }
        // Keep references to the live bubble's rendered diagram nodes (the
        // first child of each D3Renderer host).  After the handoff, each is
        // either re-attached inside the committed message (adopted from the
        // park) or detached (discarded and rendered again).
        if (live) {
            T.liveDiagrams = [...live.querySelectorAll('.d3-container')].map(h => ({
                hostClass: String(h.className).slice(0, 60),
                node: h.firstElementChild,
            }));
        }
        if (prevLive && !live && T.endAt == null) T.endAt = row.t;
        prevLive = !!live;
        T.frames.push(row);
        T.raf = requestAnimationFrame(frame);
    };
    T.raf = requestAnimationFrame(frame);

    const sizeOf = (nd) => nd.nodeType === 1 ? nd.getElementsByTagName('*').length + 1 : 0;
    T.mo = new MutationObserver((recs) => {
        const t = performance.timeOrigin + performance.now();
        const cv = conv();
        const agg = new Map();
        for (const r of recs) {
            const el = r.target.nodeType === 1 ? r.target : r.target.parentElement;
            let where = 'other';
            if (el) {
                const host = el.closest('[data-message-index]');
                if (host && cv && cv.contains(host)) where = 'msg:' + host.getAttribute('data-message-index');
                else if (el.closest('.message.assistant')) where = 'live';
                else if (cv && cv.contains(el)) where = 'conv';
            }
            const e = agg.get(where) || { added: 0, removed: 0, attrs: 0 };
            for (const x of r.addedNodes) e.added += sizeOf(x);
            for (const x of r.removedNodes) e.removed += sizeOf(x);
            if (r.type === 'attributes') e.attrs++;
            agg.set(where, e);
        }
        for (const [where, e] of agg) T.muts.push({ t, where, ...e });
    });
    const root = chat() || document.body;
    T.mo.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] });
    try {
        T.po = new PerformanceObserver((list) => {
            for (const e of list.getEntries()) T.longtasks.push({ t: performance.timeOrigin + e.startTime, dur: Math.round(e.duration) });
        });
        T.po.observe({ entryTypes: ['longtask'] });
    } catch { /* not supported */ }
});

// ── send the turn ───────────────────────────────────────────────────────
const input = await page.$('[contenteditable="true"][data-placeholder]');
if (!input) throw new Error('chat input not found');
await input.focus();
await page.keyboard.type(PROMPT);
await page.keyboard.press('Enter');
const sentAt = Date.now();
log(`sent (${WHERE}); waiting for content`);

if (WHERE === 'reading') {
    // Wait for the live bubble to have some height, then wheel up like a user.
    for (let i = 0; i < 120; i++) {
        const h = await page.evaluate(() => {
            const cv = document.querySelector('.conversation-messages-container');
            const m = [...document.querySelectorAll('.message.assistant')].find(x => !cv || !cv.contains(x));
            return m ? m.getBoundingClientRect().height : 0;
        });
        if (h > 300) break;
        await sleep(500);
    }
    const box = await page.evaluate(() => {
        const r = document.querySelector('.chat-container').getBoundingClientRect();
        return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    });
    await page.mouse.move(box.x, box.y);
    for (let i = 0; i < 7; i++) { await page.mouse.wheel({ deltaY: -100 }); await sleep(60); }
    await sleep(500);
    const idx = await page.evaluate(({ x, y }) => {
        const el = document.elementFromPoint(x, y);
        const host = el && el.closest('.conversation-messages-container [data-message-index]');
        const v = host ? host.getAttribute('data-message-index') : null;
        window.__se.anchorIdx = v;
        return v;
    }, box);
    log('reading: wheeled up; anchor message index =', idx);
}

// Wait for the live bubble to go away (stream end), then settle.
let ended = null;
for (let i = 0; i < STREAM_MAX_S * 2; i++) {
    ended = await page.evaluate(() => window.__se.endAt);
    if (ended) break;
    await sleep(500);
}
log(ended ? `stream ended after ${((ended - sentAt) / 1000).toFixed(1)} s` : 'stream did not end in time');
await sleep(SETTLE_S * 1000);

const T = await page.evaluate(() => {
    const T = window.__se;
    cancelAnimationFrame(T.raf); T.mo.disconnect(); try { T.po.disconnect(); } catch {}
    const cv = document.querySelector('.conversation-messages-container');
    const diagrams = (T.liveDiagrams || []).map(d => ({
        hostClass: d.hostClass,
        tag: d.node ? d.node.tagName : null,
        isHTMLElement: d.node instanceof HTMLElement,
        connected: !!(d.node && d.node.isConnected),
        inCommitted: !!(d.node && cv && cv.contains(d.node)),
    }));
    return { frames: T.frames, muts: T.muts, longtasks: T.longtasks, anchorIdx: T.anchorIdx, endAt: T.endAt, diagrams,
             idb: (window.__idb || []).slice() };
});
await browser.close();

// ── analysis ────────────────────────────────────────────────────────────
const t0 = T.endAt;
const rel = (t) => t0 ? Math.round(t - t0) : null;
const outPath = path.join(OUT, `${run}.streamend.${WHERE}.json`);
fs.writeFileSync(outPath, JSON.stringify({ port: PORT, conv: CONV, where: WHERE, prompt: PROMPT, ...T, console: consoleLines }, null, 1));
if (!t0) { log('no stream end observed; raw trace written to', outPath); process.exit(1); }

const win = T.frames.filter(f => f.t >= t0 - 1500 && f.t <= t0 + SETTLE_S * 1000);
log(`frames in window: ${win.length}; anchor=${T.anchorIdx}`);
console.log('\n rel_ms   scrollTop  scrollH  dEnd  anchorTop  live liveH lastIdx lastH shell  nodes  blocks');
let prev = null;
for (const f of win) {
    const dEnd = f.sh != null ? f.sh - f.st - f.ch : null;
    const changed = !prev || f.st !== prev.st || f.sh !== prev.sh || f.anchorTop !== prev.anchorTop
        || f.live !== prev.live || f.lastIdx !== prev.lastIdx || f.lastShell !== prev.lastShell || f.blocks;
    if (changed) {
        console.log(`${String(rel(f.t)).padStart(7)} ${String(f.st).padStart(10)} ${String(f.sh).padStart(8)} ${String(dEnd).padStart(5)} ${String(f.anchorTop).padStart(10)}  ${f.live ? ' Y ' : ' - '} ${String(f.liveH).padStart(5)} ${String(f.lastIdx).padStart(7)} ${String(f.lastH).padStart(5)} ${String(f.lastShell).padStart(5)} ${String(f.nodes).padStart(6)}  ${f.blocks ? f.blocks.join(',') : ''}`);
    }
    prev = f;
}

const after = win.filter(f => f.t >= t0);
const anchorJumps = [];
for (let i = 1; i < after.length; i++) {
    const a = after[i - 1].anchorTop, b = after[i].anchorTop;
    if (a != null && b != null && a !== b) anchorJumps.push({ rel: rel(after[i].t), dy: b - a });
}
const dEnds = after.map(f => f.sh - f.st - f.ch);
const shs = after.map(f => f.sh);
let lastChange = 0;
for (let i = 1; i < after.length; i++) {
    const p = after[i - 1], f = after[i];
    if (f.st !== p.st || f.sh !== p.sh || f.anchorTop !== p.anchorTop || f.blocks) lastChange = rel(f.t);
}
console.log('\nmutations (|added|+|removed| >= 30) relative to stream end:');
for (const m of T.muts.filter(m => m.t >= t0 - 1500 && m.t <= t0 + SETTLE_S * 1000 && m.added + m.removed >= 30)) {
    console.log(`${String(rel(m.t)).padStart(7)}  ${m.where.padEnd(10)} +${m.added} -${m.removed}`);
}
console.log('\nlong tasks:');
for (const l of T.longtasks.filter(l => l.t >= t0 - 1500)) console.log(`${String(rel(l.t)).padStart(7)}  ${l.dur} ms`);
const near = consoleLines.filter(c => c.t >= t0 - 1500 && c.t <= t0 + SETTLE_S * 1000);
const byPrefix = {};
for (const c of near) { const k = c.text.slice(0, 40); byPrefix[k] = (byPrefix[k] || 0) + 1; }
console.log('\nconsole lines near stream end (top 25 by 40-char prefix):');
for (const [k, v] of Object.entries(byPrefix).sort((a, b) => b[1] - a[1]).slice(0, 25)) console.log(`${String(v).padStart(4)}  ${k}`);

console.log('\nlive diagram nodes at stream end -> after handoff (adopted = same node now inside the committed message):');
for (const d of T.diagrams || []) console.log(`  ${d.hostClass.padEnd(40)} <${d.tag}> HTMLElement=${d.isHTMLElement} connected=${d.connected} adopted=${d.inCommitted}`);
const summary = {
    where: WHERE, anchorIdx: T.anchorIdx, diagrams: T.diagrams,
    anchorJumpCount: anchorJumps.length,
    anchorTotalTravelPx: anchorJumps.reduce((s, j) => s + Math.abs(j.dy), 0),
    anchorJumps: anchorJumps.slice(0, 40),
    distFromEndMin: Math.min(...dEnds), distFromEndMax: Math.max(...dEnds),
    scrollHeightMin: Math.min(...shs), scrollHeightMax: Math.max(...shs),
    lastLayoutChangeMs: lastChange,
    longTasksAfterEnd: T.longtasks.filter(l => l.t >= t0).map(l => ({ rel: rel(l.t), dur: l.dur })),
};
// IndexedDB reads from send to the end of the settle window.
const turnReads = (T.idb || []).filter(r => r.t >= sentAt);
const idbByKey = {};
for (const r of turnReads) {
    const k = `${r.m}${r.unbounded ? '(ALL)' : '(key)'} ${r.store}`;
    idbByKey[k] = (idbByKey[k] || 0) + 1;
}
summary.idbReadsDuringTurn = idbByKey;
summary.idbUnboundedGetAllDuringTurn = turnReads.filter(r => r.m === 'getAll' && r.unbounded)
    .map(r => ({ rel: rel(r.t), store: r.store }));
summary.idbReadsAtLoad = (T.idb || []).filter(r => r.t < sentAt).length;
console.log('\nsummary:', JSON.stringify(summary, null, 1));
log('raw trace:', outPath);
