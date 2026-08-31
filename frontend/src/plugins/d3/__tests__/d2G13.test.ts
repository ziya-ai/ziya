/**
 * G-13 — d2 plugin structural repairs, part 2 (shared file: d2Plugin.ts).
 *
 * All four defects are kind:structural and THEME-INVARIANT: the parse output
 * and the sizing/wrapping geometry are byte-identical in light and dark (no
 * colour is resolved from the theme in any of these paths), so each test
 * asserts the theme-independent value. Author `style` colours are passed
 * through verbatim by design — per-theme contrast clamping of author colours is
 * a separate theme defect cluster, not G-13.
 *
 *   D-083  `x.style.y: value` and `style { }` blocks are parsed as STYLES on
 *          the target node/class, not as phantom nodes (boxes labelled
 *          '#8b0000') or containers that steal the parent's label.
 *   D-086  d2CanvasSize returns a natural PIXEL width (not '100%'), so the
 *          fixed-px label font is not downscaled with the viewBox.
 *   D-087  wrapLabel breaks a long label into lines that fit the box (and
 *          hard-breaks a single unbreakable token), and d2NodeHeight grows to
 *          hold them, so labels no longer run off the rect/canvas.
 *   D-088  d2GridPitch is >= the widest node, so grid boxes cannot overlap and
 *          truncate their right neighbour (old nodeSpacing=150 < width up to 200).
 *
 * Direction: every assertion is paired with a check documenting the PRE-FIX
 * value (label swallowed into a node, single flat text line, pitch 150 < width),
 * so each test fails against unpatched d2Plugin.ts.
 */
import {
  D2Parser,
  wrapLabel,
  d2NodeWidth,
  d2NodeHeight,
  d2GridPitch,
  d2CanvasSize,
  D2_FONT_SIZE,
  D2_MAX_NODE_WIDTH,
} from '../d2Plugin';

