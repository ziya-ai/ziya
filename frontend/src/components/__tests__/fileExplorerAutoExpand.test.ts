/**
 * MUIFileExplorer auto-expand and the file-row ✓.
 *
 * Observed 2026-10-03: unchecking and rechecking Docs/ArchitectureOverview.md
 * expanded every folder under app/ holding a checked file.  The effect that
 * expands ancestors of selected keys re-ran over the WHOLE selection on any
 * change.  It must expand only for keys newly added, and not at all for a
 * click in the tree (the clicked node is already visible; a folder click
 * adds its whole subtree, which would open every subfolder).
 *
 * Also: the row showed ✓ for any accurate entry, including a 0 it then
 * ignored in favour of the estimate.
 *
 * Source-level: the logic is inline in a very large component.
 */
import * as fs from 'fs';
import * as path from 'path';

const SRC = fs.readFileSync(path.join(__dirname, '..', 'MUIFileExplorer.tsx'), 'utf8');

const effect = (() => {
  const start = SRC.indexOf('Compute expanded keys based on selected items');
  expect(start).toBeGreaterThan(0);
  return SRC.slice(start, SRC.indexOf('}, [treeData, checkedKeys]);', start));
})();

describe('auto-expand on selection change', () => {
  it('expands ancestors of newly added keys only', () => {
    expect(effect).toMatch(/prevCheckedSetRef\.current/);
    expect(effect).toMatch(/findAncestorsOfSelected\(treeData, added\)/);
    expect(effect).not.toMatch(/findAncestorsOfSelected\(treeData, checkedSet\)/);
  });

  it('skips expansion for a click in the tree', () => {
    expect(effect).toMatch(/suppressAutoExpandUntilRef\.current/);
    const click = SRC.slice(SRC.indexOf('const handleCheckboxClick = '));
    expect(click.slice(0, 600)).toMatch(/suppressAutoExpandUntilRef\.current = Date\.now\(\) \+/);
  });
});

describe('file row ✓', () => {
  it('only marks a positive accurate count', () => {
    expect(SRC).toMatch(/accurateData && accurateData\.count > 0 \? '✓' : ''/);
    expect(SRC).not.toMatch(/\$\{accurateData \? '✓' : ''\}/);
  });
});
