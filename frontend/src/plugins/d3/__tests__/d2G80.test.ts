/**
 * G-80 — d2 plugin recovery repairs (shared file: d2Plugin.ts).
 *
 * Both defects are THEME-INVARIANT: the smart-quote fold and the Mermaid sniff
 * run on the definition text before any colour is resolved from the theme, so
 * the parse output and the detection verdict are byte-identical in light and
 * dark. Where the Mermaid verdict drives a render-path message card, that card
 * is theme-branched only in its colours (the shared decision is theme-blind and
 * is what is asserted here).
 *
 *   D-101 (backlog D-251) normalizeD2SmartQuotes folds U+201C/201D/2018/2019 to
 *          ASCII so a curly-quoted label behaves like the ASCII form and the
 *          curly glyphs never survive into a node / edge label.
 *   D-102 (backlog D-183) looksLikeMermaid detects Mermaid flowchart source
 *          (bracket/brace labels + `-->`/`-.->` arrows) mis-typed as d2 so the
 *          renderer can say "looks like Mermaid, not D2" instead of drawing
 *          each node twice plus a blank endpoint box.
 *
 * Direction: each assertion is paired with a check documenting the PRE-FIX
 * value — the helpers do not exist in unpatched d2Plugin.ts (the import itself
 * fails to compile against HEAD), and the parse of the raw payloads shows the
 * mangling the fix prevents.
 */
import {
  D2Parser,
  normalizeD2SmartQuotes,
  looksLikeMermaid,
} from '../d2Plugin';

const CURLY = /[\u201C\u201D\u2018\u2019]/;

// ---------------------------------------------------------------------------
// D-101 / backlog D-251 — smart-quote normalisation (d2-w4-04)
// ---------------------------------------------------------------------------
describe('D-251 smart/curly quotes are folded to ASCII', () => {
  test('DIRECTION: the raw payload carries curly glyphs a pre-fix parse leaves in the label', () => {
    const raw = 'web: \u201CWeb Server\u201D';
    expect(CURLY.test(raw)).toBe(true);
    // Pre-fix parseSimpleNode did `line.split(":")` verbatim (no smart-quote
    // fold, no ASCII-quote strip), so the label kept the curly glyphs.
    const preFixLabel = raw.split(':').slice(1).join(':').trim();
    expect(CURLY.test(preFixLabel)).toBe(true);
  });

  test('normalizeD2SmartQuotes maps curly quotes to ASCII (em/en dashes untouched)', () => {
    expect(normalizeD2SmartQuotes('\u201CWeb Server\u201D')).toBe('"Web Server"');
    expect(normalizeD2SmartQuotes('\u2018API\u2019')).toBe("'API'");
    // Em-dash is a legitimate label glyph, not a delimiter — left as-is.
    expect(normalizeD2SmartQuotes('Database \u2014 primary')).toBe('Database \u2014 primary');
    // Idempotent.
    expect(normalizeD2SmartQuotes(normalizeD2SmartQuotes('\u201Cx\u201D'))).toBe('"x"');
  });

  test('no node/edge label retains a curly quote after parse (d2-w4-04)', () => {
    const def = [
      'web: \u201CWeb Server\u201D',
      'api: \u2018API Service\u2019',
      'db: Database \u2014 primary',
      'web -> api: \u201Chands off\u201D',
      'api -> db',
    ].join('\n');
    const { nodes, edges } = new D2Parser().parse(def);
    for (const n of nodes) expect(CURLY.test(String(n.label ?? ''))).toBe(false);
    for (const e of edges) expect(CURLY.test(String(e.label ?? ''))).toBe(false);
    // The em-dash label still renders (glyph preserved, not stripped).
    const dbNode = nodes.find(n => n.id === 'db');
    expect(dbNode && String(dbNode.label)).toContain('\u2014');
  });

  test('parity: curly-quoted input parses to the same labels as the ASCII form', () => {
    const curly = new D2Parser().parse('web: \u201CWeb Server\u201D\nweb -> api: \u201Chands off\u201D');
    const ascii = new D2Parser().parse('web: "Web Server"\nweb -> api: "hands off"');
    const label = (r: any, id: string) => r.nodes.find((n: any) => n.id === id)?.label;
    expect(label(curly, 'web')).toBe(label(ascii, 'web'));
    expect(curly.edges[0].label).toBe(ascii.edges[0].label);
  });
});

// ---------------------------------------------------------------------------
// D-102 / backlog D-183 — Mermaid dialect detection (d2-w4-06)
// ---------------------------------------------------------------------------
describe('D-183 Mermaid flowchart mis-typed as d2 is detected', () => {
  const MERMAID_W4_06 = 'A[Web Server] --> B{API Gateway}\nB --> C[(Database)]\nC -.-> A\n';

  test('DIRECTION: the pre-fix parser mangles the Mermaid payload into extra boxes', () => {
    // Old path had no dialect sniff: bracket/brace labels became part of the
    // node id and `-->`/`-.->` were not d2 connectors, so the 3 logical nodes
    // did NOT come out as a clean {A,B,C} set. Prove the mangling exists so the
    // guard is justified (this is the behaviour looksLikeMermaid short-circuits).
    const { nodes } = new D2Parser().parse(MERMAID_W4_06);
    const ids = new Set(nodes.map(n => n.id));
    const cleanThree = ids.size === 3 && ['A', 'B', 'C'].every(k => ids.has(k));
    expect(cleanThree).toBe(false);
  });

  test('looksLikeMermaid is TRUE for the Mermaid flowchart payload (d2-w4-06)', () => {
    expect(looksLikeMermaid(MERMAID_W4_06)).toBe(true);
  });

  test('looksLikeMermaid is TRUE for an explicit flowchart/graph header', () => {
    expect(looksLikeMermaid('graph TD\n  A --> B')).toBe(true);
    expect(looksLikeMermaid('flowchart LR\n  A --> B')).toBe(true);
    expect(looksLikeMermaid('sequenceDiagram\n  Alice->>Bob: hi')).toBe(true);
  });

  test('looksLikeMermaid is FALSE for valid d2 (no false positives)', () => {
    expect(looksLikeMermaid('a -> b')).toBe(false);
    expect(looksLikeMermaid('web: Web Server\nweb -> api\napi -> db')).toBe(false);
    // A d2 sql_table node body: brace present but `: {` (not a bare `id{`),
    // and no Mermaid arrow.
    expect(looksLikeMermaid('users: {\n  shape: sql_table\n  id: int\n}')).toBe(false);
    // A d2 container with a single-dash edge inside.
    expect(looksLikeMermaid('group {\n  a -> b\n}')).toBe(false);
    // A d2 inline-property node (single-dash arrow only elsewhere).
    expect(looksLikeMermaid('node: { shape: circle }\nnode -> other')).toBe(false);
  });
});
