/**
 * @jest-environment jsdom
 *
 * D-174 (regression) — the music PLUGIN render entry pre-empted the shared
 * lenient recovery.
 *
 * frontend/src/plugins/d3/musicPlugin.ts render() has a guard that, for a
 * `definition` string starting with `{` on an as-yet-unclaimed wrapper, reports
 * "Invalid JSON in definition" so a malformed body gets an explicit error card
 * rather than a misleading "requires a notes array" one. That guard used a
 * STRICT `JSON.parse`, which throws for exactly the class of near-miss specs the
 * recovery path (resolveMusicSpec -> lenientParse) is designed to fix — trailing
 * commas, unquoted keys, single/smart quotes, comments, semicolon separators —
 * so every recoverable music-w4 spec errored BEFORE resolveMusicSpec ran. That
 * regressed music-w4-01/02/03/05/06/07 to a total loss (signature
 * strict-json-guard-preempts-lenient-parse).
 *
 * The fix swaps the strict `JSON.parse` in that guard for the shared
 * `lenientParse`: only a definition even the lenient parser cannot recover is a
 * genuine "Invalid JSON" error; a recoverable body flows on to resolveMusicSpec.
 *
 * Direction is pinned by mocking renderMusicSpec to a no-op (so VexFlow is not
 * exercised headlessly) and asserting the render entry does NOT emit the
 * strict-JSON error card for a recoverable spec. On the UNPATCHED tree the guard
 * fires and stamps data-diagram-error="Invalid JSON in definition"; with the fix
 * it does not. Asserted in BOTH themes, since the recovery is theme-independent
 * and the both-themes contract applies.
 */

// vexflow 5.0.0 uses structuredClone; jest's jsdom on Node 20 may not expose it.
if (typeof (globalThis as any).structuredClone !== 'function') {
  (globalThis as any).structuredClone = (v: any) =>
    (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
}

// Stub ONLY renderMusicSpec so the render entry can be driven without VexFlow;
// isMusicSpec / resolveMusicSpec / degenerateMusicBody stay real so the guard
// and the recovery path are exercised for real.
jest.mock('../../../utils/d3Plugins/musicPlugin', () => {
  const actual = jest.requireActual('../../../utils/d3Plugins/musicPlugin');
  return { __esModule: true, ...actual, renderMusicSpec: jest.fn().mockResolvedValue(undefined) };
});

import { musicPlugin } from '../musicPlugin';

// The exact malformed music-w4 recovery bodies (as `definition` strings).
const W4: Record<string, string> = {
  'w4-01': '{"timeSignature": "4/4", "clef": "treble", "keySignature": "G",\n'
    + ' "title": "Trailing Commas",\n "notes": [\n'
    + '   {"keys": ["g/4"], "duration": "q",},\n'
    + '   {"keys": ["a/4"], "duration": "q"},\n ],\n}',
  'w4-02': '{\n  type: \'music\',\n  timeSignature: \'4/4\',\n  clef: \'treble\',\n'
    + '  title: \'Unquoted Keys\',\n  notes: [\n    {keys: [\'c/4\'], duration: \'8\'},\n'
    + '    {keys: [\'g/4\'], duration: \'q\'}\n  ]\n}',
  'w4-03': '{\'timeSignature\': \'3/4\', \'clef\': \'bass\', \'keySignature\': \'F\','
    + ' \'title\': \'Single Quoted Keys\', \'notes\': [{\'keys\': [\'f/3\'], \'duration\': \'q\'},'
    + ' {\'keys\': [\'a/3\'], \'duration\': \'q\'}, {\'keys\': [\'c/4\'], \'duration\': \'h\'}]}',
  'w4-05': '{\n  // a cadence\n  "timeSignature": "4/4",\n  "clef": "treble",\n'
    + '  "title": "Comments Inside JSON",\n  /* block */\n  "notes": [\n'
    + '    {"keys": ["c/4"], "duration": "q"}   // tonic\n  ]\n}',
  'w4-06': '{\n  \u201CtimeSignature\u201D: \u201C4/4\u201D,\n  \u201Cclef\u201D: \u201Ctreble\u201D,\n'
    + '  \u201Ctitle\u201D: \u201CSmart Quotes\u201D,\n  \u201Cnotes\u201D: [\n'
    + '    {\u201Ckeys\u201D: [\u201Ce/4\u201D], \u201Cduration\u201D: \u201Cq\u201D}\n  ]\n}',
  'w4-07': '{\n  "timeSignature": "4/4";\n  "clef": "treble";\n  "title": "Semicolon Separators";\n'
    + '  "notes": [\n    {"keys": ["a/4"], "duration": "q"};\n    {"keys": ["c/5"], "duration": "q"}\n  ]\n}',
};

const errorMsg = (container: HTMLElement): string | null => {
  const el = container.querySelector('[data-diagram-error]');
  return el ? el.getAttribute('data-diagram-error') : null;
};

const d3Stub = { select: () => ({} as any) };

describe('D-174 — music render guard uses lenient recovery, not strict JSON.parse', () => {
  for (const [name, definition] of Object.entries(W4)) {
    for (const isDark of [false, true]) {
      it(`recovers music-${name} without a strict-JSON error card (${isDark ? 'dark' : 'light'})`, async () => {
        const container = document.createElement('div');
        document.body.appendChild(container);
        await musicPlugin.render(container, d3Stub, { type: 'music', definition }, isDark);
        // The strict guard would have stamped exactly this message and bailed.
        expect(errorMsg(container)).not.toBe('Invalid JSON in definition');
      });
    }
  }

  it('still reports a genuinely unrecoverable definition as an Invalid JSON error', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    // Unbalanced braces / garbage the lenient parser cannot rescue.
    await musicPlugin.render(
      container, d3Stub,
      { type: 'music', definition: '{ this is not json at all "notes"' },
      false,
    );
    expect(errorMsg(container)).toBe('Invalid JSON in definition');
  });
});
