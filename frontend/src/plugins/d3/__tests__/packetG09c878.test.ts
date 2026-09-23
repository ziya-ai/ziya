/**
 * G-09c878 packet structural fixes (iteration 12).
 *
 * Direction is asserted for every case: the pre-fix behaviour is reconstructed
 * and shown to be wrong BEFORE the post-fix helper is asserted correct, so a
 * test that would also pass against unpatched code cannot masquerade as a fix.
 * Pure helpers only (no DOM / no d3), matching the other packet unit tests.
 *
 *  - D-453  unwrapNestedPacketDefinition bridges a packet-beta DSL that the
 *           server's normalize_spec_definition json.dumps() into a DOUBLE-
 *           ENCODED string envelope `'{"definition":"packet-beta …"}'`. The
 *           earlier resolvePacketDefinitionString fix handled only the OBJECT
 *           envelope shape, which the server never actually delivers — after
 *           json.dumps the plugin sees a string, lenientParsePacketJson yields
 *           `{definition: dsl}` (no sections), and render() hit the "requires a
 *           sections array" error card (blank capture).
 */
import {
  parsePacketBetaDsl,
  lenientParsePacketJson,
  unwrapNestedPacketDefinition,
} from '../packetPlugin';
import { normalizePacketSpec } from '../../../utils/d3Plugins/packetPlugin';

const DSL =
  'packet-beta\n0-3: "Version"\n4-7: "IHL"\n8-15: "Type of Service"\n' +
  '16-31: "Total Length"\n32-47: "Identification"\n48-50: "Flags"\n' +
  '51-63: "Fragment Offset"';

// What the plugin actually receives for packet-w3-07: the author wrote
// { type:'packet', definition:{ definition:"packet-beta …" } }, and the server's
// normalize_spec_definition json.dumps() the object `definition`, so it crosses
// to the frontend as a STRING.
const DOUBLE_ENCODED = JSON.stringify({ definition: DSL });

describe('D-453 double-encoded {definition:{definition:dsl}} envelope', () => {
  it('DIRECTION: the raw parse yields an envelope with no sections (pre-fix)', () => {
    // The definition string starts with `{`, so the DSL sniff never fires and
    // the JSON parse returns the inner envelope object — which carries no
    // sections, so render() would reach the "requires a sections array" guard.
    expect(parsePacketBetaDsl(DOUBLE_ENCODED)).toBeNull();
    const parsed = lenientParsePacketJson(DOUBLE_ENCODED);
    expect(parsed).toEqual({ definition: DSL });
    expect(normalizePacketSpec(parsed)?.sections?.length ?? 0).toBe(0);
  });

  it('unwrapNestedPacketDefinition bridges the inner DSL to a real spec', () => {
    const parsed = lenientParsePacketJson(DOUBLE_ENCODED);
    const spec = unwrapNestedPacketDefinition(parsed);
    // The inner DSL is now bridged: it is a loose PacketSpec with fields.
    expect((spec as any).type).toBe('packet');
    expect(Array.isArray((spec as any).fields)).toBe(true);
    expect((spec as any).fields.length).toBe(7);
    // …and normalizePacketSpec turns those fields into the sections render()
    // requires, so the "sections array" guard is cleared.
    const normalized = normalizePacketSpec(spec);
    expect(normalized?.sections?.length ?? 0).toBeGreaterThan(0);
  });

  it('an even-deeper nested string envelope is still unwrapped', () => {
    const doubleNested = JSON.stringify({ definition: JSON.stringify({ definition: DSL }) });
    const parsed = lenientParsePacketJson(doubleNested);
    const spec = unwrapNestedPacketDefinition(parsed);
    expect((spec as any).fields?.length).toBe(7);
  });

  it('a direct spec with real sections is returned untouched', () => {
    const direct = { type: 'packet', title: 'T', sections: [{ label: 'S', rows: [[['a', 8]]] }] };
    expect(unwrapNestedPacketDefinition(direct)).toBe(direct);
  });

  it('a flat {fields:[...]} spec (no definition key) is left for normalizePacketSpec', () => {
    const flat = { type: 'packet', fields: [['a', 8], ['b', 8]] };
    expect(unwrapNestedPacketDefinition(flat)).toBe(flat);
  });

  it('a self-referential envelope cannot loop', () => {
    const loop: any = {};
    loop.definition = loop;
    // Bounded descent returns the object rather than hanging.
    expect(() => unwrapNestedPacketDefinition(loop)).not.toThrow();
  });
});
