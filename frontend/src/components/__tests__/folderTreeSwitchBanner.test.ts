/**
 * Static wiring guard: a long project switch must be visible from EVERY
 * sidebar tab, not only Files.
 *
 * The defect: FolderTree gates all four tabs on ChatContext's
 * `isProjectSwitching`, which is released as soon as the IndexedDB preload
 * commits (fast, and deliberately so).  The slow part of a switch -- the
 * folder scan -- is tracked by FolderContext's `isSwitchingProject`, which
 * only MUIFileExplorer consumed.  From the Chats tab the switch therefore
 * looked finished the instant the conversation list appeared, while the
 * Files tab still said "Switching projects…" for the rest of the scan.
 *
 * Static rather than a render test for the same reason as
 * folderScanWiring.test.ts: the content of the assertion is "this signal is
 * consumed at this call site", and both contexts would need heavy mocking
 * to drive the two flags apart in jsdom.
 */

import * as fs from 'fs';
import * as path from 'path';

const TREE = fs.readFileSync(path.resolve(__dirname, '..', 'FolderTree.tsx'), 'utf8');

/** Source of a tab's `children:` block, located by its unique inner component. */
const tabChildren = (innerComponent: string): string => {
  const inner = TREE.indexOf(`<${innerComponent} />`);
  expect(inner).toBeGreaterThan(-1);
  const start = TREE.lastIndexOf('children: (', inner);
  expect(start).toBeGreaterThan(-1);
  return TREE.slice(start, inner);
};

describe('FolderTree surfaces the folder-tree switch window on non-Files tabs', () => {
  it('reads the tree-level switching flag from FolderContext', () => {
    // The ChatContext flag alone cannot see the folder scan.
    expect(TREE).toMatch(
      /const\s*\{[^}]*isSwitchingProject:\s*isTreeSwitching[^}]*\}\s*=\s*useFolderContext\(\)/);
  });

  it('only shows the banner AFTER the blanking spinner has released', () => {
    // While the ChatContext spinner is up the whole tab is already blank and
    // labelled; the banner covers the gap that follows, not the same window.
    expect(TREE).toMatch(/treeStillSwitching\s*=\s*!isSwitchingProject\s*&&\s*isTreeSwitching/);
  });

  it.each([
    ['Skills', 'ContextsTab'],
    ['Chats', 'MUIChatHistory'],
    ['Backlog', 'BacklogBrowser'],
  ])('renders the banner in the %s tab', (_label, inner) => {
    expect(tabChildren(inner)).toContain('{treeSwitchBanner}');
  });

  it('does NOT render the banner in the Files tab', () => {
    // MUIFileExplorer already renders the full switching view from the same
    // flag; stacking a banner on it would say the same thing twice.
    expect(tabChildren('MUIFileExplorer')).not.toContain('{treeSwitchBanner}');
  });

  it('the banner names the switch and what is still loading', () => {
    expect(TREE).toMatch(/Switching project… loading file tree/);
  });
});
