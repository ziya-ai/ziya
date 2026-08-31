/**
 * G-32 packet recovery regression tests.
 *
 * Covers three shared-root-cause defects in the packet string entry point,
 * all previously aborting at the single bare `JSON.parse` / rigid DSL sniff
 * and burning the full 30s capture timeout with no <svg>:
 *
 *  - D-214 no-lenient-json-parse : trailing commas / unquoted keys / single
 *    quotes / smart quotes / comments + trailing `;`.
 *  - D-215 markdown-fence-not-stripped : ```json JSON and ```mermaid DSL.
 *  - D-216 dsl-relative-width-syntax-unsupported : mermaid v11 `+N:` widths.
 *
 * Every case asserts DIRECTION: the raw input is shown to defeat the
 * pre-fix path (strict JSON.parse throws, or the `/^packet/` sniff / absolute
 * range regex matches nothing) BEFORE asserting the new helper recovers it —
 * so a test that would pass against unpatched code cannot masquerade as a fix.
 * Pure helpers only (no DOM / no d3), matching the other packet unit tests.
 */
import {
  lenientParsePacketJson,
  parsePacketBetaDsl,
  stripPacketFence,
  normalizePacketSmartQuotes,
} from '../packetPlugin';

// The absolute START-END pattern the DSL bridge used BEFORE D-216 — used only
// to prove `+N:` lines matched nothing pre-fix.
const OLD_ABSOLUTE_RANGE = /^(-?\d+)\s*-\s*(-?\d+)\s*:\s*([\s\S]*)$/;

describe('D-214 lenientParsePacketJson — near-miss JSON is recovered', () => {
  const valid = { type: 'packet', title: 'T', fields: [{ name: 'a', bits: 8 }] };

  it('happy path: strict-valid JSON is parsed unchanged (fix is not lossy)', () => {
    const raw = JSON.stringify(valid);
    expect(() => JSON.parse(raw)).not.toThrow(); // control
    expect(lenientParsePacketJson(raw)).toEqual(valid);
  });

  it('recovers a trailing comma (w4-01)', () => {
    const raw = '{"type":"packet","title":"T","fields":[{"name":"a","bits":8},]}';
    expect(() => JSON.parse(raw)).toThrow();            // pre-fix: dies here
    expect(lenientParsePacketJson(raw)).toEqual(valid); // post-fix: recovered
  });

  it('recovers unquoted keys (w4-02)', () => {
    const raw = '{type:"packet",title:"T",fields:[{name:"a",bits:8}]}';
    expect(() => JSON.parse(raw)).toThrow();
    expect(lenientParsePacketJson(raw)).toEqual(valid);
  });

  it('recovers single-quoted strings (w4-03)', () => {
    const raw = "{'type':'packet','title':'T','fields':[{'name':'a','bits':8}]}";
    expect(() => JSON.parse(raw)).toThrow();
    expect(lenientParsePacketJson(raw)).toEqual(valid);
  });

  it('recovers smart / curly quotes (w4-12)', () => {
    const raw = '{\u201Ctype\u201D:\u201Cpacket\u201D,\u201Ctitle\u201D:\u201CT\u201D,\u201Cfields\u201D:[{\u201Cname\u201D:\u201Ca\u201D,\u201Cbits\u201D:8}]}';
    expect(() => JSON.parse(raw)).toThrow();
    // json5 itself would still reject the smart quotes without normalisation:
    expect(normalizePacketSmartQuotes(raw)).not.toContain('\u201C');
    expect(lenientParsePacketJson(raw)).toEqual(valid);
  });

  it('recovers // comments and a trailing semicolon (w4-14)', () => {
    const raw = '{\n  // a header comment\n  "type":"packet","title":"T",\n  "fields":[{"name":"a","bits":8}]\n};';
    expect(() => JSON.parse(raw)).toThrow();
    expect(lenientParsePacketJson(raw)).toEqual(valid);
  });

  it('returns undefined for genuinely unrecoverable prose (no hijack / no false-positive)', () => {
    expect(lenientParsePacketJson('this is not a packet at all')).toBeUndefined();
    expect(lenientParsePacketJson('')).toBeUndefined();
  });
});

describe('D-215 markdown fence is stripped before parse', () => {
  const valid = { type: 'packet', title: 'T', fields: [{ name: 'a', bits: 8 }] };

  it('recovers byte-valid JSON inside a ```json fence (w4-04)', () => {
    const raw = '```json\n{"type":"packet","title":"T","fields":[{"name":"a","bits":8}]}\n```';
    expect(() => JSON.parse(raw)).toThrow();                     // leading backticks
    const stripped = stripPacketFence(raw);
    expect(stripped).not.toContain('`');                         // fence removed
    expect(JSON.parse(stripped)).toEqual(valid);                 // inner is byte-valid
    expect(lenientParsePacketJson(raw)).toEqual(valid);
  });

  it('recovers valid packet-beta DSL inside a ```mermaid fence (w4-05)', () => {
    const raw = '```mermaid\npacket-beta\n  title Frame\n  0-7: "A"\n  8-15: "B"\n```';
    // Pre-fix: the `/^packet/` sniff ran on the trimmed FENCED text (starts
    // with backticks), so the bridge never fired.
    expect(/^packet(-beta)?/.test(raw.trim())).toBe(false);
    const dsl = parsePacketBetaDsl(raw);
    expect(dsl).not.toBeNull();
    expect(dsl!.title).toBe('Frame');
    expect(dsl!.fields).toEqual([{ name: 'A', bits: 8 }, { name: 'B', bits: 8 }]);
  });
});

describe('D-216 mermaid v11 relative `+N:` widths', () => {
  it('parses `+N:` lines the absolute-range regex could never match', () => {
    const raw = 'packet-beta\n  title V11\n  +8: "Src"\n  +16: "Dst"';
    // Direction: each field line matched nothing under the old pattern.
    for (const line of ['+8: "Src"', '+16: "Dst"']) {
      expect(OLD_ABSOLUTE_RANGE.test(line.trim())).toBe(false);
    }
    const dsl = parsePacketBetaDsl(raw);
    expect(dsl).not.toBeNull();
    expect(dsl!.fields).toEqual([{ name: 'Src', bits: 8 }, { name: 'Dst', bits: 16 }]);
  });

  it('mixes absolute ranges and relative widths in one diagram', () => {
    const raw = 'packet-beta\n  0-3: "Ver"\n  +12: "Len"';
    const dsl = parsePacketBetaDsl(raw);
    expect(dsl!.fields).toEqual([{ name: 'Ver', bits: 4 }, { name: 'Len', bits: 12 }]);
  });

  it('clamps degenerate relative widths to [1, 512]', () => {
    const raw = 'packet-beta\n  +0: "zero"\n  +99999: "huge"';
    const dsl = parsePacketBetaDsl(raw);
    expect(dsl!.fields).toEqual([{ name: 'zero', bits: 1 }, { name: 'huge', bits: 512 }]);
  });
});
