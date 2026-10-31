/**
 * repairAtomicFenceRuns must cover every diagram language the renderer
 * routes (reported 2026-10-16).
 *
 * A flamegraph fence missing its close swallowed the following mermaid
 * block: the flamegraph parser was handed "```mermaid" (escaped to
 * &#96;&#96;&#96;mermaid) and the stateDiagram source, and failed with
 * "line 11 has no trailing sample count".  The repair pass already closes
 * an atomic block at the next column-0 opener, but its language set had
 * not been extended when railroad, wavedrom and flamegraph were added, so
 * those blocks got no repair.
 *
 * The drift guard reads the diagram aliases straight out of
 * determineTokenType, so a renderer added there without the repair fails
 * here instead of shipping.
 */
import * as fs from 'fs';
import * as path from 'path';
import { classifyFenceLines, repairAtomicFenceRuns } from '../fenceScanner';

const BT = '```';

const REPORTED = [
    BT + 'flamegraph',
    'switchmgr;poll_loop;rx_from_mermod 120',
    'switchmgr;plan_apply;decode_capnp 30',
    '',
    BT + 'mermaid',
    'stateDiagram-v2',
    '    [*] --> PG',
    '    PG --> Done : ok',
    BT,
].join('\n');

function blocks(md: string): Array<[string, string]> {
    const lines = md.split('\n');
    const classes = classifyFenceLines(md);
    const out: Array<[string, string]> = [];
    let open = -1;
    let lang = '';
    classes.forEach((c, i) => {
        if (c.kind === 'open') { open = i; lang = c.info; }
        else if (c.kind === 'close' && open >= 0) {
            out.push([lang, lines.slice(open + 1, i).join('\n')]);
            open = -1;
        }
    });
    return out;
}

describe('repairAtomicFenceRuns: reported flamegraph + mermaid', () => {
    it('without repair, CommonMark folds the mermaid block into the flamegraph', () => {
        const b = blocks(REPORTED);
        expect(b).toHaveLength(1);
        expect(b[0][0]).toBe('flamegraph');
        expect(b[0][1]).toContain(BT + 'mermaid');
    });

    it('closes the flamegraph before the mermaid opener', () => {
        const b = blocks(repairAtomicFenceRuns(REPORTED));
        expect(b.map(x => x[0])).toEqual(['flamegraph', 'mermaid']);
        expect(b[0][1]).not.toContain(BT);
        expect(b[0][1]).toContain('decode_capnp 30');
        expect(b[1][1]).toMatch(/^stateDiagram-v2/);
        expect(b[1][1]).toContain('PG --> Done : ok');
    });
});

// Every `lang === '...'` literal in the diagram-routing section of
// determineTokenType (visualizations are routed before tool blocks).
function routedDiagramLangs(): string[] {
    const src = fs.readFileSync(
        path.resolve(__dirname, '../MarkdownRenderer.tsx'), 'utf8');
    const start = src.indexOf('Check for visualization types BEFORE tool types');
    const end = src.indexOf('Check for MCP tool blocks first', start);
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    const section = src.slice(start, end);
    const langs = new Set<string>();
    for (const m of section.matchAll(/lang === '([^']+)'/g)) langs.add(m[1]);
    return Array.from(langs);
}

describe('repairAtomicFenceRuns covers every routed diagram language', () => {
    const langs = routedDiagramLangs();

    it('found the routing section', () => {
        expect(langs).toEqual(expect.arrayContaining(
            ['mermaid', 'flamegraph', 'wavedrom', 'railroad']));
    });

    it.each(langs)('closes an unterminated %s block at the next opener', (lang) => {
        const md = [BT + lang, 'body line', BT + 'mermaid', 'graph TD', 'A-->B', BT].join('\n');
        const b = blocks(repairAtomicFenceRuns(md));
        expect(b.map(x => x[0])).toEqual([lang, 'mermaid']);
        expect(b[0][1]).toBe('body line');
    });
});
