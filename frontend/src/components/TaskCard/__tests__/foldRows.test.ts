/**
 * foldRows — the outline's fold model over flattenBlocks output.
 *
 * Pure-function tests.  The fold is positional (a descendant is any
 * following row with a greater depth), so the cases that matter are the
 * ones where position and tree structure could disagree: invisible
 * groups, siblings after a collapsed container, and stale ids.
 */

import type { Block } from '../../../types/task_card';
import {
  flattenBlocks, foldRows, firstSelectableRow, STATUS_GLYPHS,
} from '../runMapModel';

const task = (id: string, name = id): Block =>
  ({ block_type: 'task', id, name, instructions: '', body: [] });
const container = (id: string, body: Block[], type: Block['block_type'] = 'repeat'): Block =>
  ({ block_type: type, id, name: id, body } as Block);

// root(group, invisible)
//   state
//   repeat
//     run
//     summarize
//   parallel
//     lint
//     typecheck
//   judge
const root: Block = container('root', [
  { block_type: 'state', id: 'state', name: 'state', body: [] } as Block,
  container('repeat', [task('run'), task('summarize')]),
  container('parallel', [task('lint'), task('typecheck')], 'parallel'),
  task('judge'),
], 'group');

const ids = (rows: { block: Block }[]) => rows.map(r => r.block.id);

describe('foldRows', () => {
  const flat = flattenBlocks(root);

  it('is the identity when nothing is collapsed', () => {
    const folded = foldRows(flat, new Set());
    expect(ids(folded)).toEqual(ids(flat));
    expect(folded.every(r => !r.collapsed && r.hiddenCount === 0)).toBe(true);
  });

  it('marks containers, and only containers, as having children', () => {
    const byId = Object.fromEntries(foldRows(flat, new Set()).map(r => [r.block.id, r]));
    expect(byId.repeat.hasChildren).toBe(true);
    expect(byId.parallel.hasChildren).toBe(true);
    expect(byId.run.hasChildren).toBe(false);
    expect(byId.judge.hasChildren).toBe(false);
    expect(byId.state.hasChildren).toBe(false);
  });

  it('hides a collapsed container\'s descendants but keeps its later siblings', () => {
    const folded = foldRows(flat, new Set(['repeat']));
    expect(ids(folded)).toEqual(['state', 'repeat', 'parallel', 'lint', 'typecheck', 'judge']);
    const repeat = folded.find(r => r.block.id === 'repeat')!;
    expect(repeat.collapsed).toBe(true);
    expect(repeat.hiddenCount).toBe(2);
  });

  it('folds independently per container', () => {
    const folded = foldRows(flat, new Set(['repeat', 'parallel']));
    expect(ids(folded)).toEqual(['state', 'repeat', 'parallel', 'judge']);
  });

  it('treats a collapsed id on a leaf, or a stale id, as a no-op', () => {
    // A leaf cannot fold; a deleted block's id lingering in the set must
    // not throw or hide anything.
    const folded = foldRows(flat, new Set(['judge', 'no-such-block']));
    expect(ids(folded)).toEqual(ids(flat));
    expect(folded.find(r => r.block.id === 'judge')!.collapsed).toBe(false);
  });

  it('counts nested descendants transitively', () => {
    const deep: Block = container('root', [
      container('outer', [container('inner', [task('a'), task('b')]), task('c')]),
    ], 'group');
    const folded = foldRows(flattenBlocks(deep), new Set(['outer']));
    expect(ids(folded)).toEqual(['outer']);
    expect(folded[0].hiddenCount).toBe(4);
  });

  it('does not leak rows across a group boundary', () => {
    // A group inside a container is invisible: its children sit at the
    // container's child depth, so they fold WITH the container — and
    // the sibling after the container does not.
    const withGroup: Block = container('root', [
      container('loop', [container('g', [task('x'), task('y')], 'group')]),
      task('after'),
    ], 'group');
    const folded = foldRows(flattenBlocks(withGroup), new Set(['loop']));
    expect(ids(folded)).toEqual(['loop', 'after']);
    expect(folded[0].hiddenCount).toBe(2);
  });
});

describe('firstSelectableRow', () => {
  it('skips blocks whose id is empty', () => {
    // A freshly created card's inner block can have id '' until saved;
    // selecting it would alias the root group (also id '').
    const rows = flattenBlocks(container('', [task(''), task('real')], 'group'));
    expect(firstSelectableRow(rows)?.block.id).toBe('real');
  });

  it('returns null when nothing is selectable', () => {
    expect(firstSelectableRow([])).toBeNull();
    expect(firstSelectableRow(flattenBlocks(container('', [task('')], 'group')))).toBeNull();
  });
});

describe('STATUS_GLYPHS is the single glyph table', () => {
  it('covers every block status the map distinguishes, including held', () => {
    for (const s of ['queued', 'running', 'done', 'failed', 'cancelled', 'skipped', 'held']) {
      expect(STATUS_GLYPHS[s]).toBeTruthy();
    }
    // held must not collapse into queued's glyph
    expect(STATUS_GLYPHS.held).not.toBe(STATUS_GLYPHS.queued);
  });
});
