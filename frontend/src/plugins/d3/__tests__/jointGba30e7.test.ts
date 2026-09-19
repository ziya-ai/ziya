/**
 * @jest-environment jsdom
 *
 * G-ba30e7 — jointPlugin fit-path + shape/theme regressions (all engine=joint).
 *
 * D-404 viewbox-fit-clip: computeJointFitPlan's fit branch returned
 *   paperWidth = containerWidth regardless of content width, so a graph narrower
 *   than the container got a wider-than-tall paper while the viewBox stayed
 *   narrow. preserveAspectRatio 'meet' then letterboxed with empty side/top bands
 *   and pushed nodes to the frame edge. The paper must carry the SAME aspect ratio
 *   as the content bbox the viewBox frames.
 *
 * D-405 fit-plan-downscale-to-illegible: the oversized branch scaled a
 *   ~10580px-wide fan-out to ~0.06x with no floor, collapsing the paper to a
 *   ~12px strip with sub-pixel labels (visually blank). The scale must clamp at a
 *   legibility floor (JOINT_MIN_FIT_SCALE).
 *
 * D-406 cloud-ellipse-collapsed-by-rx-ry: getNetworkElementAttrs emitted
 *   rx:15/ry:15 on the cloud body, but since the cloud is a shapes.standard.Ellipse
 *   those attrs ARE the ellipse radii, collapsing the 120x80 ellipse to a ~30px
 *   circle. Corner radii must only be emitted on rect bodies.
 *
 * D-415 cylinder-label-low-contrast (THEME): createCylinderElement hardcoded the
 *   label fill to #ffffff (light, on body #3498db = 3.15) / #eceff4 (dark, on
 *   #5e81ac = 3.50), below the 4.5 text floor in BOTH themes. It must resolve the
 *   label colour from the body fill via readableJointLabelFill.
 *
 * Direction is asserted explicitly: each check would FAIL against the pre-fix
 * source (paperWidth==container, no scale floor, rx/ry on the cloud ellipse,
 * hardcoded #ffffff/#eceff4 cylinder label) and PASSES with the fix.
 */
import {
    computeJointFitPlan,
    JOINT_MIN_FIT_SCALE,
    getNetworkElementAttrs,
    createCylinderElement,
    jointContrastRatio,
    jointPlugin,
} from '../jointPlugin';

const aspect = (w: number, h: number) => w / h;

describe('D-404 — fit plan preserves the content-bbox aspect (no letterbox band)', () => {
    it('narrow content in the FIT branch takes its natural width, not the container width', () => {
        // content 300x900 into a 640-wide, 2000-tall capture box: fits, so scale 1.
        const plan = computeJointFitPlan(300, 900, 640, 2000);
        expect(plan.scaled).toBe(false);
        expect(plan.scale).toBe(1);
        // Pre-fix returned paperWidth = 640 (container) -> aspect 640/900 != 300/900.
        expect(plan.paperWidth).toBe(300);
        expect(plan.paperHeight).toBe(900);
        // Paper aspect equals content aspect -> preserveAspectRatio 'meet' cannot band.
        expect(aspect(plan.paperWidth, plan.paperHeight)).toBeCloseTo(aspect(300, 900), 5);
    });

    it('content already filling the container is byte-unchanged (aspect preserved)', () => {
        const plan = computeJointFitPlan(640, 480, 640, 2000);
        expect(plan.paperWidth).toBe(640);
        expect(plan.paperHeight).toBe(480);
    });

    it('oversized content keeps the content aspect after downscaling', () => {
        const plan = computeJointFitPlan(2000, 1180, 640, 2000);
        expect(plan.scaled).toBe(true);
        expect(aspect(plan.paperWidth, plan.paperHeight)).toBeCloseTo(aspect(2000, 1180), 2);
    });
});