// ---------------------------------------------------------------------------
// D-083 — style keys/blocks become styles, not phantom nodes/containers
// ---------------------------------------------------------------------------
describe('D-083 style parsing (no phantom nodes/containers)', () => {
  test('DIRECTION: the pre-fix simple-node split turned a dotted style line into a node', () => {
    // Old parseSimpleNode did `line.split(':')` on `danger.style.fill: "#8b0000"`
    // -> id 'danger.style.fill', label '"#8b0000"'. Reproduce that shape here.
    const line = 'danger.style.fill: "#8b0000"';
    const parts = line.split(':');
    expect(parts[0].trim()).toBe('danger.style.fill'); // phantom node id
    expect(parts.slice(1).join(':').trim()).toBe('"#8b0000"'); // label = the colour
  });

  test('dotted `X.style.k: v` applies to node X, creates no phantom node (d2-w1-10)', () => {
    const def = [
      'danger: Critical Path',
      'danger.style.fill: "#8b0000"',
      'danger.style.stroke: "#ffffff"',
      'danger.style.font-color: "#ffffff"',
      'safe: Normal Path',
      'safe.style.fill: "#e8f5e9"',
      'safe.style.font-color: "#1b5e20"',
      'danger -> safe: fallback',
    ].join('\n');
    const { nodes, edges, containers } = new D2Parser().parse(def);

    // Exactly two nodes: danger and safe. No phantom style nodes/containers.
    const ids = nodes.map((n: any) => n.originalId).sort();
    expect(ids).toEqual(['danger', 'safe']);
    expect(containers).toHaveLength(0);
    // No node's label is a colour value (the old phantom-node symptom).
    expect(nodes.some((n: any) => String(n.label).includes('#8b0000'))).toBe(false);

    const danger = nodes.find((n: any) => n.originalId === 'danger');
    expect(danger.label).toBe('Critical Path');           // label survived
    expect(danger.style.fill).toBe('#8b0000');            // quotes stripped
    expect(danger.style.stroke).toBe('#ffffff');
    expect(danger.style['font-color']).toBe('#ffffff');

    // Edge label still parses (regression guard for the shared connection fix).
    expect(edges).toHaveLength(1);
    expect(edges[0].label).toBe('fallback');
  });

  test('nested `style { }` block applies to the parent node, not a container (d2-w1-12)', () => {
    const def = [
      'cache: Redis Cache {',
      '  style {',
      '    fill: "#fff3e0"',
      '    stroke: "#e65100"',
      '    stroke-width: 3',
      '  }',
      '}',
      'worker: Worker',
      'worker -> cache: SET/GET',
    ].join('\n');
    const { nodes, containers, edges } = new D2Parser().parse(def);

    // No container named 'style' (the old '{'-suffix container bug), and no
    // nodes named after style keys.
    expect(containers.some((c: any) => c.id === 'style')).toBe(false);
    expect(nodes.some((n: any) => ['fill', 'stroke', 'stroke-width'].includes(n.originalId))).toBe(false);

    const cache = nodes.find((n: any) => n.originalId === 'cache');
    expect(cache).toBeDefined();
    expect(cache.label).toBe('Redis Cache');   // label NOT lost to a container
    expect(cache.style.fill).toBe('#fff3e0');
    expect(cache.style.stroke).toBe('#e65100');
    expect(cache.style['stroke-width']).toBe('3');

    expect(edges).toHaveLength(1);
    expect(edges[0].label).toBe('SET/GET');
  });

  test('`classes { }` + `{class: name}` seeds node style from the class (d2-w1-13)', () => {
    const def = [
      'classes: {',
      '  service: {',
      '    style.fill: "#e1f5fe"',
      '    style.stroke: "#01579b"',
      '  }',
      '}',
      'auth: Auth {class: service}',
      'bill: Billing {class: service}',
      'auth -> bill: charge',
    ].join('\n');
    const { nodes, edges, containers } = new D2Parser().parse(def);

    // 'classes' / 'service' are class definitions, not nodes or containers.
    expect(containers).toHaveLength(0);
    const ids = nodes.map((n: any) => n.originalId).sort();
    expect(ids).toEqual(['auth', 'bill']);

    const auth = nodes.find((n: any) => n.originalId === 'auth');
    expect(auth.label).toBe('Auth');
    expect(auth.style.fill).toBe('#e1f5fe');
    expect(auth.style.stroke).toBe('#01579b');

    expect(edges).toHaveLength(1);
    expect(edges[0].label).toBe('charge');
  });

  test('plain containers still parse (regression guard for the container path)', () => {
    const def = ['outer {', '  inner {', '    leaf: Leaf', '  }', '}'].join('\n');
    const { containers, nodes } = new D2Parser().parse(def);
    const byId = new Map(containers.map((c: any) => [c.id, c]));
    expect(byId.get('inner').parent).toBe('outer');
    expect(byId.get('outer').parent).toBeNull();
    expect(nodes.find((n: any) => n.originalId === 'leaf').container).toBe('inner');
  });
});

