/**
 * @jest-environment jsdom
 *
 * Regression tests for KEYED-OBJECT / bare-array `voices` recognition and
 * normalisation (Issue 43).
 *
 * A multi-voice staff can spell its `voices` field three ways:
 *   - array of voice objects     `[{notes:[...]}, ...]`   (canonical; the only
 *                                shape the render core indexed)
 *   - array of bare note arrays  `[[...], [...]]`
 *   - keyed object               `{"1":[...], "2":[...]}` (voice-NUMBER keyed,
 *                                the MusicXML `<voice>` numbering convention,
 *                                and the shape the Issue-43 stress spec used)
 *
 * The keyed-object form matched NO branch of `voicesHaveNotes` (which required
 * `Array.isArray(voices)`), so `isMusicSpec` returned false, `canHandle`
 * returned false, and the D3Renderer reported "No compatible plugin found for
 * spec: {type: music}" and retried to the 30s timeout with zero output.  A
 * separate hazard: once detection is widened, the render core reads
 * `staffSpec.voices?.[0]`, which is `undefined` for a keyed object -> an empty
 * staff.  `normalizeVoicesShape` + `normalizeStaffVoiceShape` close both.
 *
 * These import the REAL module (never re-implement its logic) and pin BOTH
 * directions: object/bare-array voices are recognised AND already-canonical
 * shapes are returned BY REFERENCE (byte-identical), empty voices are still
 * rejected, and non-music specs are still declined.
 */
import {
  normalizeVoicesShape,
  isMusicSpec,
} from '../../../utils/d3Plugins/musicPlugin';
import { musicPlugin } from '../musicPlugin';
import { findPluginForSpec } from '../registry';

const NOTE = { keys: ['c/5'], duration: 'q' };
const NOTE2 = { keys: ['e/4'], duration: 'q' };

describe('normalizeVoicesShape', () => {
  it('converts a keyed-object of bare note arrays to voice objects, ordered by numeric key', () => {
    const out = normalizeVoicesShape({ '2': [NOTE2], '1': [NOTE] });
    expect(Array.isArray(out)).toBe(true);
    expect(out).toHaveLength(2);
    // numeric ordering: voice "1" must come first (the primary line)
    expect(out![0]).toEqual({ notes: [NOTE] });
    expect(out![1]).toEqual({ notes: [NOTE2] });
  });

  it('converts keyed-object values that are already voice objects', () => {
    const out = normalizeVoicesShape({ '1': { notes: [NOTE] }, '2': { notes: [NOTE2] } });
    expect(out).toHaveLength(2);
    expect(out![0]).toEqual({ notes: [NOTE] });
  });

  it('preserves insertion order for non-numeric keys', () => {
    const out = normalizeVoicesShape({ soprano: [NOTE], alto: [NOTE2] });
    expect(out![0]).toEqual({ notes: [NOTE] });
    expect(out![1]).toEqual({ notes: [NOTE2] });
  });

  it('wraps an array of bare note-arrays as voice objects', () => {
    const out = normalizeVoicesShape([[NOTE], [NOTE2]]);
    expect(out![0]).toEqual({ notes: [NOTE] });
    expect(out![1]).toEqual({ notes: [NOTE2] });
  });

  it('returns an already-canonical array of voice objects BY REFERENCE (byte-identical)', () => {
    const canonical = [{ notes: [NOTE] }, { notes: [NOTE2] }];
    // Reference equality proves an existing spec is not rewritten -- guards
    // against this becoming a catch-all that reallocates every voices field.
    expect(normalizeVoicesShape(canonical)).toBe(canonical);
  });

  it('returns undefined for a missing/non-object voices field', () => {
    expect(normalizeVoicesShape(undefined)).toBeUndefined();
    expect(normalizeVoicesShape(null)).toBeUndefined();
    expect(normalizeVoicesShape(42)).toBeUndefined();
    expect(normalizeVoicesShape('x')).toBeUndefined();
  });

  it('returns an empty array for an empty keyed object', () => {
    expect(normalizeVoicesShape({})).toEqual([]);
  });
});

describe('isMusicSpec with keyed-object voices', () => {
  it('accepts a single-staff spec whose top-level voices is a keyed object (the Issue-43 shape)', () => {
    // Pre-fix this returned false -> canHandle false -> "No compatible plugin".
    expect(isMusicSpec({ type: 'music', voices: { '1': [NOTE], '2': [NOTE2] } })).toBe(true);
  });

  it('accepts a single-staff spec whose top-level voices is a bare-array-of-note-arrays', () => {
    expect(isMusicSpec({ type: 'music', voices: [[NOTE], [NOTE2]] })).toBe(true);
  });

  it('accepts a grand staff whose stave carries keyed-object voices', () => {
    expect(isMusicSpec({
      type: 'music',
      staves: [{ clef: 'treble', voices: { '1': [NOTE] } }],
    })).toBe(true);
  });

  it('still REJECTS an empty keyed-object voices (nothing to draw)', () => {
    // Guard: widening the predicate must not admit content-free specs.
    expect(isMusicSpec({ type: 'music', voices: {} })).toBe(false);
  });

  it('still REJECTS a keyed-object voices whose voices are all empty arrays', () => {
    expect(isMusicSpec({ type: 'music', voices: { '1': [], '2': [] } })).toBe(false);
  });

  it('still REJECTS a non-music type carrying keyed-object voices', () => {
    expect(isMusicSpec({ type: 'graphviz', voices: { '1': [NOTE] } })).toBe(false);
  });

  it('still ACCEPTS a canonical single-staff notes spec (no regression)', () => {
    expect(isMusicSpec({ type: 'music', notes: [NOTE] })).toBe(true);
  });
});

describe('plugin selection with keyed-object voices', () => {
  const SPEC = { type: 'music', voices: { '1': [NOTE], '2': [NOTE2] } };

  it('musicPlugin.canHandle claims a keyed-object-voices spec', () => {
    expect(musicPlugin.canHandle(SPEC)).toBe(true);
  });

  it('claims a keyed-object-voices spec delivered as a JSON definition string', () => {
    expect(musicPlugin.canHandle({ definition: JSON.stringify(SPEC) })).toBe(true);
  });

  it('registry resolves a keyed-object-voices spec to the music plugin (no more "no plugin found")', async () => {
    const plugin = await findPluginForSpec(SPEC);
    expect(plugin?.name).toBe('music-renderer');
  });
});
