/**
 * G-67dceb — chartTheme fixes for band-axis tick fitting and functional-colour
 * parsing.
 *
 * D-444: classifyColor() rejected percentage / CSS4 space-separated rgb() so a
 *        percent-notation fill fell back to the default teal (wrong colour).
 * D-371: leading-only truncation collapsed long shared-prefix category names to
 *        an identical string, destroying the categorical axis.
 * D-372: rotated tick pruning used a fixed "one glyph per slot" stride that left
 *        dense numeric labels interleaving glyph-on-glyph.
 *
 * Each expectation below FAILS against the pre-fix code and passes after it.
 */
import {
    classifyColor,
    truncateLabel,
    truncateLabelMiddle,
    planBandLabels,
} from '../chartTheme';

describe('D-444 — classifyColor honours percent & space-separated rgb()', () => {
    it('parses integer rgb() (unchanged)', () => {
        expect(classifyColor('rgb(255, 0, 0)')).toEqual({ hex: '#ff0000' });
    });

    it('parses percentage channels instead of dropping to null', () => {
        // 60%,35%,65% -> round(153/89.25/165.75) = 153,89,166 = #9959a6
        const c = classifyColor('rgb(60%,35%,65%)');
        expect(c).not.toBeNull();
        expect(c!.hex).toBe('#9959a6');
    });

    it('parses CSS4 space-separated form with slash alpha', () => {
        expect(classifyColor('rgb(153 90 166)')).toEqual({ hex: '#995aa6' });
        expect(classifyColor('rgb(153 90 166 / 0.8)')).toEqual({ hex: '#995aa6' });
    });

    it('still rejects a fully transparent alpha and genuine garbage', () => {
        expect(classifyColor('rgba(10, 20, 30, 0)')).toBeNull();
        expect(classifyColor('rgb(not a color)')).toBeNull();
    });
});

describe('D-371 — middle truncation keeps the distinguishing suffix', () => {
    const s1 = 'service-authentication-token-refresh-worker-00';
    const s2 = 'service-authentication-token-refresh-worker-04';

    it('documents the leading-truncation collapse (pre-fix behaviour)', () => {
        // Two categories that differ only in their -NN suffix collapse to the
        // same leading-truncated string — this is the defect.
        expect(truncateLabel(s1, 16)).toBe(truncateLabel(s2, 16));
    });

    it('middle truncation keeps both distinct and within the cap', () => {
        const t1 = truncateLabelMiddle(s1, 16);
        const t2 = truncateLabelMiddle(s2, 16);
        expect(t1).not.toBe(t2);
        expect(t1.endsWith('00')).toBe(true);
        expect(t2.endsWith('04')).toBe(true);
        expect(t1.length).toBeLessThanOrEqual(16);
    });

    it('leaves short labels untouched', () => {
        expect(truncateLabelMiddle('118', 16)).toBe('118');
    });
});

describe('D-372 — rotated tick stride separates the diagonal baselines', () => {
    const lineHeight = 11 * 1.2; // fontSize 11 -> 13.2px line height

    it('120 dense numeric labels keep a readable perpendicular gap', () => {
        const labels = Array.from({ length: 120 }, (_, i) => String(i)); // width 700 - 60 gutter
        const plan = planBandLabels(labels, 640, 11, 30);
        expect(plan.rotate).toBe(true);
        const slot = 640 / 120;
        const perpGap = slot * plan.keepEvery * Math.SQRT1_2;
        // Pre-fix stride left ~7.5px here; the fix must clear the 13.2px line height.
        expect(perpGap).toBeGreaterThanOrEqual(lineHeight);
        // and still leave a useful number of labels (not over-thinned)
        const kept = Math.ceil(120 / plan.keepEvery);
        expect(kept).toBeGreaterThanOrEqual(12);
    });

    it('200 dense labels also clear the line height', () => {
        const labels = Array.from({ length: 200 }, (_, i) => String(i)); // width 900 - 60
        const plan = planBandLabels(labels, 840, 11, 30);
        expect(plan.rotate).toBe(true);
        const perpGap = (840 / 200) * plan.keepEvery * Math.SQRT1_2;
        expect(perpGap).toBeGreaterThanOrEqual(lineHeight);
    });

    it('reserves left margin for the leftmost rotated label', () => {
        const labels = Array.from({ length: 40 }, (_, i) => `service-name-longtail-${i}`);
        const plan = planBandLabels(labels, 740, 11, 30);
        expect(plan.rotate).toBe(true);
        expect(plan.reservedLeft || 0).toBeGreaterThan(40); // default left gutter
    });
});
