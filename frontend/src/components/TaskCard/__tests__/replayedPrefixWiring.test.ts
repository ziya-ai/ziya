/**
 * Wiring for the replayed-prefix exclusions.
 *
 * Static because the defect class is a MISSING GUARD in a counting loop,
 * not wrong logic: every unit test on these aggregates passed while the
 * new ``replayed`` records flowed straight through them.  A behavioural
 * test covers each aggregate in replayedDots.test.tsx; these fail loudly
 * if a future edit drops the guard from one of the four call sites,
 * which is the failure mode that would silently let a resume credit
 * itself with a prior attempt's work.
 */

import * as fs from 'fs';
import * as path from 'path';

const read = (p: string) =>
  fs.readFileSync(path.join(__dirname, p), 'utf8');

const TILE = read('../TaskCardInlineTile.tsx');
const OUTCOME = read('../partialOutcome.ts');
const CLUSTERS = read('../../../utils/iterationClusters.ts');
const MODEL = read('../runMapModel.ts');
const MAP = read('../TaskRunMap.tsx');
const CSS = read('../task-card-inline-tile.css');
const TYPES = read('../../../types/task_run.ts');

describe('replayed is excluded from every progress aggregate', () => {
  it('skips replayed in the tile\'s iteration counter', () => {
    expect(TILE).toMatch(/if \(s\.replayed\) continue;/);
  });

  it('skips replayed in progressCounts', () => {
    expect(OUTCOME).toMatch(/if \(s\.replayed\) continue;/);
  });

  it('skips replayed in failure clustering', () => {
    expect(CLUSTERS).toMatch(/if \(s\.replayed\) continue;/);
  });
});

describe('replayed is surfaced on the dot strip', () => {
  it('is carried into the dot model', () => {
    expect(MODEL).toMatch(/replayed: !!s\.replayed/);
  });

  it('drives a distinct dot class', () => {
    expect(MAP).toMatch(/tc-map__dot--replayed/);
  });

  it('has a style for that class', () => {
    expect(CSS).toMatch(/\.tc-map__dot--replayed \{/);
  });

  it('keeps the pass/fail hue rather than going greyscale', () => {
    // A preserved FAILURE must still read as a failure, so the replayed
    // treatment may adjust weight but must not set a background colour.
    const block = CSS.match(/\.tc-map__dot--replayed \{[\s\S]*?\}/);
    expect(block).toBeTruthy();
    expect(block![0]).not.toMatch(/background/);
  });

  it('explains the provenance in the dot title', () => {
    expect(MAP).toMatch(/replayed from an earlier/);
  });
});

describe('the type admits the field optionally', () => {
  it('marks replayed optional so legacy records still parse', () => {
    expect(TYPES).toMatch(/replayed\?: boolean;/);
  });
});
