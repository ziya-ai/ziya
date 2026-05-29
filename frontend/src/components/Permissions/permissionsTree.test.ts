import { describe, it, expect } from '@jest/globals';
import {
  isAncestor,
  findAncestorInSet,
  cellState,
  toggleSet,
  applyClick,
} from './permissionsTree';

const universe = [
  { path: 'app', is_dir: true },
  { path: 'app/foo.py', is_dir: false },
  { path: 'app/sub', is_dir: true },
  { path: 'app/sub/bar.py', is_dir: false },
  { path: 'out', is_dir: true },
  { path: 'out/log.txt', is_dir: false },
  { path: 'README.md', is_dir: false },
];

describe('isAncestor', () => {
  it('handles the simple case', () => {
    expect(isAncestor('app', 'app/foo.py')).toBe(true);
    expect(isAncestor('app', 'app/sub/bar.py')).toBe(true);
    expect(isAncestor('app', 'app')).toBe(false);
    expect(isAncestor('app', 'apple/foo.py')).toBe(false);
  });
  it('treats empty/root as ancestor of everything', () => {
    expect(isAncestor('', 'anything')).toBe(true);
    expect(isAncestor('/', 'anything')).toBe(true);
    expect(isAncestor('', '')).toBe(false);
  });
  it('regression — prefix without separator must NOT match', () => {
    expect(isAncestor('out', 'output/log.txt')).toBe(false);
  });
});

describe('findAncestorInSet', () => {
  it('finds the closest ancestor', () => {
    const set = new Set(['app', 'app/sub']);
    expect(findAncestorInSet('app/sub/bar.py', set)).toBe('app/sub');
    expect(findAncestorInSet('app/foo.py', set)).toBe('app');
    expect(findAncestorInSet('out/log.txt', set)).toBe(null);
  });
});

describe('cellState', () => {
  it('returns direct when path is in the set', () => {
    const set = new Set(['app']);
    expect(cellState('app', true, 'scope', set, universe).kind).toBe('direct');
  });
  it('returns inherited when an ancestor is in the set', () => {
    const set = new Set(['app']);
    const r = cellState('app/foo.py', false, 'scope', set, universe);
    expect(r.kind).toBe('inherited');
    expect(r.kind === 'inherited' && r.from).toBe('app');
  });
  it('returns indeterminate when some descendants are in the set', () => {
    const set = new Set(['app/foo.py']);
    expect(cellState('app', true, 'scope', set, universe).kind).toBe('indeterminate');
  });
  it('returns empty when nothing is set', () => {
    expect(cellState('app', true, 'scope', new Set(), universe).kind).toBe('empty');
  });
  it('directory context cascades like other columns (no longer "na")', () => {
    // Pre-Slice-1a: context on a directory was meaningless (preload is
    // file-only) and returned ``na``.  Now context-on-dir is a cascade
    // grant: the executor expands it to descendant files at run time.
    // Empty set → empty cell.
    expect(cellState('app', true, 'context', new Set(), universe).kind).toBe('empty');
    // Set on dir → direct.
    expect(cellState('app', true, 'context', new Set(['app']), universe).kind).toBe('direct');
    // Some descendant in set → indeterminate.
    expect(cellState('app', true, 'context', new Set(['app/foo.py']), universe).kind).toBe('indeterminate');
  });
  it('skips descendant walk when universe is null', () => {
    const set = new Set(['app/foo.py']);
    // Without universe, indeterminate isn't computed — stays empty.
    expect(cellState('app', true, 'scope', set, null).kind).toBe('empty');
  });
});

