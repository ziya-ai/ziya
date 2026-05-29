/**
 * Tests for the pure tree helpers in permissionsTree.ts.
 *
 * Focused on hasDescendantInSets — the predicate used by
 * PermissionsDialog to render directory rows whose subtree contains
 * a configured grant (A1b, "change at lower level" indicator).
 *
 * Direct grants on the directory itself MUST NOT count — those are
 * already visible via the row's own column checkboxes; the indicator
 * exists specifically to surface non-obvious sub-tree state.
 */

import {
  hasDescendantInSets,
  isAncestor,
  type PermissionSets,
} from '../permissionsTree';

function emptySets(): PermissionSets {
  return { scope: new Set(), writable: new Set(), context: new Set() };
}

describe('isAncestor', () => {
  it('strict ancestor returns true', () => {
    expect(isAncestor('a/b', 'a/b/c')).toBe(true);
    expect(isAncestor('a', 'a/b/c/d')).toBe(true);
  });
  it('equal paths return false (strict)', () => {
    expect(isAncestor('a/b', 'a/b')).toBe(false);
  });
  it('sibling-with-shared-prefix is NOT an ancestor', () => {
    // Bug guard: naive startsWith without "/" guard would say yes here.
    expect(isAncestor('a/b', 'a/bc')).toBe(false);
    expect(isAncestor('a/b', 'a/bc/d')).toBe(false);
  });
  it('reverse direction returns false', () => {
    expect(isAncestor('a/b/c', 'a/b')).toBe(false);
  });
  it('trailing-slash on either side is normalised', () => {
    expect(isAncestor('a/b/', 'a/b/c')).toBe(true);
    expect(isAncestor('a/b', 'a/b/c/')).toBe(true);
  });
});

describe('hasDescendantInSets', () => {
  it('returns false for empty sets', () => {
    expect(hasDescendantInSets('any/path', emptySets())).toBe(false);
  });

  it('returns true when a descendant is in scope', () => {
    const sets = emptySets();
    sets.scope.add('proj/src/deep/file.ts');
    expect(hasDescendantInSets('proj', sets)).toBe(true);
    expect(hasDescendantInSets('proj/src', sets)).toBe(true);
    expect(hasDescendantInSets('proj/src/deep', sets)).toBe(true);
  });

  it('returns true when a descendant is in writable', () => {
    const sets = emptySets();
    sets.writable.add('proj/src/file.ts');
    expect(hasDescendantInSets('proj', sets)).toBe(true);
  });

  it('returns true when a descendant is in context', () => {
    const sets = emptySets();
    sets.context.add('proj/docs/note.md');
    expect(hasDescendantInSets('proj', sets)).toBe(true);
  });

  it('returns true if any of the three sets has a descendant', () => {
    const sets = emptySets();
    sets.context.add('proj/a/b');
    sets.writable.add('other/c'); // unrelated, must not affect proj
    expect(hasDescendantInSets('proj', sets)).toBe(true);
    expect(hasDescendantInSets('other', sets)).toBe(true);
    expect(hasDescendantInSets('unrelated', sets)).toBe(false);
  });

  it('does NOT count a direct grant on the path itself', () => {
    // Direct grants are visible in the row's own checkboxes; the
    // descendant indicator must not double-count them.
    const sets = emptySets();
    sets.writable.add('proj/src');
    expect(hasDescendantInSets('proj/src', sets)).toBe(false);
    // But an ancestor of that direct grant SHOULD light up.
    expect(hasDescendantInSets('proj', sets)).toBe(true);
  });

  it('rejects sibling-with-shared-prefix (regression)', () => {
    const sets = emptySets();
    sets.scope.add('proj/src-extra/file.ts');
    // 'proj/src' must NOT see 'proj/src-extra/...' as a descendant.
    expect(hasDescendantInSets('proj/src', sets)).toBe(false);
    // Common parent still does.
    expect(hasDescendantInSets('proj', sets)).toBe(true);
  });

  it('trailing-slash variants on the query are normalised', () => {
    const sets = emptySets();
    sets.scope.add('proj/a/b');
    expect(hasDescendantInSets('proj/', sets)).toBe(true);
    expect(hasDescendantInSets('proj', sets)).toBe(true);
  });

  it('multiple grants under same parent still trigger', () => {
    const sets = emptySets();
    sets.scope.add('proj/a/x');
    sets.scope.add('proj/a/y');
    sets.writable.add('proj/a/z');
    expect(hasDescendantInSets('proj/a', sets)).toBe(true);
    expect(hasDescendantInSets('proj', sets)).toBe(true);
  });

  it('absolute root "/" matches any non-root descendant', () => {
    const sets = emptySets();
    sets.scope.add('/proj/a');
    expect(hasDescendantInSets('/', sets)).toBe(true);
  });
});
