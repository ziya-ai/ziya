/**
 * Regression test for D-035 / group G-32 — the ONE genuine remaining gap in the
 * network `definition` recovery path.
 *
 * The other 11 near-miss specs in this defect (trailing commas, unquoted keys,
 * single quotes, markdown fence, smart quotes, `graph` envelope, numeric/string
 * id slip, transparent/token colours, plural `styles`, missing endpoints) are
 * already recovered by prior work (D-208..D-213, D-206) and are covered by the
 * existing network suites.
 *
 * network-w4-06, however, mixes TWO slips: semicolons used as object-member
 * separators (handled by `replaceUnquotedSemicolons`) AND a MISSING COMMA
 * between two adjacent node objects (`}` <newline> `{`). JSON5 tolerates the
 * former but rejects the latter, so `lenientParseNetworkObject` returned
 * `undefined`, no plugin claimed the spec, and D3Renderer retried to the 30s
 * empty-DOM timeout.
 *
 * The fix adds `insertMissingItemCommas` (a string-aware repair that inserts a
 * comma between a value-closer `}`/`]` and a following value-opener `{`/`[`
 * separated only by whitespace) and a final JSON5 parse attempt that applies it.
 *
 * Direction is pinned: on the UNPATCHED tree `insertMissingItemCommas` does not
 * exist (the import is `undefined`, so the calls throw) AND
 * `lenientParseNetworkObject` returns `undefined` for the w4-06 body — either
 * way these assertions fail without the fix.
 */
import {
    lenientParseNetworkObject,
    insertMissingItemCommas,
    replaceUnquotedSemicolons,
} from '../networkDiagram';

// The exact network-w4-06 definition string (semicolons + a missing comma
// between the "Web" and "Cache" node objects).
const W4_06 = `{
  "nodes": [
    {"id": "Web", "x": 150, "y": 120, "size": 16}
    {"id": "Cache", "x": 330, "y": 250, "size": 16},
    {"id": "Origin", "x": 520, "y": 120, "size": 16}
  ];
  "links": [
    {"source": "Web", "target": "Cache"},
    {"source": "Cache", "target": "Origin"}
  ];
  "width": 640;
  "height": 380;
  "style": {"labelColor": "#7e7e7e"; "fontSize": 13}
}`;

describe('D-035/G-32 network missing-comma recovery (network-w4-06)', () => {
    test('lenientParseNetworkObject recovers the w4-06 body (missing comma between array items)', () => {
        const parsed = lenientParseNetworkObject(W4_06);
        // Pre-fix: this was `undefined` (JSON5 rejects `}` <ws> `{`).
        expect(parsed).toBeTruthy();
        expect(Array.isArray(parsed.nodes)).toBe(true);
        expect(parsed.nodes).toHaveLength(3);
        expect(parsed.nodes.map((n: any) => n.id)).toEqual(['Web', 'Cache', 'Origin']);
        expect(parsed.links).toHaveLength(2);
        // The semicolon-separated top-level members are also recovered.
        expect(parsed.width).toBe(640);
        expect(parsed.height).toBe(380);
        expect(parsed.style.labelColor).toBe('#7e7e7e');
    });

    test('insertMissingItemCommas inserts a comma between adjacent closer/opener pairs', () => {
        expect(insertMissingItemCommas('[{"a":1}\n{"b":2}]')).toBe('[{"a":1},\n{"b":2}]');
        expect(insertMissingItemCommas('[[1]\n[2]]')).toBe('[[1],\n[2]]');
        expect(insertMissingItemCommas('[{"a":1}  [2]]')).toBe('[{"a":1},  [2]]');
    });

    test('insertMissingItemCommas is a no-op on already-valid JSON and never corrupts string content', () => {
        // A valid array (elements already comma-separated) is unchanged.
        const valid = '{"a":[{"x":1},{"y":2}]}';
        expect(insertMissingItemCommas(valid)).toBe(valid);
        // A string VALUE that literally contains "}{" or "][" must not gain a comma.
        const withBraces = '{"s":"a}{b","t":"]["}';
        expect(insertMissingItemCommas(withBraces)).toBe(withBraces);
        expect(JSON.parse(insertMissingItemCommas(withBraces))).toEqual({ s: 'a}{b', t: '][' });
    });

    test('the repair only fires as a last resort — a clean semicolon-only body still recovers', () => {
        // A body with ONLY the semicolon slip (no missing comma) still parses,
        // proving the new attempt does not change earlier-passing behaviour.
        const semiOnly = '{"nodes": [{"id": "A"}, {"id": "B"}]; "links": []}';
        const parsed = lenientParseNetworkObject(semiOnly);
        expect(parsed).toBeTruthy();
        expect(parsed.nodes).toHaveLength(2);
        // And the semicolon folder alone (no missing comma here) already yields valid JSON5.
        expect(() => JSON.parse(insertMissingItemCommas(replaceUnquotedSemicolons(semiOnly)))).not.toThrow();
    });
});
