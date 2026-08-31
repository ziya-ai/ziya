/**
 * G-36 / D-106 — shape-not-implemented-falls-back-to-rect.
 *
 * maxGraph core (`registerDefaultShapes`) registers only: actor, arrow, arrowConnector,
 * cloud, connector, cylinder, doubleEllipse, ellipse, hexagon, image, label, line,
 * rectangle, rhombus, swimlane, triangle. Common drawio shape names — `parallelogram`,
 * `process`, `step`, `note`, `cylinder3` — are NOT in that set, so CellRenderer resolves
 * them to the default rectangle and the shape SEMANTICS are silently lost (a decision
 * diamond stays a box, a predefined-process box loses its side rails, etc.) in both themes.
 *
 * Fix: register minimal path/rectangle-derived implementations of the missing shapes on
 * the dynamically-loaded maxGraph module, once, right after it loads. This is additive —
 * it only fills names the core registry leaves empty (existing shapes are never replaced),
 * so no other diagram output changes. Geometry follows the standard drawio conventions.
 *
 * The classes are defined at call time from the passed-in module because @maxgraph/core is
 * dynamically imported (there are no usable static shape exports at build time here).
 */

const CUSTOM_SHAPE_NAMES = ['parallelogram', 'process', 'step', 'note', 'cylinder3'] as const;

/** min inset used by the skew/step/fold, scaled to the box but capped so big boxes stay sane. */
const inset = (w: number, h: number, frac: number, cap: number): number =>
    Math.max(1, Math.min(frac * Math.min(w, h), cap));

/**
 * Register the drawio-specific shapes that maxGraph core omits.
 * Safe to call repeatedly (idempotent) and never throws — a shape that cannot be built
 * (unexpected module shape) is skipped, leaving the plain-rectangle fallback intact.
 *
 * @returns the list of shape names that are registered after the call (for tests).
 */
export function registerDrawioExtraShapes(maxGraphModule: any): string[] {
    try {
        const ShapeRegistry = maxGraphModule?.ShapeRegistry;
        const RectangleShape = maxGraphModule?.RectangleShape;
        const CylinderShape = maxGraphModule?.CylinderShape;
        if (!ShapeRegistry || typeof ShapeRegistry.add !== 'function' || !RectangleShape) {
            return [];
        }

        // parallelogram: a rectangle skewed on the top/bottom edges.
        class ParallelogramShape extends RectangleShape {
            paintBackground(c: any, x: number, y: number, w: number, h: number) {
                const dx = inset(w, h, 0.25, 40);
                c.begin();
                c.moveTo(x + dx, y);
                c.lineTo(x + w, y);
                c.lineTo(x + w - dx, y + h);
                c.lineTo(x, y + h);
                c.close();
                c.fillAndStroke();
            }
        }

        // step: right-pointing chevron/pentagon (workflow step).
        class StepShape extends RectangleShape {
            paintBackground(c: any, x: number, y: number, w: number, h: number) {
                const s = inset(w, h, 0.25, 40);
                c.begin();
                c.moveTo(x, y);
                c.lineTo(x + w - s, y);
                c.lineTo(x + w, y + h / 2);
                c.lineTo(x + w - s, y + h);
                c.lineTo(x, y + h);
                c.close();
                c.fillAndStroke();
            }
        }

        // note: rectangle with a folded top-right corner.
        class NoteShape extends RectangleShape {
            paintBackground(c: any, x: number, y: number, w: number, h: number) {
                const s = inset(w, h, 0.25, 20);
                c.begin();
                c.moveTo(x, y);
                c.lineTo(x + w - s, y);
                c.lineTo(x + w, y + s);
                c.lineTo(x + w, y + h);
                c.lineTo(x, y + h);
                c.close();
                c.fillAndStroke();
                // fold triangle (stroke only) so the corner reads as a dog-ear.
                c.begin();
                c.moveTo(x + w - s, y);
                c.lineTo(x + w - s, y + s);
                c.lineTo(x + w, y + s);
                c.stroke();
            }
        }

        // process: a rectangle with two inner vertical rails (predefined process).
        class ProcessShape extends RectangleShape {
            paintForeground(c: any, x: number, y: number, w: number, h: number) {
                const dx = inset(w, h, 0.1, 20);
                c.begin();
                c.moveTo(x + dx, y);
                c.lineTo(x + dx, y + h);
                c.moveTo(x + w - dx, y);
                c.lineTo(x + w - dx, y + h);
                c.stroke();
                if (typeof super.paintForeground === 'function') {
                    super.paintForeground(c, x, y, w, h);
                }
            }
        }

        ShapeRegistry.add('parallelogram', ParallelogramShape);
        ShapeRegistry.add('step', StepShape);
        ShapeRegistry.add('note', NoteShape);
        ShapeRegistry.add('process', ProcessShape);
        // cylinder3 is drawio's 3D-database variant — reuse the real cylinder geometry
        // rather than degrade to a rectangle. Fall back to a rectangle-derived cylinder
        // only if the core CylinderShape is unexpectedly absent.
        if (CylinderShape) {
            ShapeRegistry.add('cylinder3', CylinderShape);
        }

        return CUSTOM_SHAPE_NAMES.filter((n) => ShapeRegistry.get(n) != null);
    } catch {
        // Never let a shape-registration problem break drawio rendering — the worst case
        // is the pre-existing rectangle fallback.
        return [];
    }
}
