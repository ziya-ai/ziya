/**
 * @jest-environment jsdom
 */
/**
 * G-a05f90 — d2Plugin.ts label-delimiter parsing (D-366) and edge-label
 * legibility (D-362). Shared file: frontend/src/plugins/d3/d2Plugin.ts.
 *
 * D-366 (label-delimiter-collision): parseLine dispatched connection-vs-node on
 * the FIRST occurrence of `->`/`<-`/`<->`/`:` ANYWHERE in the line, so a label
 * that merely contained those glyphs was torn into phantom nodes:
 *   `arrowLabel: uses -> as text`         -> edge "arrowLabel: uses" -> "as text"
 *   `braceLabel -> pipeLabel: sep -> here` -> edges to phantom "here"/"as text"
 * The fix routes on d2HasStructuralConnector (a connector only counts when it is
 * BEFORE the first top-level label colon and OUTSIDE any quoted/{} span), and
 * parseConnection splits the label off at d2LabelColonIndex before splitting the
 * remaining structural region on connectors.
 *
 * D-362 (edge-label-collides-with-arrow): the edge label was a bare <text> with
 * a no-op `background` attribute, painted straight over the arrow line and
 * arrowhead — a strikethrough smear (worst in dark: white label over the
 * #9aa4b2 arrowhead, 2.52:1). The fix draws each label in a <g> with a BACKING
 * RECT filled from the PAGE BACKGROUND beneath the text, so the label reads on
 * the maximal-contrast page footing in BOTH themes.
 *
 * DIRECTION: every assertion documents the pre-fix behaviour it rejects, so the
 * suite fails against unpatched d2Plugin.ts.
 */
import {
  D2Parser,
  d2LabelColonIndex,
  d2HasStructuralConnector,
  d2Plugin,
  d2ThemeColors,
  D2_DARK_BG,
  D2_LIGHT_BG,
} from '../d2Plugin';
import { contrastRatio } from '../chartTheme';

const parse = (def: string) => new D2Parser().parse(def);

// ---------------------------------------------------------------------------
// D-366 — a connector / colon inside label text is literal, not structure
// ---------------------------------------------------------------------------
describe('D-366 delimiter-in-label does not spawn phantom nodes/edges', () => {
  test('helpers: a connector after the label colon is not structural', () => {
    // `arrowLabel: uses -> as text` — the `->` lives in the label region.
    expect(d2LabelColonIndex('arrowLabel: uses -> as text')).toBe(10);
    expect(d2HasStructuralConnector('arrowLabel: uses -> as text')).toBe(false);
    // `a -> b: label` — connector precedes the colon: structural.
    expect(d2HasStructuralConnector('a -> b: label')).toBe(true);
    // colon inside {} is not the label colon; `->` before it IS structural.
    expect(d2LabelColonIndex('a -> b {near: top}')).toBe(-1);
    expect(d2HasStructuralConnector('a -> b {near: top}')).toBe(true);
  });

  test('DIRECTION: pre-fix dispatched on the first `->` anywhere, tearing the label', () => {
    // The whole-line includes() test the old code used would misclassify this.
    const line = 'arrowLabel: uses -> as text';
    expect(line.includes('->')).toBe(true);            // old code => connection
    expect(d2HasStructuralConnector(line)).toBe(false); // new code => node
  });

  test('`arrowLabel: uses -> as text` is ONE node whose label keeps the arrow', () => {
    const { nodes, edges } = parse('arrowLabel: uses -> as text');
    expect(nodes.map((n: any) => n.id)).toEqual(['arrowLabel']);
    expect(nodes[0].label).toBe('uses -> as text');
    // No phantom endpoint node ("as text") and no edge.
    expect(edges).toHaveLength(0);
    expect(nodes.some((n: any) => /as text/i.test(n.id))).toBe(false);
  });

  test('`braceLabel -> pipeLabel: sep -> here` is ONE edge; label keeps the arrow', () => {
    const { nodes, edges } = parse('braceLabel -> pipeLabel: sep -> here');
    expect(nodes.map((n: any) => n.id).sort()).toEqual(['braceLabel', 'pipeLabel']);
    // Exactly one edge braceLabel -> pipeLabel (not a chain to phantom "here").
    expect(edges).toHaveLength(1);
    expect(edges[0].source).toBe('braceLabel');
    expect(edges[0].target).toBe('pipeLabel');
    expect(edges[0].label).toBe('sep -> here');
    expect(nodes.some((n: any) => /here/.test(n.id))).toBe(false);
  });

  test('a colon inside label text is preserved (D-061 guard stays green)', () => {
    // `a -> b: x` still yields two clean nodes + a labelled edge.
    const { nodes, edges } = parse('a -> b: x');
    expect(nodes.map((n: any) => n.id).sort()).toEqual(['a', 'b']);
    expect(edges).toHaveLength(1);
    expect(edges[0].label).toBe('x');
    // `colonLabel: ratio 16:9 aspect` -> node keeps the 16:9 in its label.
    const r = parse('colonLabel: ratio 16:9 aspect');
    expect(r.nodes.map((n: any) => n.id)).toEqual(['colonLabel']);
    expect(r.nodes[0].label).toBe('ratio 16:9 aspect');
  });
});

