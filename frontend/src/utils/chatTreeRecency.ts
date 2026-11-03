/**
 * "Recent only" filter for the conversation sidebar.
 *
 * Runs on the OUTPUT of MUIChatHistory's cached tree memo, never inside it:
 * the memo's structural hash, sort-only fast path and folder-count cache
 * guard all assume the cached tree holds every folder, so pruning there
 * would force a full rebuild every render.  Here the full tree stays cached
 * and only the view is narrowed.  Input nodes are never mutated; a node
 * whose children change is shallow-copied.
 */
import type { ConversationFolder } from './types';

export type RecencyWindow = 'all' | '1d' | '7d';

export const RECENCY_WINDOWS: RecencyWindow[] = ['all', '1d', '7d'];

export const RECENCY_WINDOW_MS: Record<RecencyWindow, number> = {
  all: Infinity,
  '1d': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
};

export const RECENCY_WINDOW_LABELS: Record<RecencyWindow, string> = {
  all: 'Show all',
  '1d': 'Active in last 24 hours',
  '7d': 'Active in last 7 days',
};

// Global preference, like ZIYA_PINNED_FOLDERS; not keyed by project.
export const RECENCY_STORAGE_KEY = 'ZIYA_CHAT_RECENCY_WINDOW';

export function parseRecencyWindow(value: unknown): RecencyWindow {
  return value === '1d' || value === '7d' ? value : 'all';
}

export function loadRecencyWindow(): RecencyWindow {
  try {
    return parseRecencyWindow(localStorage.getItem(RECENCY_STORAGE_KEY));
  } catch {
    return 'all';
  }
}

/**
 * When the conversation was last written to.  lastActiveAt is the server
 * summary field; a locally hydrated record may only carry lastAccessedAt
 * (syncMerge maps one onto the other), so fall back the same way the row's
 * time label does.
 */
export function conversationActivityAt(conv: { lastActiveAt?: number; lastAccessedAt?: number | null } | null | undefined): number {
  return conv?.lastActiveAt || conv?.lastAccessedAt || 0;
}

export interface RecencyFilterOptions {
  window: RecencyWindow;
  now: number;
  /** Conversation ids shown regardless of age: current, streaming, running tasks. */
  exempt: ReadonlySet<string>;
}

export interface RecencyFilterResult {
  nodes: any[];
  /** Conversation rows removed from the view. */
  hiddenConversations: number;
}

function countConversations(nodes: any[], depth = 0): number {
  if (depth > 30) return 0;
  let n = 0;
  for (const node of nodes) {
    if (node.conversation) n++;
    if (node.children?.length) n += countConversations(node.children, depth + 1);
  }
  return n;
}

export function applyRecencyFilter(nodes: any[], opts: RecencyFilterOptions): RecencyFilterResult {
  if (opts.window === 'all') return { nodes, hiddenConversations: 0 };
  const maxAge = RECENCY_WINDOW_MS[opts.window];
  let hidden = 0;

  const walk = (list: any[], depth: number): any[] => {
    if (depth > 30) return list;
    const out: any[] = [];
    for (const node of list) {
      const children: any[] = node.children?.length ? walk(node.children, depth + 1) : [];
      if (node.conversation) {
        const conv = node.conversation;
        const recent = opts.exempt.has(conv.id)
          || opts.now - conversationActivityAt(conv) <= maxAge;
        // A stale conversation with visible descendants (forks, TaskPlan
        // folders) stays as their anchor rather than orphaning them.
        if (!recent && children.length === 0) { hidden++; continue; }
        out.push({ ...node, children });
      } else if (node.folder) {
        const f: ConversationFolder = node.folder;
        // A folder created or edited inside the window is shown even when
        // empty, so "New folder" does not vanish the moment it is made.
        const ownRecent = opts.now - (f.updatedAt || f.createdAt || 0) <= maxAge;
        if (children.length === 0 && !ownRecent) continue;
        out.push({ ...node, children, conversationCount: countConversations(children) });
      } else {
        out.push(node);
      }
    }
    return out;
  };

  return { nodes: walk(nodes, 0), hiddenConversations: hidden };
}
