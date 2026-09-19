/**
 * G-6f8b51 packet regression + structural + theme fixes (iteration 24).
 *
 * Direction is asserted for every case: the pre-fix behaviour is reconstructed
 * and shown to be wrong BEFORE the post-fix helper is asserted correct, so a
 * test that would also pass against unpatched code cannot masquerade as a fix.
 * Pure helpers only (no DOM / no d3), matching the other packet unit tests.
 *
 *  - D-196 / D-197  parsePacketBetaDsl bitWidth accommodates the widest field
 *                   (Ethernet/ARP 48-bit fields no longer overflow a 32-bit row
 *                   and get drawn past the declared grid → blank capture).
 *  - D-451          computeGridMetrics/maxRowBits: the grid encloses the widest
 *                   row (viewBox never narrower than content) and never exceeds
 *                   the capture ceiling.
 *  - D-453          resolvePacketDefinitionString unwraps a nested
 *                   {definition:{definition:"packet-beta …"}} envelope.
 *  - D-452          coextensiveLabelVShifts staggers co-extensive different-depth
 *                   horizontal bracket labels so they stop overprinting.
 *  - D-454          resolveColor gives an explicit near-canvas triple a border
 *                   meeting the 3:1 graphical floor on the ACTIVE theme canvas.
 */
import {
  parsePacketBetaDsl,
  resolvePacketDefinitionString,
  coextensiveLabelVShifts,
  type BracketLabelBox,
} from '../packetPlugin';
import {
  computeGridMetrics,
  maxRowBits,
  resolveColor,
  PACKET_MAX_GRID_PX,
  type PacketSpec,
} from '../../../utils/d3Plugins/packetPlugin';
import { calculateContrastRatio } from '../../../utils/colorUtils';

describe('D-196/D-197 parsePacketBetaDsl — bitWidth accommodates the widest field', () => {
  const ETH = 'packet-beta\ntitle Ethernet II Frame\n0-47: "Destination MAC"\n48-95: "Source MAC"\n96-111: "EtherType"\n112-127: "Payload start"';
  const ARP = 'packet\ntitle ARP Request\n+16: "Hardware Type"\n+16: "Protocol Type"\n+8: "HW Addr Len"\n+8: "Proto Addr Len"\n+16: "Operation"\n+48: "Sender HW Addr"';

  it('Ethernet (48-bit MACs): bitWidth is at least the widest field (w4-05)', () => {
    const dsl = parsePacketBetaDsl(ETH)!;
    expect(dsl).not.toBeNull();
    const widest = Math.max(...dsl.fields.map(f => f.bits));
    expect(widest).toBe(48);
    // DIRECTION: the pre-fix bridge hardcoded bitWidth:32, so a 48-bit field
    // exceeded the row and was drawn past bits*BIT_W (blank capture).
    expect(32).toBeLessThan(widest); // old value would overflow
    expect(dsl.bitWidth).toBeGreaterThanOrEqual(widest);
    expect(dsl.bitWidth).toBe(48);
  });

  it('ARP (+48 relative field): bitWidth accommodates it (w4-06)', () => {
    const dsl = parsePacketBetaDsl(ARP)!;
    const widest = Math.max(...dsl.fields.map(f => f.bits));
    expect(widest).toBe(48);
    expect(dsl.bitWidth).toBeGreaterThanOrEqual(widest);
  });

  it('control: an all-narrow DSL keeps the familiar 32-bit ruler', () => {
    const dsl = parsePacketBetaDsl('packet-beta\n0-15: "a"\n16-31: "b"')!;
    expect(Math.max(...dsl.fields.map(f => f.bits))).toBe(16);
    expect(dsl.bitWidth).toBe(32);
  });
});