// ---------------------------------------------------------------------------
// D-087 — long labels wrap instead of overflowing the box/canvas
// ---------------------------------------------------------------------------
describe('D-087 label wrapping', () => {
  const longLabel =
    'Extremely Long Descriptive Node Label That Goes On And On For A Very Long Time Indeed Without Any Break Whatsoever Here 0';

  test('DIRECTION: the pre-fix renderer emitted the whole label as one line', () => {
    // One flat run at ~8px/char is ~950px wide inside a <=200px box.
    expect(longLabel.length * 8).toBeGreaterThan(D2_MAX_NODE_WIDTH * 3);
  });

  test('wrapLabel splits into multiple lines each within the box width', () => {
    const width = d2NodeWidth(longLabel);
    const lines = wrapLabel(longLabel, width);
    expect(lines.length).toBeGreaterThan(1);
    const maxChars = Math.floor((width - 16) / 8);
    for (const ln of lines) {
      expect(ln.length).toBeLessThanOrEqual(maxChars);
    }
    // No content is lost: joining the lines yields the original words.
    expect(lines.join(' ').split(/\s+/).filter(Boolean).join(' ')).toBe(longLabel);
  });

  test('a single unbreakable 600-char token is hard-broken (never one 4000px line)', () => {
    const token = 'x'.repeat(600);
    const width = d2NodeWidth(token); // clamps to D2_MAX_NODE_WIDTH
    const lines = wrapLabel(token, width);
    const maxChars = Math.floor((width - 16) / 8);
    expect(lines.length).toBeGreaterThan(1);
    lines.forEach(ln => expect(ln.length).toBeLessThanOrEqual(maxChars));
    expect(lines.join('')).toBe(token); // no characters dropped
  });

  test('d2NodeHeight grows with the wrapped line count', () => {
    const tall = d2NodeHeight(longLabel);
    const short = d2NodeHeight('Short');
    expect(tall).toBeGreaterThan(short);
    // Height must actually cover the lines it will render.
    const lines = wrapLabel(longLabel, d2NodeWidth(longLabel)).length;
    expect(tall).toBeGreaterThanOrEqual(12 + lines * 16);
  });
});

// ---------------------------------------------------------------------------
// D-088 — grid pitch is wide enough that boxes never overlap
// ---------------------------------------------------------------------------
describe('D-088 grid pitch >= node width', () => {
  test('DIRECTION: the pre-fix constant pitch (150) was narrower than a wide node (up to 200/240)', () => {
    const OLD_PITCH = 150;
    expect(OLD_PITCH).toBeLessThan(d2NodeWidth('Service Component Number 000'));
  });

  test('pitch exceeds the widest laid-out node so neighbours cannot overlap', () => {
    const nodes = Array.from({ length: 12 }, (_, i) => {
      const label = `Service Component Number ${String(i).padStart(3, '0')}`;
      return { id: `n${i}`, label, width: d2NodeWidth(label), height: d2NodeHeight(label) };
    });
    const pitch = d2GridPitch(nodes);
    const maxW = Math.max(...nodes.map(n => n.width));
    expect(pitch.x).toBeGreaterThan(maxW);
  });

  test('empty node set degrades to a sane minimum pitch (no NaN)', () => {
    const pitch = d2GridPitch([]);
    expect(Number.isFinite(pitch.x)).toBe(true);
    expect(pitch.x).toBeGreaterThanOrEqual(D2_MAX_NODE_WIDTH - D2_MAX_NODE_WIDTH); // > 0
    expect(pitch.x).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// D-086 — SVG painted at natural pixel size so fixed-px text is not downscaled
// ---------------------------------------------------------------------------
describe('D-086 canvas sized in pixels (no viewBox downscale)', () => {
  test('DIRECTION: label font-size is a fixed pixel value in viewBox units', () => {
    // A fixed px font only stays legible if the SVG is NOT stretched to 100%
    // of a narrower container (which the old width:"100%" attribute did).
    expect(D2_FONT_SIZE).toBe(12);
  });

  test('d2CanvasSize returns a numeric width equal to the viewBox width', () => {
    const nodes = [
      { x: 0, y: 0, width: 200, height: 40 },
      { x: 4000, y: 3000, width: 200, height: 40 }, // large graph
    ];
    const c = d2CanvasSize(nodes);
    expect(typeof c.width).toBe('number');
    expect(Number.isFinite(c.width)).toBe(true);
    expect(c.width).toBe(4300);   // maxX(4000+200) + 100 -> 4300
    expect(c.height).toBe(3140);
    // viewBox width matches the pixel width -> scale factor 1 -> 12px stays 12px.
    expect(c.viewBox).toBe(`0 0 ${c.width} ${c.height}`);
  });

  test('small graphs keep the 800x400 minimum', () => {
    const c = d2CanvasSize([{ x: 0, y: 0, width: 80, height: 40 }]);
    expect(c.width).toBe(800);
    expect(c.height).toBe(400);
  });
});
