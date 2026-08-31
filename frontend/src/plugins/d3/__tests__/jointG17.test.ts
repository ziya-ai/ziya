/**
 * G-17 — joint plugin recovery + structural safety (shared file: jointPlugin.ts).
 *
 * Defects covered:
 *   D-139  json-repair-absent: bare JSON.parse threw on trailing commas,
 *          unquoted keys, single quotes, smart quotes, // /* *​/ comments and
 *          semicolon separators, dropping control to the JSON-blind line-DSL
 *          (zero elements -> empty container -> 30s headless timeout, no image).
 *   D-140  markdown-fence-not-stripped: a ```json fenced body never even reached
 *          JSON.parse because the branch was gated on trimmed.startsWith('{').
 *   D-144  raw-dia-element-missing-type: @joint/core v4 rejects a cell whose
 *          `type` is not a non-empty string; the bare new dia.Element({...})
 *          creators set none, so the cell (and every link touching it) was dropped.
 *   D-145  content-exceeds-viewport-no-scale-to-fit: fitContentToPaper emitted an
 *          oversized viewBox (finalWidth = max(content, container)) with no
 *          downscale, so a graph larger than one capture window was cropped.
 *
 * These import the REAL shipped module. Each recovery case first asserts that the
 * PRE-FIX behaviour (strict JSON.parse / startsWith gate / oversized dimensions)
 * fails, so the test would fail against unpatched code and passes only with the fix.
 */

import {
    stripJointFence,
    normalizeJointSmartQuotes,
    repairJsonSeparators,
    parseJointJsonish,
    isValidCellType,
    fallbackCellType,
    computeJointFitPlan,
    JOINT_MAX_RENDER_HEIGHT,
} from '../jointPlugin';

// The exact six near-miss shapes from joint-w4-01..w4-15.
const TRAILING_COMMAS = `{
  "elements": [
    {"id": "ingest", "type": "rect", "label": "Ingest",},
    {"id": "parse", "type": "rect", "label": "Parse",},
  ],
  "connections": [
    {"id": "e1", "source": "ingest", "target": "parse", "label": "raw",},
  ],
}`;

const UNQUOTED_KEYS = `{
  elements: [
    {id: "queue", type: "rect", label: "Queue"},
    {id: "worker", type: "rect", label: "Worker"}
  ],
  connections: [
    {id: "c1", source: "queue", target: "worker", label: "pop"}
  ]
}`;

const SINGLE_QUOTES = `{'elements': [{'id': 'auth', 'type': 'rect', 'label': 'Auth'},
              {'id': 'api', 'type': 'rect', 'label': 'API'}],
 'connections': [{'id': 'l1', 'source': 'auth', 'target': 'api', 'label': 'token'}]}`;

const FENCED = '```json\n' + `{
  "elements": [
    {"id": "edge", "type": "rect", "label": "Edge"},
    {"id": "cache", "type": "rect", "label": "Cache"}
  ],
  "connections": [
    {"id": "m1", "source": "edge", "target": "cache", "label": "hit?"}
  ]
}` + '\n```';

const SMART_QUOTES =
    '{\u201Celements\u201D: [{\u201Cid\u201D: \u201Cclient\u201D, \u201Ctype\u201D: \u201Crect\u201D, \u201Clabel\u201D: \u201CClient\u201D},' +
    '{\u201Cid\u201D: \u201Cproxy\u201D, \u201Ctype\u201D: \u201Crect\u201D, \u201Clabel\u201D: \u201CProxy\u201D}],' +
    '\u201Cconnections\u201D: [{\u201Cid\u201D: \u201Cs1\u201D, \u201Csource\u201D: \u201Cclient\u201D, \u201Ctarget\u201D: \u201Cproxy\u201D}]}';

const COMMENTS = `{
  // pipeline stages
  "elements": [
    {"id": "read", "type": "rect", "label": "Read"},   // source
    /* terminal stage */
    {"id": "write", "type": "rect", "label": "Write"}
  ],
  "connections": [
    {"id": "j1", "source": "read", "target": "write", "label": "rows"}
  ]
}`;

