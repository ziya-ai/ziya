/**
 * Timing harness for the "clicking this conversation freezes the UI" bug.
 *
 * Reads a real conversation dumped to /tmp/freeze-chat.json (see the
 * accompanying notes) and runs each pure markdown-preprocessing pass over
 * every message body, reporting wall-clock cost per pass per message.
 *
 * This isolates a hang in the pure text pipeline from a hang in React
 * rendering: if a pass here runs away, the browser freeze is reproduced
 * headlessly with a real stack.  If every pass is fast, the freeze is in
 * the render/DOM layer instead and this harness has ruled the text
 * pipeline out.
 */
import * as fs from 'fs';
import {
    escapeNestedBacktickFences,
    stripBareProseFences,
    classifyFenceLines,
    applyOutsideFences,
    applyOutsideCodeSpans,
    splitJsonSpecTrailingContent,
    upgradeNestedFences,
    repairAtomicFenceRuns,
    findCodeSpans,
} from '../fenceScanner';

const FIXTURE = '/tmp/freeze-chat.json';

interface Msg { role?: string; content?: unknown; }

const passes: Array<[string, (s: string) => unknown]> = [
    ['classifyFenceLines', (s) => classifyFenceLines(s)],
    ['findCodeSpans', (s) => findCodeSpans(s)],
    ['escapeNestedBacktickFences', (s) => escapeNestedBacktickFences(s)],
    ['upgradeNestedFences', (s) => upgradeNestedFences(s)],
    ['splitJsonSpecTrailingContent', (s) => splitJsonSpecTrailingContent(s)],
    ['repairAtomicFenceRuns', (s) => repairAtomicFenceRuns(s)],
    ['stripBareProseFences', (s) => stripBareProseFences(s)],
    // The five regex fixups MarkdownRenderer runs through applyOutsideFences
    ['applyOutsideFences:heading', (s) =>
        applyOutsideFences(s, (t) => t.replace(/(^#{1,6}\s+[^\n`]+?)\s+(`{3,}[a-zA-Z0-9_-]*)(?=\s|$)/gm, '$1\n\n$2'))],
    ['applyOutsideFences:list', (s) =>
        applyOutsideFences(s, (t) => t.replace(/(\d+\.\s+[^\n`]+?)\s+(`{3,}[a-zA-Z0-9_-]*)(?=\s|$)/gm, '$1\n\n$2'))],
    ['applyOutsideFences:para', (s) =>
        applyOutsideFences(s, (t) => t.replace(/([^\n])\n(`{3,}[a-zA-Z0-9_-]*)(?=\s|$)/g, '$1\n\n$2'))],
    ['applyOutsideFences:concat', (s) =>
        applyOutsideFences(s, (t) => t.replace(/([^\n`])(`{3,}[a-zA-Z][a-zA-Z0-9_-]*)(?=\s|$)/g, '$1\n\n$2'))],
    ['applyOutsideCodeSpans:noop', (s) => applyOutsideCodeSpans(s, (t) => t)],
];

describe('markdown preprocessing cost on the freezing conversation', () => {
    if (!fs.existsSync(FIXTURE)) {
        it('skipped: fixture missing', () => {
            console.warn(`No ${FIXTURE}; dump the chat there first.`);
            expect(true).toBe(true);
        });
        return;
    }

    const chat = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'));
    const msgs: Msg[] = chat.messages || [];

    it('reports per-pass timings and flags runaway passes', () => {
        const slow: string[] = [];
        let grand = 0;

        msgs.forEach((m, i) => {
            const c = typeof m.content === 'string' ? m.content : '';
            if (!c) return;
            const row: string[] = [];
            let total = 0;
            for (const [name, fn] of passes) {
                const t0 = Date.now();
                try {
                    fn(c);
                } catch (e) {
                    row.push(`${name}=THREW(${(e as Error).message.slice(0, 60)})`);
                    continue;
                }
                const dt = Date.now() - t0;
                total += dt;
                if (dt >= 50) row.push(`${name}=${dt}ms`);
                if (dt >= 1000) slow.push(`msg[${i}] ${name} ${dt}ms`);
            }
            grand += total;
            console.log(
                `msg[${i}] ${m.role} chars=${c.length} total=${total}ms` +
                (row.length ? `  hot: ${row.join(' ')}` : ''),
            );
        });

        console.log(`GRAND TOTAL pure-preprocessing: ${grand}ms across ${msgs.length} messages`);
        if (slow.length) console.log('RUNAWAY PASSES:\n  ' + slow.join('\n  '));
        expect(slow).toEqual([]);
    }, 600000);
});
