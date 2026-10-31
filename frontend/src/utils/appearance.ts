/**
 * Appearance preferences (design/visual-refresh.md).
 *
 * Six global, browser-ergonomic knobs that used to be scattered (theme on
 * the bulb button, nothing for the rest).  Theme itself stays in
 * ThemeContext; this module owns the other four and projects them onto
 * <html> as data attributes so theme-tokens.css can select on them:
 *
 *   data-paper    warm | linen | cool | white   (light mode only)
 *   data-font     charter | georgia | palatino | sans
 *   data-density  compact | comfortable
 *   data-prose-cap on | off
 *
 * Stored in localStorage like the existing theme and order preferences:
 * these follow the browser profile, not the user, by decision.
 */

export type PaperTone = 'warm' | 'linen' | 'cool' | 'white';
export type ResponseFont = 'charter' | 'georgia' | 'palatino' | 'sans';
export type Density = 'compact' | 'comfortable';

export interface AppearancePrefs {
    paper: PaperTone;
    font: ResponseFont;
    density: Density;
    proseCap: boolean;
}

export const APPEARANCE_STORAGE_KEY = 'ZIYA_APPEARANCE';

export const DEFAULT_APPEARANCE: AppearancePrefs = {
    paper: 'linen',
    font: 'charter',
    density: 'compact',
    proseCap: false,
};

const PAPERS: readonly PaperTone[] = ['warm', 'linen', 'cool', 'white'];
const FONTS: readonly ResponseFont[] = ['charter', 'georgia', 'palatino', 'sans'];
const DENSITIES: readonly Density[] = ['compact', 'comfortable'];

/** Coerce anything (bad JSON, old shapes, unknown values) to valid prefs. */
export function normalizeAppearance(raw: unknown): AppearancePrefs {
    const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
    return {
        paper: PAPERS.includes(r.paper as PaperTone) ? (r.paper as PaperTone) : DEFAULT_APPEARANCE.paper,
        font: FONTS.includes(r.font as ResponseFont) ? (r.font as ResponseFont) : DEFAULT_APPEARANCE.font,
        density: DENSITIES.includes(r.density as Density) ? (r.density as Density) : DEFAULT_APPEARANCE.density,
        proseCap: typeof r.proseCap === 'boolean' ? r.proseCap : DEFAULT_APPEARANCE.proseCap,
    };
}

export function loadAppearance(storage: Pick<Storage, 'getItem'> | null | undefined = globalThis.localStorage): AppearancePrefs {
    try {
        const s = storage?.getItem(APPEARANCE_STORAGE_KEY);
        return normalizeAppearance(s ? JSON.parse(s) : null);
    } catch {
        return { ...DEFAULT_APPEARANCE };
    }
}

/** Project prefs onto the document root so CSS can select on them. */
export function applyAppearance(prefs: AppearancePrefs, doc: Document = document): void {
    const el = doc.documentElement;
    el.setAttribute('data-paper', prefs.paper);
    el.setAttribute('data-font', prefs.font);
    el.setAttribute('data-density', prefs.density);
    el.setAttribute('data-prose-cap', prefs.proseCap ? 'on' : 'off');
}

// ---- tiny store so the popover and any consumer share one source --------

let current: AppearancePrefs | null = null;
const listeners = new Set<(p: AppearancePrefs) => void>();

export function getAppearance(): AppearancePrefs {
    if (!current) current = loadAppearance();
    return current;
}

export function setAppearance(patch: Partial<AppearancePrefs>): AppearancePrefs {
    const next = normalizeAppearance({ ...getAppearance(), ...patch });
    current = next;
    try { globalThis.localStorage?.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(next)); } catch { /* private mode */ }
    if (typeof document !== 'undefined') applyAppearance(next);
    listeners.forEach(l => l(next));
    return next;
}

export function subscribeAppearance(fn: (p: AppearancePrefs) => void): () => void {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
}

/** Call once at boot, before first paint, to avoid a flash of default paper. */
export function initAppearance(): AppearancePrefs {
    const p = getAppearance();
    if (typeof document !== 'undefined') applyAppearance(p);
    return p;
}

/** Test seam: forget the cached value so the next read hits storage. */
export function _resetAppearanceForTests(): void {
    current = null;
    listeners.clear();
}

export const PAPER_LABELS: Record<PaperTone, string> = {
    warm: 'Warm', linen: 'Linen', cool: 'Cool', white: 'White',
};
export const FONT_LABELS: Record<ResponseFont, string> = {
    charter: 'Charter', georgia: 'Georgia', palatino: 'Palatino', sans: 'System sans',
};
export const FONT_STACKS: Record<ResponseFont, string> = {
    charter: "Charter, 'Iowan Old Style', Georgia, serif",
    georgia: "Georgia, 'Times New Roman', serif",
    palatino: "Palatino, 'Palatino Linotype', 'Book Antiqua', Georgia, serif",
    sans: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif",
};
