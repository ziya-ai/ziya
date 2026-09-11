/**
 * Fix group G-bf4b3a (engine: music, suspect musicPlugin.ts).
 *
 * D-176 (no-field-alias-layer) is the net-new source fix: the `showMeasureNumbers`
 * / per-measure `barline` / measure-level `chords` / string-boolean dialect
 * spellings (music-w4-15) were dropped by normalizeMusicShape, shipping a
 * legible-but-degraded score as "success".  The w4-15 block below FAILS on the
 * pre-fix code (measureNumbers undefined, barline never mapped to endBar,
 * chords dropped, autoBeam left as the string "true").
 *
 * D-168/169/170/175/177 were already repaired in source under the prior sweep
 * (D-145/146/151/152/250); these assertions lock that behaviour so a rebuild
 * carries it into the headless bundle and a later regression cannot silently
 * undo it.  Every case here mirrors the exact spec on disk under
 * .ziya/gfx-sweep/specs/music/.
 */
import {
  resolveMusicSpec,
  normalizeMusicShape,
  degenerateMusicBody,
  resolveScoreEdgeBarlines,
  multiVoiceRestPitch,
  coerceMusicFlag,
} from '../musicPlugin';

describe('G-bf4b3a music dialect aliasing / recovery', () => {
  // ---- D-176: field-alias layer (music-w4-15 is the failing-without case) ----
  describe('D-176 dialect field aliases', () => {
    it('w4-15: showMeasureNumbers/barline/chords/string-bool are canonicalised', () => {
      const spec = {
        type: 'music',
        definition: {
          time: '4/4',
          timeSignature: '4/4',
          clef: 'treble',
          keySignature: 'C',
          title: 'Mixed Dialect Versions',
          autoBeam: 'true',
          showMeasureNumbers: 'yes',
          measures: [
            { barline: 'single', notes: [{ keys: ['c/4'], duration: '8' }] },
            { barline: 'double', chords: ['G7'], notes: [{ keys: ['g/4'], duration: 'q' }] },
            { barline: 'final', notes: [{ keys: ['c/5'], duration: 'h' }] },
          ],
        },
      };
      const r = resolveMusicSpec(spec);
      expect(r.type).toBe('music');
      // string boolean -> real boolean (a truthy "false" would wrongly beam)
      expect(r.autoBeam).toBe(true);
      // showMeasureNumbers -> measureNumbers boolean
      expect(r.measureNumbers).toBe(true);
      expect('showMeasureNumbers' in r).toBe(false);
      // per-measure barline -> endBar; alias key consumed
      expect(r.measures.map((m: any) => m.endBar)).toEqual(['single', 'double', 'final']);
      expect(r.measures.every((m: any) => !('barline' in m))).toBe(true);
      // measure-level chords -> per-note chordSymbol; alias key consumed
      expect(r.measures[1].notes[0].chordSymbol).toBe('G7');
      expect('chords' in r.measures[1]).toBe(false);
    });

    it('coerceMusicFlag: affirmative strings true, "false"/junk false', () => {
      expect(coerceMusicFlag('yes')).toBe(true);
      expect(coerceMusicFlag('true')).toBe(true);
      expect(coerceMusicFlag(true)).toBe(true);
      expect(coerceMusicFlag('false')).toBe(false);
      expect(coerceMusicFlag('no')).toBe(false);
      expect(coerceMusicFlag(undefined)).toBe(false);
    });

    it('w4-09: double-nested notes[] lifted to measures (already fixed)', () => {
      const r = resolveMusicSpec({
        type: 'music',
        definition: {
          timeSignature: '4/4', clef: 'treble', title: 'Nesting Off By One',
          notes: [
            [{ keys: ['c/4'], duration: 'q' }, { keys: ['d/4'], duration: 'q' }],
            [{ keys: ['e/4'], duration: 'q' }, { keys: ['f/4'], duration: 'q' }],
          ],
        },
      });
      expect(Array.isArray(r.measures)).toBe(true);
      expect(r.measures).toHaveLength(2);
      expect(r.measures[0].notes).toHaveLength(2);
      expect(r.notes).toBeUndefined();
    });

    it('w4-10: pitch/dur/time/key/staff aliases resolve (already fixed)', () => {
      const r = resolveMusicSpec({
        type: 'music',
        definition: {
          time: '3/4', key: 'Bb', staff: 'treble', title: 'Deprecated Field Names',
          notes: [{ pitch: ['bb/4'], dur: 'q' }, { pitch: ['d/5'], dur: 'q' }],
        },
      });
      expect(r.timeSignature).toBe('3/4');
      expect(r.keySignature).toBe('Bb');
      expect(r.clef).toBe('treble');
      expect(r.notes[0].keys).toEqual(['bb/4']);
      expect(r.notes[0].duration).toBe('q');
      expect('pitch' in r.notes[0]).toBe(false);
    });
  });

  // ---- D-177: scalar keys + long-form duration names (music-w4-12) ----
  it('D-177 w4-12: scalar keys -> array, long durations -> codes', () => {
    const r = resolveMusicSpec({
      type: 'music',
      definition: {
        timeSignature: '4/4', clef: 'treble', keySignature: 'C',
        notes: [
          { keys: 'c/4', duration: 'quarter' },
          { keys: 'e/4', duration: 'quarter' },
          { keys: 'g/4', duration: 'half' },
          { keys: 'c/5', duration: 'whole' },
        ],
      },
    });
    expect(r.notes.map((n: any) => n.keys)).toEqual([['c/4'], ['e/4'], ['g/4'], ['c/5']]);
    expect(r.notes.map((n: any) => n.duration)).toEqual(['q', 'q', 'h', 'w']);
  });

  // ---- D-175: markdown-fenced JSON definition (music-w4-04) ----
  it('D-175 w4-04: ```json fenced definition string is parsed & claimed', () => {
    const fenced = '```json\n' + JSON.stringify({
      timeSignature: '4/4', clef: 'treble', keySignature: 'D', title: 'Fenced',
      notes: [
        { keys: ['d/4'], duration: 'q' }, { keys: ['f#/4'], duration: 'q' },
        { keys: ['a/4'], duration: 'q' }, { keys: ['d/5'], duration: 'q' },
      ],
    }) + '\n```';
    const r = resolveMusicSpec({ type: 'music', definition: fenced });
    expect(r.type).toBe('music');
    expect(r.keySignature).toBe('D');
    expect(r.notes).toHaveLength(4);
  });

  // ---- D-169: empty degenerate body claimed rather than hanging (w3-09) ----
  it('D-169 w3-09: zero-content body is detected as degenerate (claimable)', () => {
    const body = degenerateMusicBody({
      type: 'music',
      definition: {
        clef: 'treble', keySignature: 'C', timeSignature: '4/4', title: 'Empty',
        measures: [], notes: [], slurs: [], ties: [], hairpins: [], staves: [],
      },
    });
    expect(body).not.toBeNull();
    expect(body.title).toBe('Empty');
    // A body WITH content is not degenerate.
    expect(degenerateMusicBody({
      type: 'music', definition: { notes: [{ keys: ['c/4'], duration: 'q' }] },
    })).toBeNull();
  });

  // ---- D-170: score-edge barlines from first/last measure (w1-05) ----
  it('D-170 w1-05: first-measure beginBar & last-measure endBar are the edges', () => {
    const edges = resolveScoreEdgeBarlines(
      undefined, undefined,
      { beginBar: 'repeat-begin' },
      { endBar: 'final' },
    );
    expect(edges).toEqual({ beginBar: 'repeat-begin', endBar: 'final' });
    // A spec-level spelling still wins over the per-measure one.
    expect(resolveScoreEdgeBarlines('single', 'double', { beginBar: 'repeat-begin' }, { endBar: 'final' }))
      .toEqual({ beginBar: 'single', endBar: 'double' });
  });

  // ---- D-168: multi-voice rest displacement pitches (w1-12 / w2-10) ----
  it('D-168: multiVoiceRestPitch raises upper / lowers lower per clef', () => {
    expect(multiVoiceRestPitch('treble', 'upper')).toBe('D5');
    expect(multiVoiceRestPitch('treble', 'lower')).toBe('G4');
    expect(multiVoiceRestPitch('bass', 'upper')).toBe('F3');
    expect(multiVoiceRestPitch('bass', 'lower')).toBe('B2');
    expect(multiVoiceRestPitch('unknown-clef', 'upper')).toBeUndefined();
  });

  // A correctly-authored body is not mutated by the new aliases.
  it('canonical body is returned unchanged by the alias layer', () => {
    const canon = {
      timeSignature: '4/4', clef: 'treble', keySignature: 'C', measureNumbers: true,
      measures: [{ endBar: 'final', notes: [{ keys: ['c/4'], duration: 'q', chordSymbol: 'C' }] }],
    };
    const r = normalizeMusicShape(canon);
    expect(r.measureNumbers).toBe(true);
    expect(r.measures[0].endBar).toBe('final');
    expect(r.measures[0].notes[0].chordSymbol).toBe('C');
  });
});
