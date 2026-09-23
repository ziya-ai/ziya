/**
 * Automatic UI zoom for narrow viewports (utils/uiScale.ts).
 *
 * Field data (Sep 2026): a MacBook Air on macOS "Larger Text" scaling
 * presents a 1024-px CSS viewport; the user found 80% browser zoom (≈1300
 * CSS px) the first usable width.  Browser page zoom is not scriptable, so
 * the app applies CSS `zoom` on <body>.  Two spec consequences drive the
 * seam tests below (css-viewport §4, verified against CSSWG #13016):
 *   - `zoom` pre-multiplies every <length>, including vh/vw — so structural
 *     `100vh` sites must become percentages or divide by --ui-zoom.
 *   - mouse clientX/clientY and window.innerWidth stay in UNZOOMED viewport
 *     px while layout is zoomed — so drag math must divide by the zoom.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
    UI_ZOOM_TARGET_WIDTH,
    UI_ZOOM_STEPS,
    computeUiZoom,
    isUiZoomExempt,
    resolveUiZoom,
    readUiZoomOverride,
    applyUiZoom,
    getUiZoom,
    toLayoutPx,
    layoutViewportWidth,
} from '../uiScale';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

afterEach(() => applyUiZoom(1));

describe('computeUiZoom', () => {
    it('targets the observed comfortable layout width and browser-like steps', () => {
        expect(UI_ZOOM_TARGET_WIDTH).toBe(1300);
        expect(UI_ZOOM_STEPS).toEqual([1, 0.9, 0.8, 0.67]);
    });
    it('reproduces the field observation: 1024 → 80%', () => {
        expect(computeUiZoom(1024)).toBe(0.8);
    });
    it('snaps to the nearest step, never zooming past 67%', () => {
        expect(computeUiZoom(1200)).toBe(0.9);
        expect(computeUiZoom(1100)).toBe(0.8);
        expect(computeUiZoom(900)).toBe(0.67);
        expect(computeUiZoom(600)).toBe(0.67);
    });
    it('leaves every stock laptop and the marginal 1280 case untouched', () => {
        expect(computeUiZoom(1280)).toBe(1);
        expect(computeUiZoom(1300)).toBe(1);
        expect(computeUiZoom(1366)).toBe(1);
        expect(computeUiZoom(2056)).toBe(1);
    });
    it('does not double-zoom a user who already set 80% browser zoom (innerWidth already reflects it)', () => {
        // 1024-px viewport at 80% browser zoom reads innerWidth = 1280
        expect(computeUiZoom(1024 / 0.8)).toBe(1);
    });
    it('returns 1 on bad input', () => {
        expect(computeUiZoom(NaN)).toBe(1);
        expect(computeUiZoom(0)).toBe(1);
        expect(computeUiZoom(-5)).toBe(1);
    });
});

describe('isUiZoomExempt', () => {
    it('exempts the headless export routes so PDF / diagram geometry is unchanged', () => {
        expect(isUiZoomExempt('/render')).toBe(true);
        expect(isUiZoomExempt('/print')).toBe(true);
        expect(isUiZoomExempt('/print-spike')).toBe(true);
        expect(isUiZoomExempt('/print/')).toBe(true);
    });
    it('applies to the app and the info pages', () => {
        expect(isUiZoomExempt('/')).toBe(false);
        expect(isUiZoomExempt('/info')).toBe(false);
        expect(isUiZoomExempt('/printer')).toBe(false);
    });
});

describe('readUiZoomOverride', () => {
    const storage = (v: string | null) => ({ getItem: () => v } as unknown as Storage);
    it('parses the debugging kill switch and numeric overrides', () => {
        expect(readUiZoomOverride(storage(null))).toBeNull();
        expect(readUiZoomOverride(storage('off'))).toBe('off');
        expect(readUiZoomOverride(storage('0.8'))).toBe(0.8);
    });
    it('rejects garbage and absurd factors', () => {
        expect(readUiZoomOverride(storage('huge'))).toBeNull();
        expect(readUiZoomOverride(storage('0'))).toBeNull();
        expect(readUiZoomOverride(storage('12'))).toBeNull();
    });
});

describe('resolveUiZoom', () => {
    it('exempt route wins over everything', () => {
        expect(resolveUiZoom(1024, '/print', 0.5)).toBe(1);
    });
    it('override wins over the heuristic; "off" disables', () => {
        expect(resolveUiZoom(1024, '/', 'off')).toBe(1);
        expect(resolveUiZoom(2056, '/', 0.67)).toBe(0.67);
    });
    it('falls through to the heuristic', () => {
        expect(resolveUiZoom(1024, '/', null)).toBe(0.8);
    });
});

describe('applyUiZoom / coordinate helpers', () => {
    it('publishes --ui-zoom on <html> and exposes the factor to JS', () => {
        applyUiZoom(0.8);
        expect(document.documentElement.style.getPropertyValue('--ui-zoom')).toBe('0.8');
        expect(getUiZoom()).toBe(0.8);
    });
    it('converts viewport px (mouse, innerWidth) into layout px', () => {
        applyUiZoom(0.8);
        expect(toLayoutPx(400)).toBe(500);
        expect(layoutViewportWidth()).toBe(window.innerWidth / 0.8);
    });
    it('is the identity at zoom 1', () => {
        applyUiZoom(1);
        expect(document.documentElement.style.getPropertyValue('--ui-zoom')).toBe('1');
        expect(toLayoutPx(400)).toBe(400);
    });
});

describe('boot seam (index.tsx)', () => {
    it('initialises the zoom before the React root renders', () => {
        const src = read('../../index.tsx');
        const init = src.indexOf('initUiZoom(');
        const render = src.indexOf('root.render(');
        expect(init).toBeGreaterThan(-1);
        expect(render).toBeGreaterThan(init);
    });
});

describe('viewport-unit seams (zoom scales vh/vw; percentages are exempt)', () => {
    const css = read('../../index.css');
    const block = (selector: string) => {
        const i = css.indexOf(`\n${selector} {`);
        expect(i).toBeGreaterThan(-1);
        return css.slice(i, css.indexOf('}', i));
    };

    it('defines zoom-corrected viewport lengths on :root', () => {
        const root = block(':root');
        expect(root).toMatch(/--ui-zoom:\s*1;/);
        expect(root).toMatch(/--viewport-height:\s*calc\(100vh \/ var\(--ui-zoom/);
        expect(root).toMatch(/--viewport-width:\s*calc\(100vw \/ var\(--ui-zoom/);
    });
    it('body no longer pins itself to 100vh (would render 80% tall under zoom)', () => {
        expect(block('body')).not.toMatch(/100vh/);
        expect(block('html, body')).toMatch(/height:\s*100%/);
    });
    it('structural layout blocks use the corrected lengths, not raw vh/vw', () => {
        for (const sel of ['.container', '.chat-container', '.folder-tree-panel', '.panel-toggle']) {
            const b = block(sel);
            expect(b).not.toMatch(/\b100v[hw]\b/);
            expect(b).toMatch(/var\(--viewport-(height|width)\)/);
        }
        expect(block('.panel-collapsed .chat-container')).not.toMatch(/100vw/);
    });
    it('graph panel and the app container inline style avoid raw viewport units', () => {
        // Match a declaration, not the explanatory comment that names the old unit.
        expect(read('../../components/ConversationGraph/GraphPanel.css')).not.toMatch(/height:\s*100vh/);
        expect(read('../../components/App.tsx')).not.toMatch(/width:\s*'100vw'/);
    });
});

describe('coordinate seams (clientX / innerWidth are unzoomed; layout is zoomed)', () => {
    it('App.tsx derives panel geometry from the layout width, not window.innerWidth', () => {
        const src = read('../../components/App.tsx');
        expect(src).toMatch(/layoutViewportWidth\(\)/);
        expect(src).not.toMatch(/window\.innerWidth \* 0\.33/);
        expect(src).not.toMatch(/window\.innerWidth - 350/);
        expect(src).not.toMatch(/window\.innerWidth \* 0\.25/);
    });
    it('every drag handler converts pointer deltas through toLayoutPx', () => {
        expect(read('../../components/PanelResizer.tsx')).toMatch(/toLayoutPx\(e\.clientX\)/);
        expect(read('../../components/ConversationGraph/GraphPanel.tsx')).toMatch(/toLayoutPx\(startX - ev\.clientX\)/);
        expect(read('../../components/MemoryBrowser.tsx')).toMatch(/toLayoutPx\(e\.clientX - svgRect\.left\)/);
        const tcl = read('../../components/TaskCard/TaskCardsLibrary.tsx');
        expect(tcl).toMatch(/toLayoutPx\(e\.clientX - startX\)/);
        expect(tcl).toMatch(/toLayoutPx\(e\.clientX - d\.startX\)/);
        expect(tcl).toMatch(/toLayoutPx\(e\.clientY - d\.startY\)/);
        const mch = read('../../components/MUIChatHistory.tsx');
        expect(mch).toMatch(/toLayoutPx\(e\.clientX\) \+ 10/);
        expect(mch).toMatch(/toLayoutPx\(e\.clientY\) \+ 10/);
    });
});
