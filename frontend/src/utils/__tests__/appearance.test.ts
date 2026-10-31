/**
 * @jest-environment jsdom
 *
 * Appearance preference store (design/visual-refresh.md, stage 1).
 *
 * The seam under test: a preference set through setAppearance() must land
 * both in localStorage and as a data attribute on <html>, because
 * theme-tokens.css selects on html[data-paper], html[data-font],
 * html[data-density] and html[data-prose-cap].  A store that persisted but
 * did not stamp the attribute would pass a round-trip test and still leave
 * the UI unchanged; the attribute assertions are the ones that matter.
 */
import {
    APPEARANCE_STORAGE_KEY, DEFAULT_APPEARANCE, _resetAppearanceForTests,
    applyAppearance, getAppearance, initAppearance, loadAppearance,
    normalizeAppearance, setAppearance, subscribeAppearance,
} from '../appearance';

beforeEach(() => {
    localStorage.clear();
    _resetAppearanceForTests();
    for (const a of ['data-paper', 'data-font', 'data-density', 'data-prose-cap']) {
        document.documentElement.removeAttribute(a);
    }
});

describe('normalizeAppearance', () => {
    it('returns defaults for garbage and fills missing keys', () => {
        expect(normalizeAppearance(null)).toEqual(DEFAULT_APPEARANCE);
        expect(normalizeAppearance('x')).toEqual(DEFAULT_APPEARANCE);
        expect(normalizeAppearance({ font: 'georgia' })).toEqual({ ...DEFAULT_APPEARANCE, font: 'georgia' });
    });

    it('rejects unknown enum values individually, keeping valid siblings', () => {
        const p = normalizeAppearance({ paper: 'neon', font: 'palatino', density: 'cosy', proseCap: 'yes' });
        expect(p).toEqual({ paper: 'linen', font: 'palatino', density: 'compact', proseCap: false });
    });
});

describe('loadAppearance', () => {
    it('survives malformed JSON in storage', () => {
        localStorage.setItem(APPEARANCE_STORAGE_KEY, '{not json');
        expect(loadAppearance()).toEqual(DEFAULT_APPEARANCE);
    });

    it('reads a stored value', () => {
        localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify({ paper: 'warm', density: 'comfortable' }));
        expect(loadAppearance()).toMatchObject({ paper: 'warm', density: 'comfortable' });
    });
});

describe('applyAppearance / initAppearance', () => {
    it('stamps every attribute theme-tokens.css selects on', () => {
        applyAppearance({ paper: 'cool', font: 'sans', density: 'comfortable', proseCap: true });
        const el = document.documentElement;
        expect(el.getAttribute('data-paper')).toBe('cool');
        expect(el.getAttribute('data-font')).toBe('sans');
        expect(el.getAttribute('data-density')).toBe('comfortable');
        expect(el.getAttribute('data-prose-cap')).toBe('on');
    });

    it('initAppearance applies the stored prefs before first paint', () => {
        localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify({ paper: 'white' }));
        initAppearance();
        expect(document.documentElement.getAttribute('data-paper')).toBe('white');
        expect(document.documentElement.getAttribute('data-prose-cap')).toBe('off');
    });
});

describe('setAppearance', () => {
    it('persists, stamps the attribute, and notifies subscribers in one call', () => {
        const seen: string[] = [];
        const unsub = subscribeAppearance(p => seen.push(p.font));

        setAppearance({ font: 'georgia' });

        expect(JSON.parse(localStorage.getItem(APPEARANCE_STORAGE_KEY)!)).toMatchObject({ font: 'georgia' });
        expect(document.documentElement.getAttribute('data-font')).toBe('georgia');
        expect(getAppearance().font).toBe('georgia');
        expect(seen).toEqual(['georgia']);

        unsub();
        setAppearance({ font: 'sans' });
        expect(seen).toEqual(['georgia']);
    });

    it('a partial patch does not reset the other knobs', () => {
        setAppearance({ paper: 'warm' });
        setAppearance({ proseCap: true });
        expect(getAppearance()).toEqual({ ...DEFAULT_APPEARANCE, paper: 'warm', proseCap: true });
    });
});
