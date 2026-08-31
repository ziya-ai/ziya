/**
 * @jest-environment jsdom
 *
 * G-46 regression tests for the graphviz plugin. Four defects, all in
 * graphvizPlugin.ts:
 *   D-131  record port declarations (`<f0>`) leaked into the label as literal
 *          text because the label->HTML-like rewrite HTML-escaped `<`/`>`.
 *   D-132  `\l` / `\r` justification escapes were left literal (only `\n` was
 *          mapped to <br/>).
 *   D-136  light-theme clusterBorder '#cccccc' (and nodeBorder '#999999') below
 *          the 3:1 graphical floor -> nested clusters invisible in light.
 *   D-137  an unfilled node kept its authored/default black text on the dark
 *          panel (1.26:1) because dark text re-theming was gated on a fill.
 *
 * Every assertion is a DIRECTION check: the pre-fix output is asserted on the
 * raw input / old constant so the test would FAIL against the unpatched module,
 * and the exports did not exist before this fix (so the import throws pre-fix).
 * The two theme defects assert BOTH themes.
 */
import {
    convertLabelsToHtmlLike,
    labelUsesRecordPortSyntax,
    GRAPHVIZ_LIGHT_CLUSTER_BORDER,
    GRAPHVIZ_LIGHT_NODE_BORDER,
    GRAPHVIZ_DARK_PANEL_BG,
    textNeedsDarkPanelRescue,
    retintUnfilledNodeTextForDark,
    readableTextColorFor,
} from '../graphvizPlugin';
import { contrastRatio } from '../chartTheme';

// ---------------------------------------------------------------------------
// D-131  record/port labels must not be HTML-escaped
// ---------------------------------------------------------------------------
describe('D-131 record-port label handling', () => {
    it('detects a record port token', () => {
        expect(labelUsesRecordPortSyntax('<f0> left | <f1> right')).toBe(true);
        expect(labelUsesRecordPortSyntax('just plain text')).toBe(false);
        expect(labelUsesRecordPortSyntax('a | b | c')).toBe(false); // fields, no ports (w1-03 passes)
    });

    it('leaves a record/port label as a plain quoted string (w1-10)', () => {
        const dot = 'a [shape=record, label="<f0> left | <f1> mid | <f2> right"];';
        const out = convertLabelsToHtmlLike(dot);
        // Fixed: the label is untouched -> ports parsed natively by graphviz.
        expect(out).toContain('label="<f0> left | <f1> mid | <f2> right"');
        // Direction: the pre-fix rewrite would have escaped the port brackets.
        expect(out).not.toContain('&lt;f0&gt;');
        expect(out).not.toContain('label=<');
    });

    it('does not double the field width with a leaked <fNN> prefix at scale (w2-13)', () => {
        const dot = 'n [shape=record, label="<f0> a | <f1> b | <f2> c | <f3> d"];';
        const out = convertLabelsToHtmlLike(dot);
        expect(out).not.toMatch(/&lt;f\d+&gt;/);
    });

    it('still converts an ordinary (non-record) label to HTML-like', () => {
        const dot = 'a [label="hello world"];';
        expect(convertLabelsToHtmlLike(dot)).toContain('label=<hello world>');
    });
});

// ---------------------------------------------------------------------------
// D-132  \l / \r justification escapes -> HTML-like <br align=...>
// ---------------------------------------------------------------------------
describe('D-132 justification escapes', () => {
    it('maps \\l to a left-justified break (w1-12)', () => {
        const dot = 'code [label="int main() {\\l    return 0;\\l}\\l"];';
        expect(dot).toContain('\\l'); // direction: broken input carries literal \l
        const out = convertLabelsToHtmlLike(dot);
        expect(out).toContain('<br align="left"/>');
        expect(out).not.toContain('\\l');
    });

    it('maps \\r to a right-justified break', () => {
        const out = convertLabelsToHtmlLike('n [label="a\\rb"];');
        expect(out).toContain('<br align="right"/>');
        expect(out).not.toContain('\\r');
    });

    it('still maps \\n to a centred break', () => {
        expect(convertLabelsToHtmlLike('n [label="a\\nb"];')).toContain('label=<a<br/>b>');
    });
});