describe('toggleSet', () => {
  it('removes when direct', () => {
    const set = new Set(['app']);
    const r = toggleSet('app', { kind: 'direct' }, set);
    expect(r.has('app')).toBe(false);
  });
  it('adds when empty', () => {
    const r = toggleSet('app', { kind: 'empty' }, new Set());
    expect(r.has('app')).toBe(true);
  });
  it('collapses descendants on add', () => {
    const set = new Set(['app/foo.py', 'app/sub/bar.py']);
    const r = toggleSet('app', { kind: 'indeterminate' }, set);
    expect(r.has('app')).toBe(true);
    expect(r.has('app/foo.py')).toBe(false);
    expect(r.has('app/sub/bar.py')).toBe(false);
  });
  it('preserves descendants on remove (no auto-add)', () => {
    // When you uncheck a parent, descendants that were never
    // explicitly checked stay gone (they were inherited).
    const set = new Set(['app']);
    const r = toggleSet('app', { kind: 'direct' }, set);
    expect(r.has('app')).toBe(false);
    expect(r.has('app/foo.py')).toBe(false);
  });
  it('is a no-op on inherited (no carve-outs)', () => {
    const set = new Set(['app']);
    const r = toggleSet(
      'app/foo.py',
      { kind: 'inherited', from: 'app' },
      set,
    );
    expect(Array.from(r).sort()).toEqual(Array.from(set).sort());
  });
  it('is a no-op on na', () => {
    const set = new Set(['app']);
    const r = toggleSet('app', { kind: 'na' }, set);
    expect(Array.from(r).sort()).toEqual(Array.from(set).sort());
  });
});

describe('applyClick — invariants', () => {
  it('checking W on a path also adds it to scope', () => {
    const r = applyClick(
      'out',
      'writable',
      true,
      { scope: new Set(), writable: new Set(), context: new Set() },
      universe,
    );
    expect(r.writable.has('out')).toBe(true);
    expect(r.scope.has('out')).toBe(true);
  });

  it('checking Ctx on a file also adds it to scope', () => {
    const r = applyClick(
      'README.md',
      'context',
      false,
      { scope: new Set(), writable: new Set(), context: new Set() },
      universe,
    );
    expect(r.context.has('README.md')).toBe(true);
    expect(r.scope.has('README.md')).toBe(true);
  });

  it('unchecking scope removes W and Ctx from path and descendants', () => {
    const r = applyClick(
      'out',
      'scope',
      true,
      {
        scope: new Set(['out']),
        writable: new Set(['out']),
        context: new Set(['out/log.txt']),
      },
      universe,
    );
    expect(r.scope.has('out')).toBe(false);
    expect(r.writable.has('out')).toBe(false);
    expect(r.context.has('out/log.txt')).toBe(false);
  });

  it('unchecking W alone does not affect scope', () => {
    const r = applyClick(
      'out',
      'writable',
      true,
      {
        scope: new Set(['out']),
        writable: new Set(['out']),
        context: new Set(),
      },
      universe,
    );
    expect(r.scope.has('out')).toBe(true);
    expect(r.writable.has('out')).toBe(false);
  });

  it('checking W on a child while parent is W is a no-op (inherited)', () => {
    const sets = {
      scope: new Set(['app']),
      writable: new Set(['app']),
      context: new Set<string>(),
    };
    const r = applyClick('app/foo.py', 'writable', false, sets, universe);
    // parent's W still there; no extra direct entry for the child
    expect(r.writable.has('app')).toBe(true);
    expect(r.writable.has('app/foo.py')).toBe(false);
  });

  it('cascade — adding parent W collapses descendant W entries', () => {
    const sets = {
      scope: new Set(['app/foo.py', 'app/sub/bar.py']),
      writable: new Set(['app/foo.py', 'app/sub/bar.py']),
      context: new Set<string>(),
    };
    const r = applyClick('app', 'writable', true, sets, universe);
    expect(r.writable.has('app')).toBe(true);
    expect(r.writable.has('app/foo.py')).toBe(false);
    expect(r.writable.has('app/sub/bar.py')).toBe(false);
  });
});

describe('cellState — directory rollup with mixed children', () => {
  it('full subtree set → indeterminate (set covers descendants but not the dir itself)', () => {
    const set = new Set(['app/foo.py', 'app/sub/bar.py']);
    expect(cellState('app', true, 'scope', set, universe).kind).toBe('indeterminate');
  });
  it('all-descendants approach: dir itself in set → direct, not indeterminate', () => {
    const set = new Set(['app']);
    expect(cellState('app', true, 'scope', set, universe).kind).toBe('direct');
  });
});
