import {
    liftJointV1BodyAttrs,
    normalizeJointElement,
    normalizeJointCells,
} from '../jointShapeResolver';

// D-419 (G-e1779c / joint-w4-09): the deprecated JointJS v1/v2 dialect stores an
// element's body styling under a geometry-named selector — attrs.rect.fill,
// attrs.circle.fill, etc. — rather than the v3 attrs.body.fill. The plugin's
// style reader (computeJointElementStyle) inspects ONLY attrs.body, so a v1
// spec's fill intent (#3498db) was structurally preserved but silently dropped.
// The dialect normalizer must lift the v1 selector onto attrs.body.
//
// These assertions FAIL against pre-fix code: normalizeJointElement did not
// touch the geometry-selector attrs and liftJointV1BodyAttrs did not exist.

describe('liftJointV1BodyAttrs — v1 geometry selector -> v3 body', () => {
    it('lifts attrs.rect fill/stroke onto a v3 body object', () => {
        const body = liftJointV1BodyAttrs({ text: { text: 'X' }, rect: { fill: '#3498db', stroke: '#111' } });
        expect(body).toEqual({ fill: '#3498db', stroke: '#111' });
    });

    it('supports circle / ellipse / path / polygon / .body selectors', () => {
        expect(liftJointV1BodyAttrs({ circle: { fill: '#abc' } })).toEqual({ fill: '#abc' });
        expect(liftJointV1BodyAttrs({ ellipse: { fill: '#def' } })).toEqual({ fill: '#def' });
        expect(liftJointV1BodyAttrs({ '.body': { fill: '#123' } })).toEqual({ fill: '#123' });
    });

    it('does NOT override an already-present v3 attrs.body', () => {
        expect(liftJointV1BodyAttrs({ body: { fill: '#000' }, rect: { fill: '#fff' } })).toBeUndefined();
    });

    it('returns undefined when no geometry selector carries styling', () => {
        expect(liftJointV1BodyAttrs({ text: { text: 'label only' } })).toBeUndefined();
        expect(liftJointV1BodyAttrs(undefined)).toBeUndefined();
    });
});

describe('normalizeJointElement — recovers v1 dialect body fill (D-419)', () => {
    it('exposes attrs.rect.fill as attrs.body.fill', () => {
        const cell = { id: 'u1', type: 'basic.Rect', attrs: { text: { text: 'Producer' }, rect: { fill: '#3498db' } } };
        const out = normalizeJointElement(cell);
        expect(out.attrs.body).toEqual({ fill: '#3498db' });
        // label text still lifted, shape type still resolved
        expect(out.label).toBe('Producer');
        expect(out.type).toBe('rect');
    });
});

describe('normalizeJointCells — joint-w4-09 v1 dialect fill survives the split', () => {
    it('every basic.Rect element keeps its #3498db fill under attrs.body', () => {
        const cells = [
            { id: 'u1', type: 'basic.Rect', attrs: { text: { text: 'Producer' }, rect: { fill: '#3498db' } } },
            { id: 'u2', type: 'basic.Rect', attrs: { text: { text: 'Topic' }, rect: { fill: '#3498db' } } },
            { id: 'u3', type: 'basic.Rect', attrs: { text: { text: 'Consumer' }, rect: { fill: '#3498db' } } },
            { id: 'b1', type: 'basic.Link', source: { id: 'u1' }, target: { id: 'u2' } },
            { id: 'b2', type: 'link', source: { id: 'u2' }, target: { id: 'u3' } },
        ];
        const { elements, connections } = normalizeJointCells(cells);
        expect(elements).toHaveLength(3);
        expect(connections).toHaveLength(2);
        for (const el of elements) {
            expect(el.attrs.body.fill).toBe('#3498db');
        }
    });
});
