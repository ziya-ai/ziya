/**
 * Recency filter for the conversation sidebar (utils/chatTreeRecency.ts).
 *
 * The filter runs on the cached tree's output.  These tests pin the rules
 * the user chose: lastActiveAt (falling back to lastAccessedAt) decides
 * recency, folders left empty are hidden unless they were themselves
 * created/edited inside the window, exempt ids always show, and a stale
 * conversation that still has visible descendants stays as their anchor.
 */
import {
  applyRecencyFilter, conversationActivityAt, loadRecencyWindow, parseRecencyWindow,
  RECENCY_STORAGE_KEY, RECENCY_WINDOW_MS, RECENCY_WINDOWS, RECENCY_WINDOW_LABELS,
} from '../chatTreeRecency';

const DAY = 24 * 60 * 60 * 1000;
const NOW = 100 * DAY;

const conv = (id: string, ageMs: number, extra: any = {}) => ({
  id: `conv-${id}`,
  conversation: { id, lastActiveAt: NOW - ageMs, lastAccessedAt: NOW - ageMs, ...extra },
  children: [] as any[],
});
const folder = (id: string, children: any[], ageMs = 50 * DAY) => ({
  id,
  folder: { id, name: id, createdAt: NOW - ageMs, updatedAt: NOW - ageMs },
  children,
  conversationCount: children.filter(c => c.conversation).length,
});
const ids = (nodes: any[]) => nodes.map(n => n.id);
const none = new Set<string>();

describe('applyRecencyFilter', () => {
  it("window 'all' returns the very same array and hides nothing", () => {
    const tree = [folder('f', [conv('a', 30 * DAY)])];
    const out = applyRecencyFilter(tree, { window: 'all', now: NOW, exempt: none });
    expect(out.nodes).toBe(tree);
    expect(out.hiddenConversations).toBe(0);
  });

  it('7d keeps conversations active within 7 days and hides older ones, counting them', () => {
    const tree = [conv('fresh', 2 * DAY), conv('edge', 7 * DAY), conv('stale', 8 * DAY), conv('ancient', 60 * DAY)];
    const out = applyRecencyFilter(tree, { window: '7d', now: NOW, exempt: none });
    expect(ids(out.nodes)).toEqual(['conv-fresh', 'conv-edge']);
    expect(out.hiddenConversations).toBe(2);
  });

  it('1d is a tighter window than 7d', () => {
    const tree = [conv('hour', DAY / 24), conv('threeDays', 3 * DAY)];
    expect(ids(applyRecencyFilter(tree, { window: '1d', now: NOW, exempt: none }).nodes)).toEqual(['conv-hour']);
    expect(ids(applyRecencyFilter(tree, { window: '7d', now: NOW, exempt: none }).nodes)).toEqual(['conv-hour', 'conv-threeDays']);
  });

  it('exempt ids (current / streaming / running task) show regardless of age', () => {
    const tree = [conv('current', 90 * DAY), conv('stale', 90 * DAY)];
    const out = applyRecencyFilter(tree, { window: '7d', now: NOW, exempt: new Set(['current']) });
    expect(ids(out.nodes)).toEqual(['conv-current']);
    expect(out.hiddenConversations).toBe(1);
  });

  it('uses lastActiveAt, falling back to lastAccessedAt only when it is absent', () => {
    // Opened yesterday (lastAccessedAt) but last written 30 days ago: hidden.
    const openedNotWritten = { id: 'conv-o', conversation: { id: 'o', lastActiveAt: NOW - 30 * DAY, lastAccessedAt: NOW - DAY }, children: [] };
    // No server stamp at all: lastAccessedAt is the only signal.
    const localOnly = { id: 'conv-l', conversation: { id: 'l', lastAccessedAt: NOW - DAY }, children: [] };
    const out = applyRecencyFilter([openedNotWritten, localOnly], { window: '7d', now: NOW, exempt: none });
    expect(ids(out.nodes)).toEqual(['conv-l']);
    expect(conversationActivityAt({ lastActiveAt: 5, lastAccessedAt: 9 })).toBe(5);
    expect(conversationActivityAt({ lastAccessedAt: 9 })).toBe(9);
    expect(conversationActivityAt(undefined)).toBe(0);
  });

  it('hides a folder whose every conversation is hidden, recursively, and recounts survivors', () => {
    const tree = [
      folder('empty', [conv('old1', 20 * DAY), conv('old2', 20 * DAY)]),
      folder('mixed', [conv('new', DAY), conv('old3', 20 * DAY), folder('sub-empty', [conv('old4', 20 * DAY)])]),
    ];
    const out = applyRecencyFilter(tree, { window: '7d', now: NOW, exempt: none });
    expect(ids(out.nodes)).toEqual(['mixed']);
    expect(ids(out.nodes[0].children)).toEqual(['conv-new']);
    expect(out.nodes[0].conversationCount).toBe(1);
    expect(out.hiddenConversations).toBe(4);
  });

  it('keeps an empty folder that was itself created or edited inside the window (a just-made "New folder")', () => {
    const tree = [folder('brandNew', [], 0), folder('oldEmpty', [], 30 * DAY)];
    const out = applyRecencyFilter(tree, { window: '7d', now: NOW, exempt: none });
    expect(ids(out.nodes)).toEqual(['brandNew']);
  });

  it('keeps a stale conversation that anchors visible descendants (fork children, TaskPlan folders)', () => {
    const parent = conv('parent', 40 * DAY);
    parent.children = [conv('fork', DAY)];
    const out = applyRecencyFilter([parent, conv('loner', 40 * DAY)], { window: '7d', now: NOW, exempt: none });
    expect(ids(out.nodes)).toEqual(['conv-parent']);
    expect(ids(out.nodes[0].children)).toEqual(['conv-fork']);
    // The anchor is shown, so it is not counted as hidden; the loner is.
    expect(out.hiddenConversations).toBe(1);
  });

  it('never mutates the cached input tree', () => {
    const tree = [folder('f', [conv('new', DAY), conv('old', 30 * DAY)])];
    const snapshot = JSON.stringify(tree);
    applyRecencyFilter(tree, { window: '7d', now: NOW, exempt: none });
    expect(JSON.stringify(tree)).toBe(snapshot);
    expect(tree[0].children).toHaveLength(2);
  });
});

describe('window preference', () => {
  it('parses only known windows, defaulting to all', () => {
    expect(parseRecencyWindow('1d')).toBe('1d');
    expect(parseRecencyWindow('7d')).toBe('7d');
    expect(parseRecencyWindow('30d')).toBe('all');
    expect(parseRecencyWindow(null)).toBe('all');
  });

  it('loads from localStorage under the documented key', () => {
    localStorage.setItem(RECENCY_STORAGE_KEY, '1d');
    expect(loadRecencyWindow()).toBe('1d');
    localStorage.removeItem(RECENCY_STORAGE_KEY);
    expect(loadRecencyWindow()).toBe('all');
  });

  it('every window has a label and a span; 7d is seven times 1d', () => {
    for (const w of RECENCY_WINDOWS) {
      expect(RECENCY_WINDOW_LABELS[w]).toBeTruthy();
      expect(RECENCY_WINDOW_MS[w]).toBeGreaterThan(0);
    }
    expect(RECENCY_WINDOW_MS['7d']).toBe(7 * RECENCY_WINDOW_MS['1d']);
  });
});
