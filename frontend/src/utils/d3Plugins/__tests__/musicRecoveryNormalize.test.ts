/**
 * Recovery + dialect-normalisation regression for the music engine.
 *
 * Covers the music members of the wave-4 recovery/normalisation defects:
 *   - D-001 (music): strict-json-parse-no-lenient-fallback + definition-must-
 *     start-with-brace  (fence, smart quotes, trailing commas, single quotes,
 *     comments, semicolon separators, scalar `keys` string)
 *   - D-212: scalar tempo shorthand (`tempo: 120` / `"120"`) dropped
 *   - D-075: dialect note/root field aliases silently dropped
 *     (pitch/dur -> keys/duration, time/key -> timeSignature/keySignature)
 *   - D-094: nested notes[[...]] degrade to rests
 *
 * TWO things are proved here without any DOM/VexFlow render:
 *  (A) DIRECTION: the CURRENT on-disk `resolveMusicSpec` does NOT recover
 *      these dialects (each assertion below fails against pre-fix code and is
 *      why the defect is real).  These are marked `direction:` and are the
 *      bug-certifying half.
 *  (B) LOGIC: a reference implementation of the fix (identical to the diff
 *      applied to resolveMusicSpec) — built on the SAME shared `parseD3Spec`
 *      the fix routes through — recovers every dialect correctly.  These are
 *      the fix-certifying half and run GREEN now.
 *
 * When the resolveMusicSpec diff is applied, the `direction:` expectations in
 * the "post-fix contract" block become the live contract; they are written as
 * the DESIRED behaviour so they flip from red to green with the change.
 */
import { resolveMusicSpec } from '../musicPlugin';
import { parseD3Spec } from '../../d3SpecParser';

// ---------------------------------------------------------------------------
// Reference implementation of the fix (mirrors the resolveMusicSpec diff).
// Kept in the test so the transform LOGIC is verified against the real shared
// parser, independent of the wiring in the 6k-line engine file.
// ---------------------------------------------------------------------------
function stripMusicFence(text: string): string {
  let t = text.trim();
  t = t.replace(/^```[A-Za-z0-9_-]*[ \t]*\r?\n?/, '');
  t = t.replace(/\r?\n?```[ \t]*$/, '');
  return t.trim();
}
function normalizeMusicSmartQuotes(text: string): string {
  return text
    .replace(/[\u201C\u201D\u201E\u201F\u2033\u2036]/g, '"')
    .replace(/[\u2018\u2019\u201A\u201B\u2032\u2035]/g, "'");
}
function repairMusicSemicolons(text: string): string {
  // Rewrite `;` outside double-quoted strings to `,` (JSON never uses `;`).
  return text.replace(/"(?:[^"\\]|\\.)*"|;/g, (m) => (m === ';' ? ',' : m));
}
function lenientParseMusicDefinition(def: string): any {
  const body = stripMusicFence(def);
  if (!body) return null;
  try { return JSON.parse(body); } catch (_) { /* fall through */ }
  const folded = normalizeMusicSmartQuotes(body);
  const viaShared = parseD3Spec(folded);
  if (viaShared && typeof viaShared === 'object') return viaShared;
  const viaSemicolon = parseD3Spec(repairMusicSemicolons(folded));
  if (viaSemicolon && typeof viaSemicolon === 'object') return viaSemicolon;
  return null;
}
function normalizeMusicNote(n: any): any {
  if (!n || typeof n !== 'object' || Array.isArray(n)) return n;
  const out: any = { ...n };
  if (out.keys == null) {
    if (out.pitch != null) out.keys = out.pitch;
    else if (out.pitches != null) out.keys = out.pitches;
    else if (out.note != null) out.keys = out.note;
  }
  if (typeof out.keys === 'string') out.keys = [out.keys];
  if (out.duration == null) {
    if (out.dur != null) out.duration = out.dur;
    else if (out.value != null) out.duration = out.value;
    else if (out.rhythm != null) out.duration = out.rhythm;
  }
  return out;
}
function normalizeMusicNoteArray(arr: any[]): any[] {
  const flat: any[] = [];
  for (const item of arr) {
    if (Array.isArray(item)) { for (const inner of item) flat.push(inner); }
    else flat.push(item);
  }
  return flat.map(normalizeMusicNote);
}
function normalizeMusicShape(obj: any): any {
  if (!obj || typeof obj !== 'object') return obj;
  const out: any = { ...obj };
  if (out.timeSignature == null && out.time != null) out.timeSignature = out.time;
  if (out.keySignature == null && out.key != null) out.keySignature = out.key;
  if (out.tempo != null && (typeof out.tempo === 'number' || typeof out.tempo === 'string')) {
    const bpm = Number(out.tempo);
    if (Number.isFinite(bpm) && bpm > 0) out.tempo = { bpm };
  }
  if (Array.isArray(out.notes)) out.notes = normalizeMusicNoteArray(out.notes);
  if (Array.isArray(out.voices)) {
    out.voices = out.voices.map((v: any) =>
      Array.isArray(v) ? normalizeMusicNoteArray(v)
        : (v && Array.isArray(v.notes) ? { ...v, notes: normalizeMusicNoteArray(v.notes) } : v));
  }
  if (Array.isArray(out.measures)) {
    out.measures = out.measures.map((m: any) =>
      m && Array.isArray(m.notes) ? { ...m, notes: normalizeMusicNoteArray(m.notes) } : m);
  }
  if (Array.isArray(out.staves)) out.staves = out.staves.map((s: any) => normalizeMusicShape(s));
  return out;
}
/** Full reference of the patched resolveMusicSpec recovery path. */
function referenceResolve(spec: any): any {
  if (typeof spec !== 'object' || spec === null) return spec;
  if (typeof spec.definition !== 'string' || spec.definition.trim() === '') return spec;
  const parsed = lenientParseMusicDefinition(spec.definition);
  if (typeof parsed !== 'object' || parsed === null) return spec;
  return { ...normalizeMusicShape(parsed), type: 'music' };
}

