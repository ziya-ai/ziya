/**
 * Fidelity tests for the attempt-lineage wiring in TaskCardInlineTile.
 *
 * These read the real component source rather than rendering it.  That
 * is deliberate: the bugs this file exists to catch are all *wiring*
 * bugs invisible to a behavioural test that mocks its dependencies —
 *
 *   - state declared as `attempts` while the JSX reads `lineage`
 *   - `selectAttempt` referenced by a useCallback dep array but never
 *     defined
 *   - `getRunLineage` used without being imported
 *   - the lineage-fetch effect dropped, leaving the rail permanently
 *     empty with no error anywhere
 *
 * Every one of those was present at some point during this change, and
 * every one is a build break or a silently-empty UI rather than a
 * wrong-value assertion — so source assertions are the cheapest honest
 * way to hold them.
 *
 * A fidelity test that passes against unpatched source is worse than no
 * test, so each assertion below names the specific identifier it needs
 * rather than pattern-matching something incidental.
 */

import * as fs from 'fs';
import * as path from 'path';

const TILE = path.join(__dirname, '..', 'TaskCardInlineTile.tsx');
const src = fs.readFileSync(TILE, 'utf8');
// The status palette moved to runStatusVocabulary when the conversation
// sidebar needed it too (the tile now aliases RUN_STATUS_FILL/_FG).  The
// colour assertion below follows the definition rather than being deleted:
// what it guards -- that partial is amber and not mistaken for a success or
// a failure -- is unchanged by where the map lives.
const VOCAB = path.join(__dirname, '..', 'runStatusVocabulary.ts');
const vocabSrc = fs.readFileSync(VOCAB, 'utf8');

describe('lineage state naming is internally consistent', () => {
  // The failure mode: `const [attempts, setAttempts]` alongside
  // `lineage={lineage}` in the JSX.  Both halves look correct in
  // isolation; together they do not compile.
  it('declares the lineage state under the name the JSX reads', () => {
    const declared = /const \[(\w+), set\w+\] = useState<TaskRun\[\]>/.exec(src);
    expect(declared).not.toBeNull();
    const name = declared![1];
    // Whatever it is called, the AttemptRail prop must be fed from it.
    expect(src).toContain(`lineage={${name}}`);
    // And the header ordinal must read the same variable, or the tile
    // reports "attempt 1 of 1" for a three-attempt lineage.
    expect(src).toContain(`${name}.length > 1`);
    expect(src).toContain(`of {${name}.length}`);
  });

  it('has no reference to a stale `attempts` identifier', () => {
    // Guards the half-applied rename: a leftover `attempts.length`
    // would be a build error, and a leftover setter would silently
    // write to state nothing renders.
    expect(src).not.toMatch(/\battempts\.length\b/);
    expect(src).not.toMatch(/\bsetAttempts\b/);
  });
});

describe('selectAttempt exists and is used', () => {
  it('is defined before being named in a dependency array', () => {
    const defIdx = src.indexOf('const selectAttempt = useCallback');
    expect(defIdx).toBeGreaterThan(-1);
    // handleResumeFrom lists it as a dep; JS hoisting does not save a
    // `const`, so definition order genuinely matters here.
    const depIdx = src.indexOf('resumingBlockId, selectAttempt]');
    expect(depIdx).toBeGreaterThan(-1);
    expect(defIdx).toBeLessThan(depIdx);
  });

  it('clears the live buffers when switching attempts', () => {
    // Block ids are SHARED across attempts (a resumed run executes the
    // source run's snapshot tree), so retaining live text/tool buffers
    // would attribute one attempt's output to another — exactly the
    // provenance confusion this feature removes.
    const body = src.slice(
      src.indexOf('const selectAttempt = useCallback'),
      src.indexOf('const selectAttempt = useCallback') + 500,
    );
    expect(body).toContain('clearLive()');
    expect(body).toContain('setFocus(null)');
  });

  it('is wired to the rail and to a successful resume', () => {
    expect(src).toContain('onSelect={selectAttempt}');
    // After launching a new attempt the tile must move to it, or the
    // click looks like it did nothing.
    expect(src).toContain('selectAttempt(res.run.id)');
  });
});

