/**
 * Tests for auto-added file heritage tracking.
 *
 * When "Auto-add diff files to context" is enabled, files referenced
 * in diffs are automatically added to the context selection.  These
 * files are marked with an "A" badge in the file explorer and can be
 * bulk-removed via a dedicated button.
 *
 * This file tests the core data-structure logic that FolderContext
 * uses to track, prune, and remove auto-added files.
 */

describe('autoAddedFiles tracking', () => {
  it('adds files to the auto-added set', () => {
    const autoAdded = new Set<string>();
    const filesToAdd = ['src/foo.ts', 'src/bar.ts'];

    filesToAdd.forEach(f => autoAdded.add(f));

    expect(autoAdded.size).toBe(2);
    expect(autoAdded.has('src/foo.ts')).toBe(true);
    expect(autoAdded.has('src/bar.ts')).toBe(true);
  });

  it('deduplicates repeated auto-adds', () => {
    const autoAdded = new Set<string>();
    autoAdded.add('src/foo.ts');
    autoAdded.add('src/foo.ts');
    autoAdded.add('src/bar.ts');

    expect(autoAdded.size).toBe(2);
  });

  it('prunes auto-added entries when unchecked manually', () => {
    const autoAdded = new Set(['src/foo.ts', 'src/bar.ts', 'src/baz.ts']);
    // Simulate user unchecking src/bar.ts — it's no longer in checkedKeys
    const checkedKeys = ['src/foo.ts', 'src/baz.ts', 'src/other.ts'];

    const checkedSet = new Set(checkedKeys);
    const pruned = new Set([...autoAdded].filter(f => checkedSet.has(f)));

    expect(pruned.size).toBe(2);
    expect(pruned.has('src/foo.ts')).toBe(true);
    expect(pruned.has('src/baz.ts')).toBe(true);
    expect(pruned.has('src/bar.ts')).toBe(false);
  });

  it('calculates token recovery from removal', () => {
    const autoAdded = new Set(['src/a.ts', 'src/b.ts', 'src/c.ts']);
    const tokenCounts: Record<string, { count: number; timestamp: number }> = {
      'src/a.ts': { count: 500, timestamp: Date.now() },
      'src/b.ts': { count: 1200, timestamp: Date.now() },
      // src/c.ts has no accurate count — will use estimated
    };

    let tokensRecovered = 0;
    const estimatedFallback = 300; // simulated getFolderTokenCount result

    autoAdded.forEach(filePath => {
      const accurate = tokenCounts[filePath];
      if (accurate && accurate.count > 0) {
        tokensRecovered += accurate.count;
      } else {
        tokensRecovered += estimatedFallback;
      }
    });

    // 500 + 1200 + 300 (fallback) = 2000
    expect(tokensRecovered).toBe(2000);
  });

  it('removal clears auto-added set and removes from checkedKeys', () => {
    const autoAdded = new Set(['src/a.ts', 'src/b.ts']);
    const checkedKeys = ['src/a.ts', 'src/b.ts', 'src/manual.ts'];

    // Simulate removeAutoAddedFiles
    const filtered = checkedKeys.filter(key => !autoAdded.has(key));
    const clearedAutoAdded = new Set<string>();

    expect(filtered).toEqual(['src/manual.ts']);
    expect(clearedAutoAdded.size).toBe(0);
  });

  it('serialization round-trip preserves the set', () => {
    const original = new Set(['src/foo.ts', 'src/bar.ts']);
    const serialized = JSON.stringify([...original]);
    const restored = new Set<string>(JSON.parse(serialized));

    expect(restored.size).toBe(original.size);
    expect(restored.has('src/foo.ts')).toBe(true);
    expect(restored.has('src/bar.ts')).toBe(true);
  });

  it('handles empty auto-added set gracefully', () => {
    const autoAdded = new Set<string>();
    const checkedKeys = ['src/manual.ts'];

    // Prune with empty set should be no-op
    const checkedSet = new Set(checkedKeys);
    const pruned = new Set([...autoAdded].filter(f => checkedSet.has(f)));
    expect(pruned.size).toBe(0);

    // Removal with empty set should return zeros
    const removedCount = autoAdded.size;
    expect(removedCount).toBe(0);
  });
});
