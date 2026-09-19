/**
 * Regression test for D-386 (duplicate-cell-ids-blank-canvas), group G-3a3c03.
 *
 * drawio keeps the FIRST declaration of a repeated mxCell id. Before the fix,
 * a repeated id was pushed into the importer's cell lists more than once while
 * cellMap kept only the last cell object, so graph.addCell() ran two-plus times
 * on one already-inserted cell — maxGraph churned the child order until the
 * whole model rendered as a blank canvas (drawio-w3-03).
 *
 * dedupeDrawioCellIds drops later duplicates up front, keeping the first, so the
 * importer sees each id exactly once. These assertions FAIL without the dedupe
 * pass (all three id="D" and both id="e1" survive) and PASS with it.
 *
 * Imports the REAL shipped helpers so the test detects drift.
 */

import { dedupeDrawioCellIds, normalizeDrawIOXml } from '../drawioPlugin';

const countId = (xml: string, id: string): number =>
    (xml.match(new RegExp(`<mxCell\\b[^>]*\\bid="${id}"`, 'g')) || []).length;

// The failing spec: three vertices share id="D", two edges share id="e1".
const W3_03 =
    '<mxGraphModel dx="900" dy="600" grid="0"><root>' +
    '<mxCell id="0"/><mxCell id="1" parent="0"/>' +
    '<mxCell id="D" value="First D" style="fillColor=#dae8fc;strokeColor=#6c8ebf;" vertex="1" parent="1"><mxGeometry x="0" y="0" width="120" height="40" as="geometry"/></mxCell>' +
    '<mxCell id="D" value="Second D" style="fillColor=#d5e8d4;strokeColor=#82b366;" vertex="1" parent="1"><mxGeometry x="160" y="0" width="120" height="40" as="geometry"/></mxCell>' +
    '<mxCell id="D" value="Third D" style="fillColor=#ffe6cc;strokeColor=#d79b00;" vertex="1" parent="1"><mxGeometry x="320" y="0" width="120" height="40" as="geometry"/></mxCell>' +
    '<mxCell id="U" value="Unique" style="fillColor=#f8cecc;strokeColor=#b85450;" vertex="1" parent="1"><mxGeometry x="160" y="90" width="120" height="40" as="geometry"/></mxCell>' +
    '<mxCell id="e1" value="dup-src" edge="1" parent="1" source="D" target="U"><mxGeometry relative="1" as="geometry"/></mxCell>' +
    '<mxCell id="e1" value="dup-edge-id" edge="1" parent="1" source="U" target="D"><mxGeometry relative="1" as="geometry"/></mxCell>' +
    '</root></mxGraphModel>';

describe('dedupeDrawioCellIds (D-386 duplicate-cell-ids-blank-canvas)', () => {
    it('collapses each repeated id to a single (first) mxCell element', () => {
        const out = dedupeDrawioCellIds(W3_03);
        expect(countId(out, 'D')).toBe(1);
        expect(countId(out, 'e1')).toBe(1);
        // base cells and the unique cell are untouched
        expect(countId(out, '0')).toBe(1);
        expect(countId(out, '1')).toBe(1);
        expect(countId(out, 'U')).toBe(1);
    });

    it('keeps the FIRST declaration and drops later ones', () => {
        const out = dedupeDrawioCellIds(W3_03);
        expect(out).toContain('First D');
        expect(out).not.toContain('Second D');
        expect(out).not.toContain('Third D');
        expect(out).toContain('dup-src');       // first e1 kept
        expect(out).not.toContain('dup-edge-id'); // second e1 dropped
    });

    it('leaves a spec whose ids are already unique byte-identical', () => {
        const unique =
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>' +
            '<mxCell id="a" value="A" vertex="1" parent="1"><mxGeometry x="0" y="0" width="80" height="40" as="geometry"/></mxCell>' +
            '<mxCell id="b" value="B" vertex="1" parent="1"><mxGeometry x="120" y="0" width="80" height="40" as="geometry"/></mxCell>' +
            '</root></mxGraphModel>';
        expect(dedupeDrawioCellIds(unique)).toBe(unique);
    });

    it('applies inside the full normalizeDrawIOXml pipeline', () => {
        const out = normalizeDrawIOXml(W3_03);
        expect(countId(out, 'D')).toBe(1);
        expect(countId(out, 'e1')).toBe(1);
        expect(out).toContain('First D');
        expect(out).not.toContain('Third D');
    });
});