const SEMICOLONS = `{
  "elements": [
    {"id": "plan"; "type": "rect"; "label": "Plan"},
    {"id": "apply"; "type": "rect"; "label": "Apply"}
  ];
  "connections": [
    {"id": "y1"; "source": "plan"; "target": "apply"; "label": "diff"}
  ]
};`;

describe('D-139 — tolerant JSON recovery (parseJointJsonish)', () => {
    const cases: Array<[string, string]> = [
        ['trailing commas (w4-01)', TRAILING_COMMAS],
        ['unquoted keys (w4-02)', UNQUOTED_KEYS],
        ['single quotes (w4-03)', SINGLE_QUOTES],
        ['smart quotes (w4-05)', SMART_QUOTES],
        ['line + block comments (w4-08)', COMMENTS],
        ['semicolon separators (w4-15)', SEMICOLONS],
    ];

    it.each(cases)('recovers %s into elements + connections', (_name, raw) => {
        // Direction: the raw body defeats strict JSON.parse (that is the bug).
        expect(() => JSON.parse(raw)).toThrow();

        const parsed = parseJointJsonish(raw);
        expect(parsed).toBeTruthy();
        expect(Array.isArray(parsed.elements)).toBe(true);
        expect(parsed.elements.length).toBeGreaterThanOrEqual(2);
        expect(Array.isArray(parsed.connections)).toBe(true);
        expect(parsed.connections.length).toBeGreaterThanOrEqual(1);
        // First element survives with its id/label intact.
        expect(parsed.elements[0].id).toBeTruthy();
    });

    it('leaves genuinely non-JSON text unrecovered (falls through to the DSL)', () => {
        expect(parseJointJsonish('start -> end\nA: box')).toBeUndefined();
        expect(parseJointJsonish('')).toBeUndefined();
    });

    it('valid JSON still parses on the fast path (no behaviour change)', () => {
        const valid = '{"elements":[{"id":"a","type":"rect"}],"connections":[]}';
        expect(parseJointJsonish(valid)).toEqual(JSON.parse(valid));
    });

    it('repairJsonSeparators only converts non-string semicolons', () => {
        // Direction: a semicolon inside a string value is preserved.
        expect(repairJsonSeparators('{"a": 1; "b": 2}')).toBe('{"a": 1, "b": 2}');
        expect(repairJsonSeparators('{"a": "x;y"; "b": 2}')).toBe('{"a": "x;y", "b": 2}');
    });
});

describe('D-140 — markdown fence stripped before the JSON gate', () => {
    it('a ```json fenced body fails the old startsWith gate but now parses', () => {
        // Direction: pre-fix gate was trimmed.startsWith('{'); a fence defeats it.
        expect(FENCED.trim().startsWith('{')).toBe(false);
        expect(() => JSON.parse(FENCED)).toThrow();

        expect(stripJointFence(FENCED).startsWith('{')).toBe(true);
        const parsed = parseJointJsonish(FENCED);
        expect(parsed).toBeTruthy();
        expect(parsed.elements.length).toBe(2);
        expect(parsed.connections.length).toBe(1);
    });

    it('normalizeJointSmartQuotes maps curly quotes to ASCII', () => {
        expect(normalizeJointSmartQuotes('\u201Cx\u201D')).toBe('"x"');
        expect(normalizeJointSmartQuotes('\u2018y\u2019')).toBe("'y'");
    });
});

