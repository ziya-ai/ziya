/**
 * Automatic UI zoom for narrow viewports.
 *
 * The layout has a usable-width floor around 1300 CSS px.  Viewports below
 * it are not low-resolution screens — they come from aggressive OS display
 * scaling (macOS "Larger Text" presents a 2560-px panel as 1024 CSS px;
 * Windows 150% turns 1080p into 1280) or a browser window tiled to half a
 * screen.  Browser page zoom (Cmd-minus) is not scriptable, so we apply the
 * visually equivalent CSS `zoom` on <body>, snapped to browser-like steps.
 * The 1300 constant is the layout width one such user settled on after
 * zooming out to the first comfortable step (80% on a 1024-px viewport).
 *
 * Two spec consequences (css-viewport §4) the rest of the app must honour:
 *
 *  - `zoom` pre-multiplies every <length>, INCLUDING vh/vw, but not
 *    percentages.  `body { height: 100vh; zoom: .8 }` renders 80% tall.
 *    Structural sites use `--viewport-height` / `--viewport-width`
 *    (defined in index.css as 100vh|vw divided by --ui-zoom) or percentages.
 *  - Pointer coordinates (clientX/Y) and window.innerWidth/Height stay in
 *    UNZOOMED viewport px while layout runs in zoomed CSS px.  Anything that
 *    turns a pointer position or the window size into a CSS length must go
 *    through toLayoutPx() / layoutViewportWidth().  getBoundingClientRect()
 *    is already in viewport px, so (clientX - rect.left) is a viewport
 *    delta and needs the same conversion.
 *
 * The factor is decided once at boot and not re-evaluated on resize.  The
 * heuristic keys on window.innerWidth, which already reflects any browser
 * zoom the user has persisted for the origin, so a user who has set 80%
 * themselves reads as wide enough and is not zoomed twice.  A user's own
 * Cmd-minus/plus composes multiplicatively for the session.
 *
 * Debugging: localStorage ZIYA_UI_ZOOM = "off" disables the heuristic;
 * a number (0.3–3) forces that factor.  Headless export routes are exempt.
 */

export const UI_ZOOM_TARGET_WIDTH = 1300;
export const UI_ZOOM_STEPS: readonly number[] = [1, 0.9, 0.8, 0.67];
export const UI_ZOOM_STORAGE_KEY = 'ZIYA_UI_ZOOM';
export const UI_ZOOM_EXEMPT_PATHS: readonly string[] = ['/render', '/print', '/print-spike'];

let currentZoom = 1;

/** Snap innerWidth / target to the nearest step; 1 for wide or bad input. */
export function computeUiZoom(innerWidth: number): number {
    if (!Number.isFinite(innerWidth) || innerWidth <= 0) return 1;
    const ratio = Math.min(1, innerWidth / UI_ZOOM_TARGET_WIDTH);
    let best = 1;
    let bestDist = Infinity;
    for (const step of UI_ZOOM_STEPS) {
        const d = Math.abs(step - ratio);
        if (d < bestDist) { best = step; bestDist = d; }
    }
    return best;
}

/** Headless export routes render for page.pdf()/screenshots; never zoom them. */
export function isUiZoomExempt(pathname: string): boolean {
    return UI_ZOOM_EXEMPT_PATHS.some(p => pathname === p || pathname.startsWith(p + '/'));
}

/** Parse the ZIYA_UI_ZOOM debugging override: 'off', a factor, or null. */
export function readUiZoomOverride(storage: Pick<Storage, 'getItem'> | null | undefined): number | 'off' | null {
    let raw: string | null = null;
    try { raw = storage?.getItem(UI_ZOOM_STORAGE_KEY) ?? null; } catch { return null; }
    if (raw == null) return null;
    if (raw.trim().toLowerCase() === 'off') return 'off';
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0.3 && n <= 3 ? n : null;
}

export function resolveUiZoom(
    innerWidth: number,
    pathname: string,
    override: number | 'off' | null,
): number {
    if (isUiZoomExempt(pathname)) return 1;
    if (override === 'off') return 1;
    if (typeof override === 'number') return override;
    return computeUiZoom(innerWidth);
}

/**
 * Apply a zoom factor: publishes --ui-zoom on <html> for the CSS side and
 * sets `zoom` on <body> (antd/MUI portals mount under body, so they follow).
 */
export function applyUiZoom(zoom: number, doc: Document = document): void {
    currentZoom = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
    doc.documentElement.style.setProperty('--ui-zoom', String(currentZoom));
    if (doc.body) {
        if (currentZoom === 1) doc.body.style.removeProperty('zoom');
        else doc.body.style.setProperty('zoom', String(currentZoom));
    }
}

/** Decide and apply the boot-time factor.  Returns the factor applied. */
export function initUiZoom(): number {
    if (typeof window === 'undefined' || typeof document === 'undefined') return 1;
    let storage: Storage | null = null;
    try { storage = window.localStorage; } catch { storage = null; }
    const zoom = resolveUiZoom(
        window.innerWidth,
        window.location?.pathname ?? '/',
        readUiZoomOverride(storage),
    );
    applyUiZoom(zoom);
    if (zoom !== 1) {
        console.info(`[uiScale] narrow viewport (${window.innerWidth}px): applying UI zoom ${zoom}`);
    }
    return zoom;
}

/** The factor currently applied (1 when none). */
export function getUiZoom(): number {
    return currentZoom;
}

/** Convert a viewport-px quantity (clientX/Y, a pointer delta) into layout CSS px. */
export function toLayoutPx(viewportPx: number): number {
    return viewportPx / currentZoom;
}

/** window.innerWidth expressed in layout CSS px. */
export function layoutViewportWidth(): number {
    return window.innerWidth / currentZoom;
}

/** window.innerHeight expressed in layout CSS px. */
export function layoutViewportHeight(): number {
    return window.innerHeight / currentZoom;
}