describe('D-451 computeGridMetrics — grid encloses widest row, capped at ceiling', () => {
  const mk = (bitWidth: number | undefined, rows: any): PacketSpec => ({
    type: 'packet', title: 'T', bitWidth,
    sections: [{ label: 'S', rows }],
  } as any);

  it('maxRowBits reports a row wider than the declared bitWidth', () => {
    // A row summing to 48 bits inside a nominal 32-bit spec.
    expect(maxRowBits([{ label: 'S', rows: [[['a', 48]]] }] as any)).toBe(48);
  });

  it('grid width encloses the widest row (viewBox not narrower than content)', () => {
    const spec = mk(32, [[['macdst', 48]], [['macsrc', 48]]]);
    const m = computeGridMetrics(spec);
    // DIRECTION: pre-fix GRID_W === bits*BIT_W === 32*24 === 768, but the 48-bit
    // field is drawn to 48*24 === 1152 — 384px OUTSIDE the viewBox.
    expect(m.gridBits).toBeGreaterThanOrEqual(48);
    expect(m.GRID_W).toBeGreaterThanOrEqual(48 * m.BIT_W);
    expect(m.GRID_W).toBeGreaterThan(32 * m.BIT_W);
  });

  it('a well-formed exact-fill spec is unchanged (no-op)', () => {
    const spec = mk(32, [[['a', 16], ['b', 16]]]);
    const m = computeGridMetrics(spec);
    expect(m.gridBits).toBe(32);
    expect(m.BIT_W).toBe(24);          // defaultLayout(32)
    expect(m.GRID_W).toBe(32 * 24);
  });

  it('a 512-bit grid is capped under the capture ceiling (w2-03)', () => {
    const spec = mk(512, [[['q0', 128], ['q1', 128], ['q2', 128], ['q3', 128]]]);
    const m = computeGridMetrics(spec);
    // DIRECTION: 512 * defaultLayout(512).BIT_W(16) === 8192 > 6000 pre-fix.
    expect(512 * 16).toBeGreaterThan(PACKET_MAX_GRID_PX);
    expect(m.GRID_W).toBeLessThanOrEqual(PACKET_MAX_GRID_PX);
  });

  it('an absurd 65536-bit field stays paintable under the ceiling (w2-14)', () => {
    const spec = mk(32, [[['normal', 16], ['huge', 65536]]]);
    const m = computeGridMetrics(spec);
    expect(m.GRID_W).toBeLessThanOrEqual(PACKET_MAX_GRID_PX);
    expect(m.BIT_W).toBeGreaterThan(0);
  });

  it('a JSON spec that omits bitWidth (default 8) but carries 16-bit rows is enclosed (w3-01)', () => {
    const spec = mk(undefined, [[['Version', 4], ['IHL', 4], ['DSCP', 8]]]);
    const m = computeGridMetrics(spec);
    expect(m.gridBits).toBeGreaterThanOrEqual(16); // row sums to 16 in an 8-bit default
    expect(m.GRID_W).toBeGreaterThanOrEqual(16 * m.BIT_W);
  });
});

describe('D-453 resolvePacketDefinitionString — nested envelope is unwrapped', () => {
  const DSL = 'packet-beta\n0-3: "Version"\n4-7: "IHL"';

  it('unwraps { definition: { definition: "<dsl>" } } (w3-07)', () => {
    const rawSpec = { type: 'packet', definition: { definition: DSL } };
    // DIRECTION: the pre-fix gate `typeof rawSpec.definition === 'string'` is
    // false here, so the DSL/JSON recovery was skipped and the engine saw an
    // object with no sections.
    expect(typeof rawSpec.definition).toBe('object');
    expect(resolvePacketDefinitionString(rawSpec)).toBe(DSL);
    // ...and the recovered string bridges to a real spec.
    expect(parsePacketBetaDsl(resolvePacketDefinitionString(rawSpec)!)).not.toBeNull();
  });

  it('a direct definition string is returned unchanged', () => {
    expect(resolvePacketDefinitionString({ definition: DSL })).toBe(DSL);
  });

  it('a direct spec (no definition) yields undefined', () => {
    expect(resolvePacketDefinitionString({ type: 'packet', sections: [] })).toBeUndefined();
  });

  it('a definition object WITHOUT a string body yields undefined', () => {
    expect(resolvePacketDefinitionString({ definition: { sections: [] } })).toBeUndefined();
  });
});

