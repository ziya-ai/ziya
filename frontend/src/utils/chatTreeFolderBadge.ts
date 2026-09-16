/**
 * Pure helpers for folder rows in the conversation tree (MUIChatHistory).
 *
 * Kept out of the component so the rules are unit-testable:
 *  - what a folder row's trailing badge says,
 *  - which folder row is the current "target" (where New chat / New folder
 *    will land) and should be outlined,
 *  - which parent must be expanded so a newly created child is visible.
 */

export interface FolderBadgeInput {
  /** Conversations in this folder AND all descendants (already rolled up). */
  conversationCount: number;
  /** Direct child folders. */
  subfolderCount: number;
  /** Node has any children (folders or conversations). */
  hasChildren: boolean;
  isTaskPlanFolder: boolean;
}

export type FolderBadge =
  | { kind: 'count'; text: string }
  | { kind: 'empty'; text: string }
  | null;

/**
 * Trailing caption for a folder row.
 *
 * A folder whose only descendants are folders used to render nothing: its
 * rolled-up conversation count was 0 (so no "(N)") and it had children (so
 * no "(empty)").  Subfolders are now counted so such a folder reads
 * "(2 folders)" rather than looking identical to one with nothing in it.
 */
export function folderBadge(input: FolderBadgeInput): FolderBadge {
  const parts: string[] = [];
  if (input.conversationCount > 0) parts.push(String(input.conversationCount));
  if (input.subfolderCount > 0) {
    parts.push(`${input.subfolderCount} folder${input.subfolderCount === 1 ? '' : 's'}`);
  }
  if (parts.length > 0) return { kind: 'count', text: `(${parts.join(' · ')})` };
  if (!input.isTaskPlanFolder && !input.hasChildren) return { kind: 'empty', text: '(empty)' };
  return null;
}

/** Direct child folders of a tree node. */
export function countDirectSubfolders(children: ReadonlyArray<{ folder?: unknown }> | undefined): number {
  if (!children) return 0;
  let n = 0;
  for (const c of children) if (c.folder) n++;
  return n;
}

/**
 * Whether a row is the current target folder — the folder that New chat /
 * New folder will create into.  Clicking a folder moves this pointer
 * without changing the open conversation, so it needs its own visual,
 * distinct from the active-conversation highlight.
 */
export function isTargetFolderRow(
  isFolder: boolean,
  nodeId: string,
  currentFolderId: string | null | undefined,
): boolean {
  return isFolder && !!currentFolderId && nodeId === currentFolderId;
}

/**
 * Expanded-node list after creating a child under ``parentId``.  A collapsed
 * parent would otherwise swallow the new child with no visible change.
 * ``null`` parent (root) needs nothing.
 */
export function withParentExpanded(expanded: ReadonlyArray<string>, parentId: string | null): string[] {
  if (!parentId) return [...expanded];
  if (expanded.includes(parentId)) return [...expanded];
  return [...expanded, parentId];
}
