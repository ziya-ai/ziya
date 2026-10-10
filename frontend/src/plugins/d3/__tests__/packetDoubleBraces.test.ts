/**
 * Regression: packet definitions emitted with f-string brace escaping
 * (`{{` / `}}` for every `{` / `}`) failed with "Invalid JSON in definition".
 * Both strict JSON and json5 reject `{{`. lenientParsePacketJson now
 * collapses uniformly-escaped braces as a last resort.
 *
 * Fails pre-fix: collapsePacketDoubleBraces did not exist and
 * lenientParsePacketJson returned undefined for both definitions below.
 */
import {
  lenientParsePacketJson,
  collapsePacketDoubleBraces,
} from '../packetPlugin';

// Verbatim from the failing renders.
const RX_SPOT_LABEL = String.raw`{{"title": "RxSpotLabel (uint16, LUT key)", "subtitle": "GetRxSpotLabel_(modemId, spotId, startQuanta [+1 when deferred])", "bitWidth": 16,
 "sections": [{{"label": "Label\n<2B>", "rows": [[["reserved (0)", 4, "reserved"], ["Q", 1, "highlight"], ["Modem ID", 4, "control"], ["Spot ID", 7, "network"]]],
   "brackets": [{{"start_row": 0, "end_row": 0, "label": "zeroed by = {{}}", "side": "right"}}]}}]}}`;

const CELL_ID_LUT = String.raw`{{"title": "Cell ID LUT entry (nineBytePhaserTransportFlag_ = true)", "subtitle": "{{6'd0, SPOT_ID[4:0], CELL_ID[20:0]}}", "bitWidth": 32,
 "sections": [{{"label": "Value\n<4B>", "rows": [[["0", 6, "reserved"], ["SPOT_ID[4:0]", 5, "accent"], ["CELL_ID[20:0]", 21, "payload"]]]}}]}}`;

describe('lenientParsePacketJson — f-string double braces', () => {
  it('control: strict JSON rejects both definitions', () => {
    expect(() => JSON.parse(RX_SPOT_LABEL)).toThrow();
    expect(() => JSON.parse(CELL_ID_LUT)).toThrow();
  });

  it('recovers RxSpotLabel with sections, rows and brackets intact', () => {
    const spec = lenientParsePacketJson(RX_SPOT_LABEL);
    expect(spec).toBeDefined();
    expect(spec.bitWidth).toBe(16);
    expect(spec.sections).toHaveLength(1);
    expect(spec.sections[0].label).toBe('Label\n<2B>');
    expect(spec.sections[0].rows[0].map((f: any[]) => f[1])).toEqual([4, 1, 4, 7]);
    expect(spec.sections[0].brackets[0]).toEqual({
      start_row: 0, end_row: 0, label: 'zeroed by = {}', side: 'right',
    });
  });

  it('recovers Cell ID LUT and un-escapes braces inside strings', () => {
    const spec = lenientParsePacketJson(CELL_ID_LUT);
    expect(spec).toBeDefined();
    expect(spec.subtitle).toBe("{6'd0, SPOT_ID[4:0], CELL_ID[20:0]}");
    expect(spec.sections[0].rows[0].map((f: any[]) => f[1])).toEqual([6, 5, 21]);
  });
});

describe('collapsePacketDoubleBraces — guards', () => {
  it('returns null for a body that does not open with {{', () => {
    expect(collapsePacketDoubleBraces('{"a":{"b":1}}')).toBeNull();
    expect(collapsePacketDoubleBraces('')).toBeNull();
  });

  it('valid JSON with nested }} still parses unchanged (collapse never runs)', () => {
    const raw = '{"title":"T","sections":[{"label":"x","rows":[[["a",8,"payload"]]]}]}';
    expect(lenientParsePacketJson(raw)).toEqual(JSON.parse(raw));
  });

  it('unrecoverable input is still undefined', () => {
    expect(lenientParsePacketJson('{{ not json at all')).toBeUndefined();
  });
});
