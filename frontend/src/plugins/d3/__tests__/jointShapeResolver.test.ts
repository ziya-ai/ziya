import {
    resolveJointShapeType,
    isJointLinkCell,
    extractJointElementLabel,
    normalizeJointElement,
    normalizeJointLink,
    normalizeJointCells,
} from '../jointShapeResolver';

// Regression tests for graphics-stress Issue 41 (joint):
// canonical JointJS graph JSON uses namespaced types (standard.Rectangle,
// standard.Circle, standard.Link) and stores labels under attrs.label.text.
// The plugin previously dumped every cell into the element loop keyed on a
// bare vocabulary, so links were coerced to rects (all edges dropped),
// circles rendered as rects, and labels showed the cell id.
//
// These tests import the REAL shipped module. They would fail against the
// pre-fix code because the module did not exist (import fails) and the plugin
// performed no cells-split / type-resolution / label-lift.

describe('resolveJointShapeType — namespace stripping + registry mapping', () => {
    it('maps the standard.* family onto registry keys', () => {
        expect(resolveJointShapeType('standard.Rectangle')).toBe('rectangle');
        expect(resolveJointShapeType('standard.Circle')).toBe('circle');
        expect(resolveJointShapeType('standard.Ellipse')).toBe('ellipse');
        expect(resolveJointShapeType('standard.Cylinder')).toBe('cylinder');
    });

    it('maps JointJS shape names with no direct registry key to a sane key', () => {
        expect(resolveJointShapeType('standard.HeaderedRectangle')).toBe('rect');
        expect(resolveJointShapeType('standard.TextBlock')).toBe('rect');
        expect(resolveJointShapeType('standard.Path')).toBe('rect');
        expect(resolveJointShapeType('standard.Image')).toBe('rect');
        expect(resolveJointShapeType('standard.Polygon')).toBe('diamond');
    });

    it('lowercases bare names and passes them through', () => {
        expect(resolveJointShapeType('Rectangle')).toBe('rectangle');
        expect(resolveJointShapeType('rect')).toBe('rect');
        expect(resolveJointShapeType('circle')).toBe('circle');
    });

    it('handles custom/unknown namespaces by taking the local name (registry falls back to rect at call site)', () => {
        expect(resolveJointShapeType('custom.NonexistentShapeXYZ')).toBe('nonexistentshapexyz');
    });

    it('GUARD: absent/blank/non-string type resolves to rect (never undefined)', () => {
        expect(resolveJointShapeType(undefined)).toBe('rect');
        expect(resolveJointShapeType(null)).toBe('rect');
        expect(resolveJointShapeType('')).toBe('rect');
        expect(resolveJointShapeType('   ')).toBe('rect');
        expect(resolveJointShapeType(42 as any)).toBe('rect');
    });
});

describe('isJointLinkCell — element vs link classification', () => {
    it('recognises the namespaced link family by type', () => {
        expect(isJointLinkCell({ type: 'standard.Link', source: { id: 'a' }, target: { id: 'b' } })).toBe(true);
        expect(isJointLinkCell({ type: 'standard.DoubleLink', source: { id: 'a' }, target: { id: 'b' } })).toBe(true);
        expect(isJointLinkCell({ type: 'link' })).toBe(true);
    });

    it('recognises a link by source+target endpoints even without a Link type', () => {
        expect(isJointLinkCell({ source: { id: 'a' }, target: { id: 'b' } })).toBe(true);
        expect(isJointLinkCell({ source: 'a', target: 'b' })).toBe(true);
    });

    it('GUARD: elements are NOT misclassified as links', () => {
        expect(isJointLinkCell({ type: 'standard.Rectangle', attrs: { label: { text: 'x' } } })).toBe(false);
        expect(isJointLinkCell({ type: 'standard.Circle' })).toBe(false);
        // Only one endpoint present -> not a link.
        expect(isJointLinkCell({ type: 'standard.Rectangle', source: { id: 'a' } })).toBe(false);
        expect(isJointLinkCell(null)).toBe(false);
        expect(isJointLinkCell('rect' as any)).toBe(false);
    });
});