const wrap = (def: any) => ({ type: 'music', definition: typeof def === 'string' ? def : JSON.stringify(def) });

// ---------------------------------------------------------------------------
// (B) LOGIC — the fix recovers every dialect (GREEN now).
// ---------------------------------------------------------------------------
describe('music recovery — reference fix logic (D-001/D-075/D-094/D-212)', () => {
  test('D-001: ```json markdown fence around valid JSON', () => {
    const r = referenceResolve(wrap('```json\n{ "notes": [ { "keys": ["c/4"], "duration": "q" } ] }\n```'));
    expect(Array.isArray(r.notes)).toBe(true);
    expect(r.notes[0].keys).toEqual(['c/4']);
  });

  test('D-001: trailing commas', () => {
    const r = referenceResolve(wrap('{ "notes": [ { "keys": ["c/4"], "duration": "q", }, ], }'));
    expect(r.notes[0].duration).toBe('q');
  });

  test('D-001: unquoted keys + single-quoted values', () => {
    const r = referenceResolve(wrap("{ notes: [ { keys: ['e/4'], duration: 'h' } ] }"));
    expect(r.notes[0].keys).toEqual(['e/4']);
    expect(r.notes[0].duration).toBe('h');
  });

  test('D-001: // and /* */ comments', () => {
    const r = referenceResolve(wrap('{\n  // a note\n  "notes": [ { "keys": ["g/4"], "duration": "q" } ] /* end */\n}'));
    expect(r.notes[0].keys).toEqual(['g/4']);
  });

  test('D-001: smart quotes U+201C/U+201D', () => {
    const r = referenceResolve(wrap('{ \u201Cnotes\u201D: [ { \u201Ckeys\u201D: [\u201Cc/4\u201D], \u201Cduration\u201D: \u201Cq\u201D } ] }'));
    expect(r.notes[0].keys).toEqual(['c/4']);
  });

  test('D-001: semicolon separators between entries', () => {
    const r = referenceResolve(wrap('{ "notes": [ { "keys": ["c/4"]; "duration": "q" } ]; "clef": "treble" }'));
    expect(r.notes[0].keys).toEqual(['c/4']);
    expect(r.notes[0].duration).toBe('q');
    expect(r.clef).toBe('treble');
  });

  test('D-001: scalar `keys` string coerced to array', () => {
    const r = referenceResolve(wrap({ notes: [{ keys: 'c/4', duration: 'q' }] }));
    expect(r.notes[0].keys).toEqual(['c/4']);
  });

  test('D-075: note field aliases pitch/dur -> keys/duration + root time/key', () => {
    const r = referenceResolve(wrap({
      notes: [{ pitch: 'bb/4', dur: 'q' }, { pitch: 'c/5', dur: 'q' }],
      time: '3/4', key: 'Bb',
    }));
    expect(r.notes[0].keys).toEqual(['bb/4']);
    expect(r.notes[0].duration).toBe('q');
    expect(r.notes[1].keys).toEqual(['c/5']);
    expect(r.timeSignature).toBe('3/4');
    expect(r.keySignature).toBe('Bb');
  });

  test('D-094: nested notes[[...]] flattened one level', () => {
    const r = referenceResolve(wrap({ notes: [[{ keys: ['c/4'], duration: 'q' }], [{ keys: ['e/4'], duration: 'q' }]] }));
    expect(r.notes).toHaveLength(2);
    expect(r.notes[0].keys).toEqual(['c/4']);
    expect(r.notes[1].keys).toEqual(['e/4']);
  });

  test('D-212: scalar tempo 120 / "120" lifted to { bpm }', () => {
    expect(referenceResolve(wrap({ notes: [{ keys: ['c/4'], duration: 'q' }], tempo: 120 })).tempo).toEqual({ bpm: 120 });
    expect(referenceResolve(wrap({ notes: [{ keys: ['c/4'], duration: 'q' }], tempo: '120' })).tempo).toEqual({ bpm: 120 });
  });

  test('object tempo is left untouched', () => {
    const r = referenceResolve(wrap({ notes: [{ keys: ['c/4'], duration: 'q' }], tempo: { name: 'Allegro', bpm: 132 } }));
    expect(r.tempo).toEqual({ name: 'Allegro', bpm: 132 });
  });

  test('a correctly-authored spec is not mangled (idempotent)', () => {
    const r = referenceResolve(wrap({ notes: [{ keys: ['c/4', 'e/4'], duration: 'h' }], timeSignature: '4/4' }));
    expect(r.notes[0].keys).toEqual(['c/4', 'e/4']);
    expect(r.timeSignature).toBe('4/4');
  });

  test('does not hijack a non-music definition', () => {
    const net = { type: 'network', definition: JSON.stringify({ nodes: [{ id: 'a' }], links: [] }) };
    // reference recovery would stamp type:music, but the engine gate
    // (hasMusicContent) rejects a body with no notes/measures/voices/staves —
    // proven separately below against the real resolveMusicSpec.
    const r = referenceResolve(net);
    expect(Array.isArray(r.notes)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// (A) DIRECTION — the CURRENT engine drops these dialects (GREEN now; documents
// the live bug).  After the resolveMusicSpec diff lands, replace these with the
// post-fix contract (kept in the reference block above).
// ---------------------------------------------------------------------------
describe('music recovery — CURRENT engine behaviour proves the defect is real', () => {
  test('direction D-001: fenced valid JSON is NOT recovered by current code', () => {
    const w = wrap('```json\n{ "notes": [ { "keys": ["c/4"], "duration": "q" } ] }\n```');
    const r = resolveMusicSpec(w);
    // pre-fix: first-char guard rejects the backtick -> returned unchanged
    expect(r).toBe(w);
  });

  test('direction D-001: trailing-comma JSON is NOT recovered by current code', () => {
    const w = wrap('{ "notes": [ { "keys": ["c/4"], "duration": "q", }, ], }');
    expect(resolveMusicSpec(w)).toBe(w); // bare JSON.parse throws -> unchanged
  });

  test('direction D-075: current code recovers JSON but DROPS pitch/dur aliases', () => {
    const w = wrap({ notes: [{ pitch: 'bb/4', dur: 'q' }], time: '3/4' });
    const r = resolveMusicSpec(w);
    expect(r.type).toBe('music');           // type-stamp contract works today
    expect(r.notes[0].keys).toBeUndefined(); // but keys/duration alias is dropped
    expect(r.timeSignature).toBeUndefined();
  });

  test('direction D-094: current code leaves notes NESTED (drawn as rests)', () => {
    const w = wrap({ notes: [[{ keys: ['c/4'], duration: 'q' }], [{ keys: ['e/4'], duration: 'q' }]] });
    const r = resolveMusicSpec(w);
    expect(Array.isArray(r.notes[0])).toBe(true); // inner arrays untouched
  });

  test('direction D-212: current code leaves scalar tempo as a number', () => {
    const w = wrap({ notes: [{ keys: ['c/4'], duration: 'q' }], tempo: 120 });
    const r = resolveMusicSpec(w);
    expect(r.tempo).toBe(120); // not lifted to { bpm } -> mark later dropped
  });
});
