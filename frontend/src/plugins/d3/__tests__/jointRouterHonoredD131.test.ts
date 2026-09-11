/**
 * D-131 (G-081f1f) — joint link routers must actually be honoured.
 *
 * signature: link-overdraw-no-label-background. The label-background (D-148 ref:'text')
 * and edge z-order (D-147 z:-1) sub-issues were already repaired under G-47. The
 * remaining, previously-unaddressed sub-issue is router EFFECTIVENESS: createEnhancedLink
 * and createLink declared a per-link
 *     connectionStrategy: (end, view, magnet, coords) => view.model.getBBox().center()
 * which force-snaps BOTH endpoints onto the element bbox centre. `connectionStrategy` is a
 * dia.Paper option (inert on a link MODEL at best; a route-collapser if a version honours
 * it), and it directly contradicts the orthogonal/manhattan/metro router the w1-09 spec
 * requests — every link would render as a straight centre-to-centre segment regardless of
 * `router`. The fix removes that closure; endpoint placement is already handled correctly
 * by the modelCenter anchor + boundary connectionPoint on source/target.
 *
 * Direction of the source-pinned check: the pre-fix source contained the centre-snapping
 * connectionStrategy, so the assertion FAILS against unpatched code and PASSES post-fix.
 * The helper checks are the standing invariant that the spec's router names survive
 * normalisation (a downgrade to 'normal' would itself erase the router effect).
 */

import * as fs from 'fs';
import * as path from 'path';
import { sanitizeRouter, KNOWN_JOINT_ROUTERS } from '../jointLinkRouting';

describe('D-131 — spec router names are honoured (not downgraded)', () => {
    // joint-w1-09 crosses these routers; if any were unknown to the sanitizer it would
    // silently become 'normal' and the router would have no visible effect.
    it.each(['orthogonal', 'manhattan', 'metro', 'normal'])(
        'sanitizeRouter preserves the "%s" router name',
        (name) => {
            expect(KNOWN_JOINT_ROUTERS.has(name)).toBe(true);
            const out = sanitizeRouter(name, 'normal', { padding: 20 });
            expect(out.name).toBe(name);
        }
    );

    it('preserves the router name given in object shape too', () => {
        const out = sanitizeRouter({ name: 'manhattan' }, 'normal');
        expect(out.name).toBe('manhattan');
    });
});

describe('D-131 — links do not force endpoints to the bbox centre (source-pinned)', () => {
    const src = fs.readFileSync(
        path.join(__dirname, '..', 'jointPlugin.ts'),
        'utf8'
    );

    it('the link factories no longer declare a centre-snapping connectionStrategy', () => {
        // Pre-fix both createEnhancedLink and createLink carried:
        //   connectionStrategy: (end, view, magnet, coords) => view.model.getBBox().center()
        // which collapsed every route onto the centre-to-centre line and defeated the router.
        // Match the actual code form (arrow-fn strategy that returns the model bbox
        // centre), not prose — the explanatory comment above the removal legitimately
        // names the pattern it removed.
        expect(src).not.toMatch(/connectionStrategy\s*:\s*\(/);
        expect(src).not.toMatch(/view\.model\.getBBox\(\)\.center\(\)/);
    });

    it('still sanitizes router/connector on both link factories (router path intact)', () => {
        // Removing the strategy must not disturb the router/connector normalisation that
        // keeps the requested router in play.
        expect((src.match(/sanitizeRouter\(linkSpec\.router/g) || []).length).toBeGreaterThanOrEqual(2);
        expect((src.match(/sanitizeConnector\(linkSpec\.connector/g) || []).length).toBeGreaterThanOrEqual(2);
    });
});