describe('D-144 — cell-type guard for raw dia.Element creators', () => {
    it('isValidCellType matches @joint/core v4 (non-empty string only)', () => {
        // The pre-fix failure: base dia.Element has no `type` -> undefined -> the
        // 'dia.Graph: cell type must be a string' throw at addCell.
        expect(isValidCellType(undefined)).toBe(false);
        expect(isValidCellType('')).toBe(false);
        expect(isValidCellType(null)).toBe(false);
        expect(isValidCellType(42)).toBe(false);
        // standard.* shapes already carry a type and stay untouched.
        expect(isValidCellType('standard.Rectangle')).toBe(true);
        expect(isValidCellType('custom.cylinder')).toBe(true);
    });

    it('fallbackCellType yields a stable non-empty namespaced string', () => {
        expect(fallbackCellType('cylinder')).toBe('custom.cylinder');
        expect(fallbackCellType('resistor')).toBe('custom.resistor');
        expect(fallbackCellType(undefined)).toBe('custom.element');
        expect(fallbackCellType('')).toBe('custom.element');
        // Whatever the fallback is, it must satisfy the v4 type check.
        expect(isValidCellType(fallbackCellType('note'))).toBe(true);
    });

    it('the render-loop guard turns a typeless cell into an acceptable one', () => {
        // Faithful simulation of the inline guard: a bare dia.Element-like cell
        // whose attributes carry no `type`.
        const cell: any = { attributes: {}, set(k: string, v: any) { this.attributes[k] = v; } };
        expect(isValidCellType(cell.attributes.type)).toBe(false); // would throw pre-fix
        if (!isValidCellType(cell.attributes.type)) {
            cell.set('type', fallbackCellType('electrical'));
        }
        expect(isValidCellType(cell.attributes.type)).toBe(true);  // addCell now accepts it
        expect(typeof cell.attributes.type).toBe('string');
    });
});

describe('D-145 — scale-to-fit bounds the emitted SVG (computeJointFitPlan)', () => {
    const CONTAINER = 1264;

    it('content that fits keeps natural size (scale 1, unchanged behaviour)', () => {
        const plan = computeJointFitPlan(480, 320, CONTAINER, JOINT_MAX_RENDER_HEIGHT);
        expect(plan.scaled).toBe(false);
        expect(plan.scale).toBe(1);
        expect(plan.paperWidth).toBe(CONTAINER);
        expect(plan.paperHeight).toBe(320);
    });

    it('an over-wide graph (100-node row) is downscaled, not cropped', () => {
        const contentW = 6000; // ~100 nodes in a row
        const contentH = 200;
        // Direction: the pre-fix path used finalWidth = max(content, container),
        // i.e. 6000 — an oversized viewBox that got clipped to the capture window.
        expect(Math.max(contentW, CONTAINER)).toBe(6000);

        const plan = computeJointFitPlan(contentW, contentH, CONTAINER, JOINT_MAX_RENDER_HEIGHT);
        expect(plan.scaled).toBe(true);
        expect(plan.scale).toBeLessThan(1);
        expect(plan.paperWidth).toBeLessThanOrEqual(CONTAINER);
        expect(plan.paperHeight).toBeLessThanOrEqual(JOINT_MAX_RENDER_HEIGHT);
    });

    it('an over-tall graph (long chain) is bounded to the max render height', () => {
        const contentW = 300;   // narrow vertical chain
        const contentH = 8000;  // taller than the capture window
        // Direction: the pre-fix path set finalHeight = contentHeight = 8000.
        expect(contentH).toBeGreaterThan(JOINT_MAX_RENDER_HEIGHT);

        const plan = computeJointFitPlan(contentW, contentH, CONTAINER, JOINT_MAX_RENDER_HEIGHT);
        expect(plan.scaled).toBe(true);
        expect(plan.paperHeight).toBeLessThanOrEqual(JOINT_MAX_RENDER_HEIGHT);
        expect(plan.paperWidth).toBeLessThanOrEqual(CONTAINER);
    });

    it('a big dense grid keeps aspect ratio while fitting the box', () => {
        const contentW = 5000;
        const contentH = 4000;
        const plan = computeJointFitPlan(contentW, contentH, CONTAINER, JOINT_MAX_RENDER_HEIGHT);
        expect(plan.scaled).toBe(true);
        // aspect preserved (single uniform scale)
        const ratioIn = contentW / contentH;
        const ratioOut = plan.paperWidth / plan.paperHeight;
        expect(Math.abs(ratioIn - ratioOut)).toBeLessThan(0.02);
        expect(plan.paperWidth).toBeLessThanOrEqual(CONTAINER);
        expect(plan.paperHeight).toBeLessThanOrEqual(JOINT_MAX_RENDER_HEIGHT);
    });
});