// ---------------------------------------------------------------------------
// D-362 — edge label rides on a backing rect filled from the page background,
// in BOTH themes (render-level).
// ---------------------------------------------------------------------------
type El = { tag: string; attrs: Record<string, any>; text?: any; datum: any };

function makeD2Recorder() {
  const store: El[] = [];
  function sel(els: El[], data: any[] | null, mode: 'normal' | 'enter'): any {
    const self: any = { __isSel: true };
    self.append = (tag: string) => {
      let created: El[];
      if (mode === 'enter' && data && data.length) {
        created = data.map((d) => { const e: El = { tag, attrs: {}, datum: d }; store.push(e); return e; });
      } else {
        const d = els[0] ? els[0].datum : undefined;
        const e: El = { tag, attrs: {}, datum: d };
        store.push(e);
        created = [e];
      }
      return sel(created, null, 'normal');
    };
    self.select = () => sel(els, null, 'normal');
    self.selectAll = () => sel([], null, 'normal');
    self.data = (arr: any[]) => sel([], Array.isArray(arr) ? arr : [], 'normal');
    self.datum = (d: any) => sel([{ tag: 'x', attrs: {}, datum: d }], null, 'normal');
    self.enter = () => sel(els, data, 'enter');
    self.exit = () => sel([], null, 'normal');
    self.merge = () => self;
    self.call = () => self;
    self.remove = () => self;
    self.attr = (k: string, v: any) => {
      els.forEach((e, i) => { e.attrs[k] = typeof v === 'function' ? v(e.datum, i) : v; });
      return self;
    };
    self.style = () => self;
    self.text = (v: any) => {
      els.forEach((e, i) => { e.text = typeof v === 'function' ? v(e.datum, i) : v; });
      return self;
    };
    self.each = function (fn: any) {
      els.forEach((e, i) => { fn.call(sel([e], null, 'normal'), e.datum, i); });
      return self;
    };
    return self;
  }
  const d3: any = { select: (arg: any) => (arg && arg.__isSel ? arg : sel([], null, 'normal')) };
  return { d3, store };
}

const byClass = (store: El[], cls: string) => store.filter((e) => e.attrs['class'] === cls);

describe('D-362 edge label sits on a backing rect filled from the page bg (both themes)', () => {
  for (const isDark of [false, true]) {
    const themeName = isDark ? 'dark' : 'light';
    const pageBg = isDark ? D2_DARK_BG : D2_LIGHT_BG;

    test(`a labelled edge emits a .edge-label <g> + .edge-label-bg <rect> filled with the page bg [${themeName}]`, async () => {
      const { d3, store } = makeD2Recorder();
      const def = ['a -> b: hello'].join('\n');
      await d2Plugin.render!(document.createElement('div'), d3, { type: 'd2', definition: def } as any, isDark);

      // The label is grouped (was a bare <text> pre-fix).
      const groups = byClass(store, 'edge-label').filter((e) => e.tag === 'g');
      expect(groups.length).toBeGreaterThanOrEqual(1);

      // DIRECTION: pre-fix emitted NO backing rect (only a <text> with a no-op
      // `background` attribute), so this fails against unpatched source.
      const bgRects = byClass(store, 'edge-label-bg').filter((e) => e.tag === 'rect');
      expect(bgRects.length).toBeGreaterThanOrEqual(1);
      // The rect fill resolves from the THEME's page background, not a constant.
      expect(bgRects.every((r) => r.attrs['fill'] === pageBg)).toBe(true);
      // The rect actually covers a footprint (non-zero box).
      expect(bgRects.every((r) => Number(r.attrs['width']) > 0 && Number(r.attrs['height']) > 0)).toBe(true);

      // The label text is drawn (on top of the rect).
      const labelTexts = store.filter((e) => e.tag === 'text' && e.text === 'hello');
      expect(labelTexts.length).toBeGreaterThanOrEqual(1);
    });

    test(`edge-label text on the backing bg clears WCAG 4.5:1 [${themeName}]`, () => {
      // The backing rect is the page bg and the text is the theme text colour,
      // so legibility no longer depends on whatever the arrow paints beneath.
      const text = d2ThemeColors(isDark).text; // #fff dark / #000 light
      expect(contrastRatio(text, pageBg)).toBeGreaterThanOrEqual(4.5);
    });
  }

  test('DIRECTION: white edge-label directly over the dark arrowhead was only 2.52:1', () => {
    // The pre-fix failure mode this fix removes: the label read against the
    // #9aa4b2 arrowhead, not a solid page-bg backing.
    expect(contrastRatio('#ffffff', '#9aa4b2')).toBeLessThan(4.5);
    // With the backing rect the same label reads against #1f1f1f instead.
    expect(contrastRatio('#ffffff', D2_DARK_BG)).toBeGreaterThanOrEqual(4.5);
  });
});
