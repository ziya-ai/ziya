/**
 * Per-row resume inside a parallel.
 *
 * The server never resumes AT a parallel branch: its single skip flag would
 * replay the branches ordered before the target and re-run those after it,
 * by body order (audit S7: "Retry Lane D" would have discarded Lanes
 * A-C).  It resumes the outermost enclosing parallel and replays the
 * branches that finished.  So the map must (a) disable per-row controls on
 * branch rows and (b) offer a FRESH re-run on the parallel row for the
 * case where finished branches should run again.
 *
 * Logic is tested directly; the wiring is checked against source text, in
 * the style of holdSurfacingWiring.test.ts, because the defects here are
 * of the defined-but-never-consumed kind that mounting would not catch.
 */

import * as fs from 'fs';
import * as path from 'path';
import { idsInsideParallel } from '../runMapModel';
import type { Block } from '../../../types/task_card';

const DIR = path.join(__dirname, '..');
const read = (f: string) => fs.readFileSync(path.join(DIR, f), 'utf8');

const n = (id: string, block_type: string, body: Block[] = []): Block =>
  ({ id, block_type, body } as unknown as Block);

// Shape of the audit S7 card.
const S7 = n('root', 'group', [
  n('givens', 'task'),
  n('foundation', 'parallel', [
    n('f0', 'task'),
    n('telemetry', 'parallel', [
      n('lane_a', 'task'), n('lane_b', 'task'),
    ]),
    n('lane_e', 'task'),
  ]),
  n('verify', 'task'),
]);

describe('idsInsideParallel', () => {
  it('marks every block strictly inside a parallel, at any depth', () => {
    const ids = idsInsideParallel(S7);
    expect([...ids].sort()).toEqual(
      ['f0', 'lane_a', 'lane_b', 'lane_e', 'telemetry'].sort());
  });

  it('leaves the outermost parallel and serial blocks resumable', () => {
    const ids = idsInsideParallel(S7);
    for (const id of ['root', 'givens', 'foundation', 'verify']) {
      expect(ids.has(id)).toBe(false);
    }
  });

  it('tolerates a missing root', () => {
    expect(idsInsideParallel(null).size).toBe(0);
  });
});

describe('run map wiring', () => {
  const map = read('TaskRunMap.tsx');
  const tile = read('TaskCardInlineTile.tsx');

  it('the map consults idsInsideParallel for the card root', () => {
    expect(map).toMatch(/idsInsideParallel\(card\.root\)/);
  });

  it('both per-row controls are disabled on branch rows', () => {
    const disabled = map.match(/disabled=\{!!resumingBlockId \|\| inParallel\}/g) ?? [];
    expect(disabled.length).toBe(2);
  });

  it('a parallel row offers a fresh re-run, wired to mode rerun', () => {
    expect(map).toMatch(/onRerunFrom && !viaCall && isParallel/);
    expect(tile).toMatch(/onRerunFrom=\{[\s\S]*?handleResumeFrom\(blockId, 'rerun'\)/);
  });

  it('the API accepts the rerun mode', () => {
    const api = fs.readFileSync(
      path.join(DIR, '..', '..', 'services', 'taskRunApi.ts'), 'utf8');
    expect(api).toMatch(/export type ResumeMode = 'retry' \| 'continue' \| 'rerun'/);
  });
});