describe('D-452 coextensiveLabelVShifts — co-extensive labels stagger vertically', () => {
  // Three co-extensive brackets: same y-span, distinct depths, all horizontal.
  const boxes: BracketLabelBox[] = [
    { depth: 0, horizontal: true, fontSize: 11, yMin: 40, yMax: 60 },
    { depth: 1, horizontal: true, fontSize: 11, yMin: 40, yMax: 60 },
    { depth: 2, horizontal: true, fontSize: 11, yMin: 40, yMax: 60 },
  ];

  it('each successive co-extensive horizontal label is pushed further down', () => {
    const s = coextensiveLabelVShifts(boxes, 4);
    // DIRECTION: pre-fix these three labels all sat at the same y (shift 0) and
    // overprinted; now each clears the previous one.
    expect(s[0]).toBe(0);
    expect(s[1]).toBeGreaterThan(0);
    expect(s[2]).toBeGreaterThan(s[1]);
  });

  it('non-overlapping labels are not shifted', () => {
    const apart: BracketLabelBox[] = [
      { depth: 0, horizontal: true, fontSize: 11, yMin: 0, yMax: 20 },
      { depth: 1, horizontal: true, fontSize: 11, yMin: 100, yMax: 120 },
    ];
    expect(coextensiveLabelVShifts(apart, 4)).toEqual([0, 0]);
  });

  it('rotated labels never stagger', () => {
    const rot: BracketLabelBox[] = [
      { depth: 0, horizontal: false, fontSize: 10, yMin: 40, yMax: 60 },
      { depth: 1, horizontal: false, fontSize: 10, yMin: 40, yMax: 60 },
    ];
    expect(coextensiveLabelVShifts(rot, 4)).toEqual([0, 0]);
  });

  it('same-depth overlaps are left to the x-shift (0 here)', () => {
    const same: BracketLabelBox[] = [
      { depth: 0, horizontal: true, fontSize: 11, yMin: 40, yMax: 60 },
      { depth: 0, horizontal: true, fontSize: 11, yMin: 40, yMax: 60 },
    ];
    expect(coextensiveLabelVShifts(same, 4)).toEqual([0, 0]);
  });
});

describe('D-454 resolveColor — explicit near-canvas triple gets a visible border per theme', () => {
  const FLOOR = 3; // WCAG graphical-object contrast floor
  const CANVAS_LIGHT = '#ffffff';
  const CANVAS_DARK = '#1e1e1e';

  it('near-white fill fails LIGHT canvas pre-fix, gets a >=3:1 border after (w3-09)', () => {
    const authored = { bg: '#FAFAFA', border: '#DDDDDD', text: '#333333' };
    // DIRECTION: authored border is invisible on the light page.
    expect(calculateContrastRatio(authored.border, CANVAS_LIGHT)).toBeLessThan(FLOOR);
    const r = resolveColor(authored, /* isDarkMode */ false, 0);
    expect(calculateContrastRatio(r.border, CANVAS_LIGHT)).toBeGreaterThanOrEqual(FLOOR);
  });

  it('near-black fill fails DARK canvas pre-fix, gets a >=3:1 border after (w3-10)', () => {
    const authored = { bg: '#1A1A1A', border: '#333333', text: '#EEEEEE' };
    expect(calculateContrastRatio(authored.border, CANVAS_DARK)).toBeLessThan(FLOOR);
    const r = resolveColor(authored, /* isDarkMode */ true, 0);
    expect(calculateContrastRatio(r.border, CANVAS_DARK)).toBeGreaterThanOrEqual(FLOOR);
  });

  it('asymmetry: near-white border already clears the DARK canvas → unchanged', () => {
    const authored = { bg: '#FAFAFA', border: '#DDDDDD', text: '#333333' };
    expect(calculateContrastRatio(authored.border, CANVAS_DARK)).toBeGreaterThanOrEqual(FLOOR);
    const r = resolveColor(authored, /* isDarkMode */ true, 0);
    expect(r.border).toBe('#DDDDDD');
  });

  it('asymmetry: near-black border already clears the LIGHT canvas → unchanged', () => {
    const authored = { bg: '#1A1A1A', border: '#333333', text: '#EEEEEE' };
    expect(calculateContrastRatio(authored.border, CANVAS_LIGHT)).toBeGreaterThanOrEqual(FLOOR);
    const r = resolveColor(authored, /* isDarkMode */ false, 0);
    expect(r.border).toBe('#333333');
  });

  it('the fill and text are preserved (only the border is corrected)', () => {
    const authored = { bg: '#FAFAFA', border: '#DDDDDD', text: '#333333' };
    const r = resolveColor(authored, false, 0);
    expect(r.bg).toBe('#FAFAFA');
    expect(r.text).toBe('#333333');
  });
});