describe('D-405 — fit plan clamps the downscale at a legibility floor', () => {
    it('a ~10580px-wide fan-out is not shrunk below JOINT_MIN_FIT_SCALE', () => {
        const plan = computeJointFitPlan(10580, 200, 640, 2000);
        // Pre-fix raw scale = min(640/10580, 2000/200) = ~0.0605 -> blank strip.
        expect(plan.scale).toBeGreaterThanOrEqual(JOINT_MIN_FIT_SCALE);
        // Paper stays legibly tall rather than collapsing to a ~12px strip.
        expect(plan.paperHeight).toBeGreaterThanOrEqual(Math.round(200 * JOINT_MIN_FIT_SCALE));
        // Aspect is still preserved so nothing is squashed.
        expect(aspect(plan.paperWidth, plan.paperHeight)).toBeCloseTo(aspect(10580, 200), 2);
    });

    it('a moderately oversized graph still uses its natural fit scale (floor not over-applied)', () => {
        const plan = computeJointFitPlan(2000, 1180, 640, 2000);
        // raw scale here (~0.32) is above the floor, so it is used unchanged.
        expect(plan.scale).toBeCloseTo(Math.min(640 / 2000, 2000 / 1180), 5);
        expect(plan.scale).toBeGreaterThan(JOINT_MIN_FIT_SCALE);
    });
});

describe('D-406 — cloud (ellipse body) gets NO corner radii; rects still do', () => {
    for (const theme of ['light', 'dark'] as const) {
        it(`cloud (${theme}) body has no rx/ry`, () => {
            const attrs = getNetworkElementAttrs('cloud', theme);
            expect(attrs.body.rx).toBeUndefined();
            expect(attrs.body.ry).toBeUndefined();
        });
        it(`router (${theme}) rect body keeps rounded corners`, () => {
            const attrs = getNetworkElementAttrs('router', theme);
            expect(attrs.body.rx).toBe(5);
            expect(attrs.body.ry).toBe(5);
        });
    }
});

describe('D-415 — cylinder label resolves from the body fill and clears 4.5 in BOTH themes', () => {
    // Body fill the cylinder label sits on, per createCylinderElement.
    const BODY = { light: '#3498db', dark: '#5e81ac' };

    // Stub the joint runtime so createCylinderElement can build without a real paper.
    let savedDeps: any;
    beforeAll(() => {
        savedDeps = (globalThis as any).__jointRuntimeDeps;
        class FakeElement {
            cfg: any;
            constructor(cfg: any) { this.cfg = cfg; }
        }
        (globalThis as any).__jointRuntimeDeps = { dia: { Element: FakeElement } };
    });
    afterAll(() => { (globalThis as any).__jointRuntimeDeps = savedDeps; });

    for (const theme of ['light', 'dark'] as const) {
        it(`cylinder (${theme}) label clears the 4.5 text floor on its body`, () => {
            const el: any = createCylinderElement({ id: 'db', text: 'Database' } as any, theme);
            const labelFill = el.cfg.attrs.label.fill;
            expect(jointContrastRatio(BODY[theme], labelFill)).toBeGreaterThanOrEqual(4.5);
            // Pin the direction: the pre-fix hardcoded fills FAILED the floor.
            const preFix = theme === 'dark' ? '#eceff4' : '#ffffff';
            expect(jointContrastRatio(BODY[theme], preFix)).toBeLessThan(4.5);
        });
    }
});

describe('D-409 — empty spec paints a MARKED error card (no 30s timeout)', () => {
    for (const isDarkMode of [false, true] as const) {
        it(`an empty elements array tags data-diagram-error (${isDarkMode ? 'dark' : 'light'})`, async () => {
            const container = document.createElement('div');
            document.body.appendChild(container);
            // Empty structured spec -> render throws 'No elements found' internally.
            await jointPlugin.render(
                container, null as any,
                { type: 'joint', elements: [] } as any,
                isDarkMode,
            );
            // Pre-fix: the joint error card was a bare <div class="joint-error"> with
            // no svg/canvas/img and NO marker, so DiagramRenderPage never got a
            // readiness signal and spun to its 30s cap returning svg:0.
            const card = container.querySelector('[data-diagram-error]');
            expect(card).not.toBeNull();
            expect(card!.getAttribute('data-diagram-error')!.length).toBeGreaterThan(0);
            document.body.removeChild(container);
        });
    }
});
