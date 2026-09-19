/**
 * @jest-environment jsdom
 *
 * G-7b82d2 / D-367 — recovery, engine d2, specs d2-w3-05, d2-w4-02, d2-w4-06.
 * THEME-INVARIANT (the rejection happens before any colour is resolved; asserted
 * in BOTH themes anyway so a future theme-gated regression is caught).
 *
 * SYMPTOM: three "unrenderable" d2 inputs (comments/whitespace only -> zero
 * nodes; a JSON graph blob; a Mermaid flowchart dialect) each hung the headless
 * capture harness for the full 30000ms and then reported a generic svg:0
 * snapshot, discarding the precise message the plugin had already produced.
 *
 * REAL CAUSE (differs from triage, which pointed at diagram_renderer.py): the
 * d2 plugin ALREADY paints a rejection card for each of these via looksLikeJson
 * / looksLikeMermaid / nodes.length===0, but those <div>s carried no
 * `data-diagram-error` attribute. DiagramRenderPage decides a render finished
 * by finding an svg/canvas/img OR an element tagged `data-diagram-error`; an
 * untagged text card matches neither, so the completion poll never fired and
 * the render burned the safety timeout. The fix tags all three cards, matching
 * the cross-plugin contract in diagramErrorMarkerContract.test.ts.
 *
 * DIRECTION: against pre-fix source the three marker assertions fail (the
 * attribute did not exist on any d2 card); the valid-spec guard passes both
 * before and after, so the marker is not a blanket attribute.
 */
import { d2Plugin } from '../d2Plugin';

const MARKER = 'data-diagram-error';

function freshContainer(): HTMLElement {
    const el = document.createElement('div');
    document.body.appendChild(el);
    return el;
}

afterEach(() => { document.body.innerHTML = ''; });

// The three D-367 trigger inputs, verbatim from their specs.
const SPEC_W3_05 = // comments/whitespace only -> zero nodes
    '# only comments and whitespace\n\n#   another comment\n\n# nothing parseable at all\n';
const SPEC_W4_02 = // JSON graph blob (trailing commas) mis-emitted as d2
    '{\n  "nodes": [\n    {"id": "web", "label": "Web Server"},\n' +
    '    {"id": "api", "label": "API Service"},\n' +
    '  ],\n  "edges": [\n    {"from": "web", "to": "api"},\n  ]\n}\n';
const SPEC_W4_06 = // Mermaid flowchart dialect mis-typed as d2
    'A[Web Server] --> B{API Gateway}\nB --> C[(Database)]\nC -.-> A\n';

const CASES: Array<[string, string, string]> = [
    ['d2-w3-05 (zero-node degenerate)', SPEC_W3_05, 'No nodes found'],
    ['d2-w4-02 (JSON graph blob)', SPEC_W4_02, 'JSON'],
    ['d2-w4-06 (Mermaid dialect)', SPEC_W4_06, 'Mermaid'],
];

describe('D-367: d2 rejection cards are tagged so the harness fails fast (G-7b82d2)', () => {
    for (const isDark of [false, true]) {
        const theme = isDark ? 'dark' : 'light';
        describe(`theme=${theme}`, () => {
            for (const [name, def, needle] of CASES) {
                test(`${name} paints a marked error card`, async () => {
                    const c = freshContainer();
                    await d2Plugin.render(c, null, { type: 'd2', definition: def } as any, isDark);
                    const card = c.querySelector(`[${MARKER}]`);
                    // Without the marker the harness would hang 30s and report svg:0.
                    expect(card).not.toBeNull();
                    const msg = card!.getAttribute(MARKER) || '';
                    expect(msg.length).toBeGreaterThan(0);
                    expect(msg).toContain(needle);
                });
            }
        });
    }
});
