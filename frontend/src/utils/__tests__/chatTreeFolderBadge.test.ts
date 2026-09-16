/**
 * Folder-row affordances in the conversation tree.
 *
 * Three UX gaps reported together:
 *  1. Clicking a folder moved currentFolderId (the New chat / New folder
 *     target) with no visual feedback.
 *  2. The toolbar "New folder" button created under a collapsed parent
 *     without expanding it, so the child appeared nowhere.
 *  3. A folder whose descendants are all folders rendered no badge at all —
 *     conversation counts roll up, subfolders were never counted, and
 *     "(empty)" is suppressed once there are children.
 *
 * The rules live in chatTreeFolderBadge.ts; the seam tests below read
 * MUIChatHistory.tsx as source to confirm the component consumes them,
 * since rendering that component in jest is impractical.
 */
import * as fs from 'fs';
import * as path from 'path';
import {
  folderBadge,
  countDirectSubfolders,
  isTargetFolderRow,
  withParentExpanded,
} from '../chatTreeFolderBadge';

describe('folderBadge', () => {
  const base = { hasChildren: true, isTaskPlanFolder: false };

  it('shows the rolled-up conversation count alone when there are no subfolders', () => {
    expect(folderBadge({ ...base, conversationCount: 5, subfolderCount: 0 }))
      .toEqual({ kind: 'count', text: '(5)' });
  });

  it('distinguishes a folder that holds only subfolders from an empty one', () => {
    // Previously: conversationCount 0 + hasChildren true → null (no badge).
    expect(folderBadge({ ...base, conversationCount: 0, subfolderCount: 2 }))
      .toEqual({ kind: 'count', text: '(2 folders)' });
    expect(folderBadge({ ...base, conversationCount: 0, subfolderCount: 1 }))
      .toEqual({ kind: 'count', text: '(1 folder)' });
  });

  it('shows both counts when a folder has conversations and subfolders', () => {
    expect(folderBadge({ ...base, conversationCount: 3, subfolderCount: 2 }))
      .toEqual({ kind: 'count', text: '(3 · 2 folders)' });
  });

  it('marks a folder with no children as empty', () => {
    expect(folderBadge({ conversationCount: 0, subfolderCount: 0, hasChildren: false, isTaskPlanFolder: false }))
      .toEqual({ kind: 'empty', text: '(empty)' });
  });

  it('never marks a task-plan folder as empty', () => {
    expect(folderBadge({ conversationCount: 0, subfolderCount: 0, hasChildren: false, isTaskPlanFolder: true }))
      .toBeNull();
  });
});

describe('countDirectSubfolders', () => {
  it('counts only children that are folders', () => {
    expect(countDirectSubfolders([{ folder: {} }, { conversation: {} } as any, { folder: {} }])).toBe(2);
    expect(countDirectSubfolders([{ conversation: {} } as any])).toBe(0);
    expect(countDirectSubfolders(undefined)).toBe(0);
  });
});

describe('isTargetFolderRow', () => {
  it('is true only for the folder row matching currentFolderId', () => {
    expect(isTargetFolderRow(true, 'f1', 'f1')).toBe(true);
    expect(isTargetFolderRow(true, 'f2', 'f1')).toBe(false);
    expect(isTargetFolderRow(true, 'f1', null)).toBe(false);
    expect(isTargetFolderRow(true, 'f1', undefined)).toBe(false);
  });

  it('is never true for a conversation row, even with a colliding id', () => {
    expect(isTargetFolderRow(false, 'f1', 'f1')).toBe(false);
  });
});

describe('withParentExpanded', () => {
  it('adds a collapsed parent', () => {
    expect(withParentExpanded(['a'], 'b')).toEqual(['a', 'b']);
  });
  it('does not duplicate an already-expanded parent', () => {
    expect(withParentExpanded(['a', 'b'], 'b')).toEqual(['a', 'b']);
  });
  it('is a no-op for a root (null) parent and does not alias the input', () => {
    const input = ['a'];
    const out = withParentExpanded(input, null);
    expect(out).toEqual(['a']);
    expect(out).not.toBe(input);
  });
});

describe('MUIChatHistory consumes the folder-row helpers (seam)', () => {
  const src = fs.readFileSync(
    path.resolve(__dirname, '../../components/MUIChatHistory.tsx'),
    'utf8',
  );

  it('imports the helpers', () => {
    expect(src).toMatch(/import \{[^}]*\bfolderBadge\b[^}]*\} from '\.\.\/utils\/chatTreeFolderBadge'/);
    expect(src).toMatch(/import \{[^}]*\bisTargetFolderRow\b[^}]*\} from '\.\.\/utils\/chatTreeFolderBadge'/);
    expect(src).toMatch(/import \{[^}]*\bwithParentExpanded\b[^}]*\} from '\.\.\/utils\/chatTreeFolderBadge'/);
  });

  it('computes and passes isTargetFolder for each row', () => {
    expect(src).toMatch(/const isTargetFolder = isTargetFolderRow\(isFolder, nodeId, currentFolderId\)/);
    expect(src).toMatch(/isTargetFolder=\{isTargetFolder\}/);
    // The row applies it visually, not just accepts the prop.
    expect(src).toMatch(/data-target-folder=\{isTargetFolder/);
  });

  it('passes a subfolder count into the row and renders the badge from folderBadge', () => {
    expect(src).toMatch(/subfolderCount=\{subfolderCount\}/);
    expect(src).toMatch(/folderBadge\(\{\s*conversationCount,\s*subfolderCount/);
    // The old hard-coded branches are gone.
    expect(src).not.toMatch(/\(\{conversationCount\}\)<\/Typography>/);
  });

  it('routes the toolbar "New folder" button through handleCreateSubfolder', () => {
    // The direct createFolder call that skipped parent expansion must not return.
    expect(src).not.toMatch(/const newId = await createFolder\('New Folder', currentFolderId\)/);
    expect(src).toMatch(/onClick=\{\(\) => handleCreateSubfolder\(currentFolderId\)\}/);
    expect(src).toMatch(/handleCreateSubfolder = async \(parentFolderId: string \| null\)/);
    expect(src).toMatch(/setExpandedNodes\(prev => withParentExpanded\(prev\.map\(String\), parentFolderId\)\)/);
  });
});
