/**
 * Folder badge bubbling (design/visual-refresh.md, stage 2 follow-up).
 *
 * A COLLAPSED folder used to show only its count: a conversation inside it
 * that finished while the user was away, is streaming, or has a task run in
 * flight was invisible until the folder was opened.  rollUpFolderStatus
 * aggregates the descendants' status so the folder row can carry the same
 * trailing-slot vocabulary the chat rows use.
 *
 * Rules pinned here:
 *  - unread / processing / run-status counts sum over every descendant
 *    conversation, through nested folders and conversation-anchored children
 *    (delegates, forks);
 *  - the open conversation never counts as unread (it is being read);
 *  - a folder with nothing to report yields null, so the row renders no
 *    empty cluster;
 *  - the component shows the rollup only on collapsed folders (an expanded
 *    folder shows it on the children; repeating it would double-count).
 *
 * The seam tests read MUIChatHistory.tsx as source, as chatTreeFolderBadge
 * .test.ts does, because rendering that component in jest is impractical.
 */
import * as fs from 'fs';
import * as path from 'path';
import { rollUpFolderStatus, FolderStatusContext } from '../chatTreeFolderBadge';

const conv = (id: string, extra: Record<string, unknown> = {}) => ({
  id: `conv-${id}`, conversation: { id, ...extra }, children: undefined as any,
});
const folder = (id: string, children: any[]) => ({ id, folder: { id }, children });

const ctx = (over: Partial<FolderStatusContext> = {}): FolderStatusContext => ({
  currentConversationId: null,
  streamingIds: new Set<string>(),
  runStatusCounts: {},
  ...over,
});

describe('rollUpFolderStatus', () => {
  it('returns null when no descendant has anything to report', () => {
    const f = folder('f', [conv('a'), folder('g', [conv('b')])]);
    expect(rollUpFolderStatus(f, ctx())).toBeNull();
  });

  it('counts unread descendants, excluding the open conversation', () => {
    const f = folder('f', [
      conv('a', { hasUnreadResponse: true }),
      conv('b', { hasUnreadResponse: true }),
      conv('c'),
    ]);
    expect(rollUpFolderStatus(f, ctx())!.unread).toBe(2);
    expect(rollUpFolderStatus(f, ctx({ currentConversationId: 'a' }))!.unread).toBe(1);
  });

  it('counts streaming descendants from the streaming set', () => {
    const f = folder('f', [conv('a'), conv('b')]);
    const r = rollUpFolderStatus(f, ctx({ streamingIds: new Set(['b']) }));
    expect(r).toEqual({ unread: 0, processing: 1, counts: {} });
  });

  it('sums run-status counts per status across descendants', () => {
    const f = folder('f', [conv('a'), conv('b'), conv('c')]);
    const r = rollUpFolderStatus(f, ctx({
      runStatusCounts: {
        a: { running: 1, done: 2 },
        b: { done: 1, failed: 1 },
        c: { awaiting_input: 1 },
        zzz: { running: 9 },   // not a descendant: must not leak in
      },
    }));
    expect(r!.counts).toEqual({ running: 1, done: 3, failed: 1, awaiting_input: 1 });
  });

  it('walks nested folders and conversation-anchored children', () => {
    const fork = conv('fork', { hasUnreadResponse: true });
    const parent = { ...conv('p'), children: [fork] };
    const f = folder('top', [folder('mid', [folder('deep', [conv('x', { hasUnreadResponse: true })])]), parent]);
    const r = rollUpFolderStatus(f, ctx({ runStatusCounts: { x: { held: 1 } } }));
    expect(r).toEqual({ unread: 2, processing: 0, counts: { held: 1 } });
  });

  it('ignores zero counts and does not mutate the index it reads', () => {
    const index = { a: { done: 0, running: 1 } };
    const frozen = JSON.stringify(index);
    const r = rollUpFolderStatus(folder('f', [conv('a')]), ctx({ runStatusCounts: index }));
    expect(r!.counts).toEqual({ running: 1 });
    expect(JSON.stringify(index)).toBe(frozen);
  });

  it('terminates on a cyclic tree', () => {
    const a: any = folder('a', []);
    const b: any = folder('b', [a]);
    a.children = [b];
    expect(() => rollUpFolderStatus(a, ctx())).not.toThrow();
  });
});

describe('MUIChatHistory consumes the rollup (seam)', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '..', '..', 'components', 'MUIChatHistory.tsx'), 'utf8');

  it('imports rollUpFolderStatus from the badge helpers', () => {
    expect(src).toMatch(/import \{[^}]*rollUpFolderStatus[^}]*\} from '\.\.\/utils\/chatTreeFolderBadge'/);
  });

  it('computes the rollup only for collapsed, non-task-plan folders and passes it to the row', () => {
    const compute = src.match(/const folderRollup = ([^;]*);/);
    expect(compute).not.toBeNull();
    expect(compute![1]).toMatch(/isFolder/);
    expect(compute![1]).toMatch(/!flat\.isExpanded/);
    expect(compute![1]).toMatch(/!isTaskPlanFolder/);
    expect(compute![1]).toMatch(/rollUpFolderStatus\(/);
    expect(src).toMatch(/folderRollup=\{folderRollup\}/);
  });

  it('feeds the rollup the same inputs the chat rows use', () => {
    const compute = src.match(/const folderRollup = ([^;]*);/)![1];
    expect(compute).toMatch(/currentConversationId/);
    expect(compute).toMatch(/streamingIds:\s*streamingConversations/);
    expect(compute).toMatch(/runStatusCounts:\s*runStatusIndex\.conversations/);
  });

  it('renders the rollup gated on !isExpanded and reuses RunStatusGears for the counts', () => {
    const render = src.match(/\{isFolder && !isExpanded && folderRollup && \(([\s\S]*?)\n\s*\)\}/);
    expect(render).not.toBeNull();
    expect(render![1]).toMatch(/data-testid="folder-rollup"/);
    expect(render![1]).toMatch(/<RunStatusGears counts=\{folderRollup\.counts\}/);
    expect(render![1]).toMatch(/folderRollup\.unread/);
    expect(render![1]).toMatch(/folderRollup\.processing/);
  });
});