describe('label lifting', () => {
    it('lifts attrs.label.text for elements', () => {
        expect(extractJointElementLabel({ attrs: { label: { text: 'Hello' } } })).toBe('Hello');
        expect(extractJointElementLabel({ attrs: { text: { text: 'Legacy' } } })).toBe('Legacy');
    });

    it('normalizeJointElement lifts label and resolves type without clobbering explicit label/text', () => {
        const el = normalizeJointElement({ id: 'n1', type: 'standard.Rectangle', attrs: { label: { text: 'NegSizeRoot' } } });
        expect(el.type).toBe('rectangle');
        expect(el.label).toBe('NegSizeRoot');
        // explicit top-level label wins over attrs
        const el2 = normalizeJointElement({ id: 'n2', type: 'standard.Circle', label: 'explicit', attrs: { label: { text: 'ignored' } } });
        expect(el2.label).toBe('explicit');
    });

    it('normalizeJointLink lifts labels[].attrs.text.text into label', () => {
        const lk = normalizeJointLink({ id: 'l1', source: { id: 'a' }, target: { id: 'b' }, labels: [{ attrs: { text: { text: 'edge1' } } }] });
        expect(lk.label).toBe('edge1');
    });

    it('GUARD: element with no label info is left without a fabricated label', () => {
        const el = normalizeJointElement({ id: 'plain', type: 'standard.Rectangle' });
        expect(el.label).toBeUndefined();
        expect(el.text).toBeUndefined();
    });
});

describe('normalizeJointCells — the core split (Issue 41)', () => {
    it('splits a mixed cells array into elements vs links, resolving types and lifting labels', () => {
        const cells = [
            { id: 'g_root', type: 'standard.Rectangle', attrs: { label: { text: 'NegSizeRoot' } } },
            { id: 'tie_a', type: 'standard.Circle', attrs: { label: { text: 'TieA' } } },
            { id: 'tie_b', type: 'standard.Circle', attrs: { label: { text: 'TieB' } } },
            { id: 'link1', type: 'standard.Link', source: { id: 'g_root' }, target: { id: 'tie_a' }, labels: [{ attrs: { text: { text: 'e1' } } }] },
            { id: 'link2', type: 'standard.Link', source: { id: 'tie_a' }, target: { id: 'tie_b' } },
        ];
        const { elements, connections } = normalizeJointCells(cells);

        // Pre-fix behaviour dumped ALL 5 cells into elements and 0 into links.
        expect(elements.length).toBe(3);
        expect(connections.length).toBe(2);

        // Elements got real shape types + real labels (not the id).
        expect(elements.map(e => e.type)).toEqual(['rectangle', 'circle', 'circle']);
        expect(elements.map(e => e.label)).toEqual(['NegSizeRoot', 'TieA', 'TieB']);

        // Links preserved their endpoints and lifted the label.
        expect(connections[0].source).toEqual({ id: 'g_root' });
        expect(connections[0].target).toEqual({ id: 'tie_a' });
        expect(connections[0].label).toBe('e1');
    });

    it('appends an explicit rawLinks collection without re-splitting', () => {
        const { elements, connections } = normalizeJointCells(
            [{ id: 'a', type: 'standard.Rectangle' }],
            [{ id: 'lk', source: { id: 'a' }, target: { id: 'a' } }]
        );
        expect(elements.length).toBe(1);
        expect(connections.length).toBe(1);
        expect(connections[0].id).toBe('lk');
    });

    it('handles an id-keyed object of cells', () => {
        const { elements, connections } = normalizeJointCells({
            a: { type: 'standard.Rectangle', attrs: { label: { text: 'A' } } },
            l: { type: 'standard.Link', source: { id: 'a' }, target: { id: 'a' } },
        });
        expect(elements.length).toBe(1);
        expect(elements[0].id).toBe('a');
        expect(elements[0].label).toBe('A');
        expect(connections.length).toBe(1);
    });

    it('GUARD: an already-bare element spec is preserved (not a catch-all rewrite)', () => {
        const { elements, connections } = normalizeJointCells([
            { id: 'plain', type: 'rect', label: 'Plain', position: { x: 5, y: 5 } },
        ]);
        expect(connections.length).toBe(0);
        expect(elements.length).toBe(1);
        expect(elements[0].type).toBe('rect');
        expect(elements[0].label).toBe('Plain');
        expect(elements[0].position).toEqual({ x: 5, y: 5 });
    });

    it('GUARD: tolerates junk (non-array/non-object cells, null entries)', () => {
        expect(normalizeJointCells(undefined)).toEqual({ elements: [], connections: [] });
        expect(normalizeJointCells(null)).toEqual({ elements: [], connections: [] });
        const { elements } = normalizeJointCells([null, 'x', { id: 'ok', type: 'standard.Rectangle' }]);
        expect(elements.length).toBe(1);
        expect(elements[0].id).toBe('ok');
    });
});