describe('lineage is actually fetched', () => {
  it('imports and calls getRunLineage', () => {
    expect(src).toContain('getRunLineage');
    // Import, not just usage: usage alone is a ReferenceError.
    const importLine = src
      .split('\n')
      .find(l => l.includes("from '../../services/taskRunApi'")
        && l.includes('getRunLineage'));
    expect(importLine).toBeDefined();
  });

  it('refetches when the run reaches a new terminal status', () => {
    // Without run?.status in the deps, a resume launched from this tile
    // adds an attempt the rail never shows until a reload.
    const effect = src.slice(src.indexOf('getRunLineage(projectId'));
    const deps = /\}, \[projectId, shownRunId, ([^\]]*)\]\);/.exec(effect);
    expect(deps).not.toBeNull();
    expect(deps![1]).toContain('run?.status');
  });

  it('degrades to an empty lineage rather than throwing', () => {
    // The rail is additive; a lineage fetch failure must not break a
    // tile whose run is perfectly fine.
    const effect = src.slice(
      src.indexOf('getRunLineage(projectId'),
      src.indexOf('getRunLineage(projectId') + 400,
    );
    expect(effect).toMatch(/\.catch\(/);
  });
});

describe('ResumeMode is imported for the continue path', () => {
  it('imports the type used by handleResumeFrom', () => {
    expect(src).toMatch(/import type \{[^}]*ResumeMode[^}]*\}/);
    expect(src).toContain("mode: ResumeMode = 'retry'");
  });

  it('passes mode through to the API call', () => {
    // A dropped 4th arg silently makes every continue a retry — which
    // would re-run the block the user just fixed by hand.
    expect(src).toContain('resumeRunFromBlock(projectId, run.id, blockId, mode)');
  });

  it('offers continue to the run map only when permitted', () => {
    expect(src).toContain('controls.canContinueFromBlock');
    expect(src).toContain("handleResumeFrom(blockId, 'continue')");
  });
});

describe('partial-outcome helpers are imported where used', () => {
  // Each of these is called in the tile's inline PartialBanner /
  // ProvenanceBlock / AttemptRail components.
  it.each([
    'isPartial', 'progressCounts', 'firstFailedBlock',
    'sideEffectSummary', 'provenance', 'resumeKindLabel', 'attemptSummary',
  ])('imports %s from partialOutcome', (name) => {
    const importBlock = /import \{([\s\S]*?)\} from '\.\/partialOutcome';/
      .exec(src);
    expect(importBlock).not.toBeNull();
    expect(importBlock![1]).toContain(name);
  });
});

describe('the inline components are the ones that render', () => {
  // Two standalone files (PartialBanner.tsx / AttemptRail.tsx) existed
  // as orphans from an earlier draft, duplicating logic the tile
  // defines inline.  Keeping both means a future edit to the wrong copy
  // silently changes nothing on screen.
  it('defines PartialBanner and AttemptRail locally', () => {
    expect(src).toMatch(/const PartialBanner: React\.FC/);
    expect(src).toMatch(/const AttemptRail: React\.FC/);
  });

  it('does not also import them from standalone modules', () => {
    expect(src).not.toContain("from './PartialBanner'");
    expect(src).not.toContain("from './AttemptRail'");
  });

  it('has no orphaned duplicate modules on disk', () => {
    const dir = path.join(__dirname, '..');
    expect(fs.existsSync(path.join(dir, 'PartialBanner.tsx'))).toBe(false);
    expect(fs.existsSync(path.join(dir, 'AttemptRail.tsx'))).toBe(false);
  });
});

describe('partial is a first-class status in the tile', () => {
  it('has a colour and an icon', () => {
    // A missing Record entry is a TS error, but a wrong-looking one is
    // not — so assert the amber and the half-disc specifically.  The
    // colour lives in the shared vocabulary; the ICON is still the tile's
    // own, so the two are read from different files on purpose.
    expect(vocabSrc).toMatch(/partial: '#d29922'/);
    expect(src).toMatch(/partial: <span aria-hidden>◐<\/span>/);
  });

  it('renders the banner only for a partial run', () => {
    expect(src).toContain('{isPartial(run) && (');
  });
});
