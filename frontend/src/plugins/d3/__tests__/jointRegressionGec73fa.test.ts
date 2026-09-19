/**
 * Regression guard for fix-group G-ec73fa (jointPlugin.ts).
 *
 * D-128/D-129/D-134/D-135/D-136/D-144 were each fixed, verified, then
 * REPEATEDLY reappeared as regressions. Their per-helper tests
 * (jointG17, jointG48, jointGb8516d) each passed in isolation, so nothing
 * asserted the whole cluster together: a silent reversion of any single
 * helper could slip through and only surface as a render-time regression.
 *
 * This suite locks all six fixes at unit level in ONE place. Each assertion
 * fails if its fix is reverted:
 *   - D-128: isValidCellType / fallbackCellType (typeless custom cells kept)
 *   - D-129: computeJointFitPlan (oversized content scaled, not cropped)
 *   - D-134: jointPortSide (string port position -> valid side group)
 *   - D-135: networkElementStyle (distinct fills per network type)
 *   - D-136: jointContainerFill (per-depth dark ramp, null in light)
 *   - D-144: coerceJointBoolean ('false'/'0' -> false, manual layout kept)
 *
 * Structural fixes are theme-independent; the theme-sensitive ones
 * (D-135 fills, D-136 ramp) are asserted in BOTH themes.
 */
import {
    isValidCellType,
    fallbackCellType,
    computeJointFitPlan,
    JOINT_MAX_RENDER_HEIGHT,
    jointPortSide,
    networkElementStyle,
    jointContainerFill,
    coerceJointBoolean,
} from '../jointPlugin';

describe('G-ec73fa joint regression cluster', () => {
    // ── D-128: typeless custom cells must gain a valid string type ──────────
    describe('D-128 cell-type guard', () => {
        it('rejects a missing/empty/non-string type', () => {
            expect(isValidCellType(undefined)).toBe(false);
            expect(isValidCellType(null)).toBe(false);
            expect(isValidCellType('')).toBe(false);
            expect(isValidCellType(42)).toBe(false);
        });
        it('accepts a real cell type', () => {
            expect(isValidCellType('standard.Rectangle')).toBe(true);
        });
        it('produces a stable namespaced fallback for a bare creator', () => {
            expect(fallbackCellType('cylinder')).toBe('custom.cylinder');
            // must still be a non-empty string for an unusable shapeType
            expect(isValidCellType(fallbackCellType(undefined))).toBe(true);
            expect(isValidCellType(fallbackCellType(''))).toBe(true);
        });
    });

    // ── D-129: oversized content is scaled to fit, never cropped ────────────
    describe('D-129 scale-to-fit', () => {
        it('keeps natural size when content fits the capture box', () => {
            const p = computeJointFitPlan(800, 600, 1264, JOINT_MAX_RENDER_HEIGHT);
            expect(p.scale).toBe(1);
            expect(p.scaled).toBe(false);
        });
        it('downscales (does not crop) content wider than the container', () => {
            const contentW = 5000;
            const p = computeJointFitPlan(contentW, 1000, 1264, JOINT_MAX_RENDER_HEIGHT);
            expect(p.scaled).toBe(true);
            expect(p.scale).toBeLessThan(1);
            // paper must not exceed the capture box in either dimension:
            // that is the difference between fitting and clipping.
            expect(p.paperWidth).toBeLessThanOrEqual(1264);
            expect(p.paperHeight).toBeLessThanOrEqual(JOINT_MAX_RENDER_HEIGHT);
        });
        it('downscales content taller than the max render height', () => {
            const p = computeJointFitPlan(1000, 6000, 1264, JOINT_MAX_RENDER_HEIGHT);
            expect(p.scaled).toBe(true);
            expect(p.paperHeight).toBeLessThanOrEqual(JOINT_MAX_RENDER_HEIGHT);
        });
    });

    // ── D-134: string port position maps to a built-in side group ───────────
    describe('D-134 port position string', () => {
        it('maps documented side strings to themselves', () => {
            expect(jointPortSide('left')).toBe('left');
            expect(jointPortSide('right')).toBe('right');
            expect(jointPortSide('top')).toBe('top');
            expect(jointPortSide('bottom')).toBe('bottom');
        });
        it('always returns a valid side (never a raw arbitrary string that throws layoutCallback)', () => {
            const valid = ['top', 'bottom', 'left', 'right'];
            expect(valid).toContain(jointPortSide('input'));
            expect(valid).toContain(jointPortSide(undefined));
            expect(valid).toContain(jointPortSide({}));
        });
    });

    // ── D-135: distinct geometry/fill per network type, both themes ─────────
    describe('D-135 network shape differentiation', () => {
        (['light', 'dark'] as const).forEach((theme) => {
            it(`gives router/switch/server/firewall distinct fills in ${theme}`, () => {
                const fills = ['router', 'switch', 'server', 'firewall'].map(
                    (t) => networkElementStyle(t, theme).fill
                );
                const unique = new Set(fills);
                // If every type collapsed to one plain rectangle fill, the
                // topology is unreadable — that is the D-135 defect.
                expect(unique.size).toBeGreaterThan(1);
            });
        });
    });

    // ── D-136: dark per-depth container ramp; light has none ────────────────
    describe('D-136 nested container fill ramp', () => {
        it('returns null in light (crisp strokes on white, no ramp)', () => {
            expect(jointContainerFill(0, 'light')).toBeNull();
            expect(jointContainerFill(3, 'light')).toBeNull();
        });
        it('distinguishes adjacent depths in dark (not one flat slab)', () => {
            const d0 = jointContainerFill(0, 'dark');
            const d1 = jointContainerFill(1, 'dark');
            const d2 = jointContainerFill(2, 'dark');
            expect(d0).toBeTruthy();
            expect(d1).toBeTruthy();
            // the old bug shared #4c566a at every depth (contrast 1.00)
            expect(d0).not.toBe(d1);
            expect(d1).not.toBe(d2);
        });
    });

    // ── D-144: string booleans coerced so manual layout survives ────────────
    describe('D-144 boolean coercion', () => {
        it("coerces 'false'/'0' to false (autoLayout:'false' must not run auto-layout)", () => {
            expect(coerceJointBoolean('false')).toBe(false);
            expect(coerceJointBoolean('0')).toBe(false);
            expect(coerceJointBoolean('False')).toBe(false);
        });
        it("coerces 'true'/'1' to true", () => {
            expect(coerceJointBoolean('true')).toBe(true);
            expect(coerceJointBoolean('1')).toBe(true);
        });
        it('passes through real booleans and non-boolean strings untouched', () => {
            expect(coerceJointBoolean(true)).toBe(true);
            expect(coerceJointBoolean(false)).toBe(false);
            expect(coerceJointBoolean('maybe')).toBe('maybe');
        });
    });
});