// ---------------------------------------------------------------------------
// D-136  light cluster/node border must clear the 3:1 graphical floor
//        (BOTH themes: light now correct, dark unchanged & still correct)
// ---------------------------------------------------------------------------
describe('D-136 cluster/node border contrast (theme)', () => {
    const WHITE = '#ffffff';
    const LIGHTGREY_FILL = '#d3d3d3';   // authored nested-cluster fill
    const CLUSTER_BG = '#f0f0f0';       // injected clusterBg
    const DARK_CLUSTER_BG = '#1a1a2e';  // dark themeColors.clusterBg
    const DARK_CLUSTER_BORDER = '#4cc9f0'; // dark themeColors.clusterBorder (UNCHANGED)

    it('light: fixed clusterBorder clears 3:1 on white AND the lightgrey nested-cluster fill', () => {
        expect(contrastRatio(GRAPHVIZ_LIGHT_CLUSTER_BORDER, WHITE)).toBeGreaterThanOrEqual(3);
        expect(contrastRatio(GRAPHVIZ_LIGHT_CLUSTER_BORDER, LIGHTGREY_FILL)).toBeGreaterThanOrEqual(3);
        expect(contrastRatio(GRAPHVIZ_LIGHT_CLUSTER_BORDER, CLUSTER_BG)).toBeGreaterThanOrEqual(3);
    });

    it('light: DIRECTION — the old #cccccc failed both backgrounds', () => {
        expect(contrastRatio('#cccccc', WHITE)).toBeLessThan(3);
        expect(contrastRatio('#cccccc', LIGHTGREY_FILL)).toBeLessThan(3);
    });

    it('light: fixed nodeBorder clears 3:1 on white (old #999999 did not)', () => {
        expect(contrastRatio(GRAPHVIZ_LIGHT_NODE_BORDER, WHITE)).toBeGreaterThanOrEqual(3);
        expect(contrastRatio('#999999', WHITE)).toBeLessThan(3); // direction
    });

    it('dark: unchanged cyan cluster border still clears 3:1 on the dark cluster bg', () => {
        expect(contrastRatio(DARK_CLUSTER_BORDER, DARK_CLUSTER_BG)).toBeGreaterThanOrEqual(3);
    });
});

// ---------------------------------------------------------------------------
// D-137  unfilled node text re-themed against the dark panel (theme)
// ---------------------------------------------------------------------------
describe('D-137 unfilled-node text on dark panel (theme)', () => {
    it('white text is readable on the dark panel; black is not (the defect)', () => {
        expect(contrastRatio('#ffffff', GRAPHVIZ_DARK_PANEL_BG)).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio('#000000', GRAPHVIZ_DARK_PANEL_BG)).toBeLessThan(1.5); // ~1.26 defect
        expect(readableTextColorFor(GRAPHVIZ_DARK_PANEL_BG)).toBe('#ffffff');
    });

    it('rescue predicate: black / missing rescued; already-light left alone', () => {
        expect(textNeedsDarkPanelRescue('#000000')).toBe(true);
        expect(textNeedsDarkPanelRescue('black')).toBe(true);
        expect(textNeedsDarkPanelRescue(null)).toBe(true);
        expect(textNeedsDarkPanelRescue('#ffffff')).toBe(false);
        expect(textNeedsDarkPanelRescue('#eeeeee')).toBe(false);
    });

    function makeNode(textFill: string | null): SVGTextElement {
        const svgns = 'http://www.w3.org/2000/svg';
        const g = document.createElementNS(svgns, 'g');
        g.setAttribute('class', 'node');
        const poly = document.createElementNS(svgns, 'polygon');
        poly.setAttribute('fill', 'none'); // unfilled
        const text = document.createElementNS(svgns, 'text');
        if (textFill !== null) text.setAttribute('fill', textFill);
        g.appendChild(poly);
        g.appendChild(text);
        return text as unknown as SVGTextElement;
    }

    it('dark (broken theme now fixed): black label on an unfilled node -> white', () => {
        const text = makeNode('#000000');
        const poly = text.parentElement!.querySelector('polygon')!;
        retintUnfilledNodeTextForDark(poly);
        expect(text.getAttribute('fill')).toBe('#ffffff');
    });

    it('parity: an already-light author label is NOT clobbered', () => {
        const text = makeNode('#f5f5f5');
        const poly = text.parentElement!.querySelector('polygon')!;
        retintUnfilledNodeTextForDark(poly);
        expect(text.getAttribute('fill')).toBe('#f5f5f5');
    });
});
