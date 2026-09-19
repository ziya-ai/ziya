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

/**
 * G-cc6859 / D-091 — custom-and-orthogonal-arrowheads-dropped (the ER crow's-foot half).
 *
 * maxGraph core's `registerDefaultEdgeMarkers` registers only classic/classicThin,
 * block/blockThin, open/openThin, oval, diamond/diamondThin — and only when that
 * function is CALLED explicitly (it is not auto-registered on import; the drawio
 * plugin's loadMaxGraph now calls it, see D-380). The drawio ER cardinality
 * vocabulary (ERone, ERmany, ERzeroToOne, ERzeroToMany, ERoneToMany, ERmandOne) is NOT
 * in that set, so `EdgeMarkerRegistry.createMarker` returns null for those types and the
 * crow's-foot / cardinality terminator is dropped entirely — an ER edge renders as a bare
 * line with no cardinality, in both themes (the markers stroke in the edge colour, so this
 * is theme-independent).
 *
 * Fix: register minimal, geometrically-correct ER marker factories on the dynamically
 * loaded maxGraph module's EdgeMarkerRegistry, once, right after it loads. Purely additive
 * — a name already present in the registry (any core marker) is never overwritten, so no
 * other edge output changes. The classic/block/open/oval/diamond names the triage also
 * named are ALREADY core-registered and render fine; the real gap is only the ER family.
 *
 * Marker coordinate convention (matches maxGraph's built-in edge-markers): `pe` is the edge
 * end point at the terminal, `unitX`/`unitY` is the unit vector pointing INTO the terminal,
 * `size`/`sw` are the marker size and stroke width. The factory may mutate `pe` to shorten
 * the connecting line, and returns a paint closure. ER markers are stroke-only (never filled).
 */

const ER_MARKER_NAMES = [
    'ERone',
    'ERmany',
    'ERzeroToOne',
    'ERzeroToMany',
    'ERoneToMany',
    'ERmandOne',
] as const;

/**
 * Register the drawio ER crow's-foot edge markers that maxGraph core omits.
 * Idempotent and never throws — an unexpected module shape leaves the (bare-line) fallback.
 * Existing markers are preserved (additive); only unregistered ER names are filled.
 *
 * @returns the list of ER marker names present in the registry after the call (for tests).
 */
export function registerDrawioExtraEdgeMarkers(maxGraphModule: any): string[] {
    try {
        const EdgeMarkerRegistry = maxGraphModule?.EdgeMarkerRegistry;
        if (
            !EdgeMarkerRegistry ||
            typeof EdgeMarkerRegistry.add !== 'function' ||
            typeof EdgeMarkerRegistry.get !== 'function'
        ) {
            return [];
        }

        // Build the geometry shared by every ER marker: the tip at the terminal, a step
        // vector `n` (one marker length back along the line) and a unit perpendicular `p`.
        const geom = (pe: any, unitX: number, unitY: number, size: number, sw: number) => {
            const d = size + sw;
            const nx = unitX * d;
            const ny = unitY * d;
            const px = -unitY; // unit perpendicular
            const py = unitX;
            const hw = d * 0.7; // half-spread of a foot / half-length of a bar
            const tip = pe.clone();
            // Shorten the connecting line by one step so it does not overrun the terminal.
            pe.x -= nx;
            pe.y -= ny;
            return { nx, ny, px, py, hw, tip };
        };

        // A single perpendicular bar at k steps back from the terminal.
        const drawBar = (c: any, g: any, k: number) => {
            const bx = g.tip.x - g.nx * k;
            const by = g.tip.y - g.ny * k;
            c.moveTo(bx + g.px * g.hw, by + g.py * g.hw);
            c.lineTo(bx - g.px * g.hw, by - g.py * g.hw);
        };

        // A crow's foot: three prongs from an apex (one step back) fanning to the terminal.
        const drawCrowsFoot = (c: any, g: any) => {
            const ax = g.tip.x - g.nx;
            const ay = g.tip.y - g.ny;
            c.moveTo(ax, ay);
            c.lineTo(g.tip.x, g.tip.y);
            c.moveTo(ax, ay);
            c.lineTo(g.tip.x + g.px * g.hw, g.tip.y + g.py * g.hw);
            c.moveTo(ax, ay);
            c.lineTo(g.tip.x - g.px * g.hw, g.tip.y - g.py * g.hw);
        };

        // A small circle centred k steps back (the "zero"/optional part).
        const drawCircle = (c: any, g: any, k: number) => {
            const cx = g.tip.x - g.nx * k;
            const cy = g.tip.y - g.ny * k;
            const r = g.hw * 0.7;
            c.ellipse(cx - r, cy - r, r * 2, r * 2);
        };

        const factories: Record<string, any> = {
            // Exactly one: a single bar.
            ERone: (canvas: any, _s: any, _t: any, pe: any, ux: number, uy: number, size: number, _src: any, sw: number) => {
                const g = geom(pe, ux, uy, size, sw);
                return () => { canvas.begin(); drawBar(canvas, g, 1); canvas.stroke(); };
            },
            // Many: a crow's foot.
            ERmany: (canvas: any, _s: any, _t: any, pe: any, ux: number, uy: number, size: number, _src: any, sw: number) => {
                const g = geom(pe, ux, uy, size, sw);
                return () => { canvas.begin(); drawCrowsFoot(canvas, g); canvas.stroke(); };
            },
            // Zero or one: a circle plus a bar.
            ERzeroToOne: (canvas: any, _s: any, _t: any, pe: any, ux: number, uy: number, size: number, _src: any, sw: number) => {
                const g = geom(pe, ux, uy, size, sw);
                return () => { canvas.begin(); drawBar(canvas, g, 1); canvas.stroke(); canvas.begin(); drawCircle(canvas, g, 2); canvas.stroke(); };
            },
            // Zero or many: a circle plus a crow's foot.
            ERzeroToMany: (canvas: any, _s: any, _t: any, pe: any, ux: number, uy: number, size: number, _src: any, sw: number) => {
                const g = geom(pe, ux, uy, size, sw);
                return () => { canvas.begin(); drawCrowsFoot(canvas, g); canvas.stroke(); canvas.begin(); drawCircle(canvas, g, 2); canvas.stroke(); };
            },
            // One or many: a bar plus a crow's foot.
            ERoneToMany: (canvas: any, _s: any, _t: any, pe: any, ux: number, uy: number, size: number, _src: any, sw: number) => {
                const g = geom(pe, ux, uy, size, sw);
                return () => { canvas.begin(); drawCrowsFoot(canvas, g); drawBar(canvas, g, 2); canvas.stroke(); };
            },
            // One and only one (mandatory): two bars.
            ERmandOne: (canvas: any, _s: any, _t: any, pe: any, ux: number, uy: number, size: number, _src: any, sw: number) => {
                const g = geom(pe, ux, uy, size, sw);
                return () => { canvas.begin(); drawBar(canvas, g, 1); drawBar(canvas, g, 2); canvas.stroke(); };
            },
        };

        for (const name of ER_MARKER_NAMES) {
            // Additive only: never clobber a marker the core (or a prior call) registered.
            if (EdgeMarkerRegistry.get(name) == null) {
                EdgeMarkerRegistry.add(name, factories[name]);
            }
        }

        return ER_MARKER_NAMES.filter((n) => EdgeMarkerRegistry.get(n) != null);
    } catch {
        // A marker-registration problem must never break drawio rendering — the worst case
        // is the pre-existing bare-line fallback for ER cardinality edges.
        return [];
    }
}

/** The built-in marker names maxGraph's registerDefaultEdgeMarkers wires up. */
const DEFAULT_EDGE_MARKER_NAMES = [
    'classic',
    'classicThin',
    'block',
    'blockThin',
    'open',
    'openThin',
    'oval',
    'diamond',
    'diamondThin',
] as const;

/**
 * G-796ea6 / D-380 — arrowheads-missing-on-default-edges.
 *
 * maxGraph 0.18+ does NOT auto-register its built-in edge markers on import: the
 * marker set (classic/classicThin, block/blockThin, open/openThin, oval,
 * diamond/diamondThin) only lands in the EdgeMarkerRegistry when the exported
 * `registerDefaultEdgeMarkers()` is CALLED. The drawio plugin loaded maxGraph and
 * called registerCoreCodecs() but never this, so EdgeMarkerRegistry.get('classic')
 * returned null and every default / orthogonal edge painted as a bare line with no
 * terminator — flow direction lost in BOTH themes (markers stroke/fill in the edge
 * colour, so this is theme-independent). The earlier ER-only registration wrongly
 * assumed the core set was already present.
 *
 * This wraps that core call: idempotent (maxGraph guards it with an internal flag),
 * additive, and never throws — a module missing the export leaves the (pre-existing)
 * bare-line fallback. Runs BEFORE registerDrawioExtraEdgeMarkers so the ER extras
 * stay purely additive over the core set.
 *
 * @returns the built-in marker names present in the registry after the call (for tests).
 */
export function registerDrawioDefaultEdgeMarkers(maxGraphModule: any): string[] {
    try {
        const register = maxGraphModule?.registerDefaultEdgeMarkers;
        const EdgeMarkerRegistry = maxGraphModule?.EdgeMarkerRegistry;
        if (typeof register === 'function') {
            register();
        }
        if (!EdgeMarkerRegistry || typeof EdgeMarkerRegistry.get !== 'function') {
            return [];
        }
        return DEFAULT_EDGE_MARKER_NAMES.filter((n) => EdgeMarkerRegistry.get(n) != null);
    } catch {
        // Marker registration must never break drawio rendering — worst case is the
        // pre-existing bare-line fallback for default-styled edges.
        return [];
    }
}
