import { sortComparator } from '../chatTreeSort';

const conv = (id: string, lastAccessedAt = 0, extra: any = {}) => ({
  id: `conv-${id}`,
  conversation: { id, lastAccessedAt, ...extra },
});

const folder = (id: string, lastActivityTime = 0, createdAt = 0, extra: any = {}) => ({
  id,
  folder: { id },
  lastActivityTime,
  createdAt,
  ...extra,
});

const noBoost = new Map<string, number>();
const sortAll = (items: any[], active?: Set<string>) =>
  [...items].sort((a, b) => sortComparator(a, b, noBoost, active)).map(n => n.id);

describe('sortComparator active-processing tier', () => {
  it('sorts an active (streaming) conversation above idle conversations with NEWER timestamps — the screenshot regression', () => {
    const activeOld = conv('active', 1000);
    const idleNew1 = conv('idle1', 5000);
    const idleNew2 = conv('idle2', 4000);
    const order = sortAll([idleNew1, idleNew2, activeOld], new Set(['active']));
    expect(order).toEqual(['conv-active', 'conv-idle1', 'conv-idle2']);
  });

  it('sorts an active conversation above a folder with newer activity', () => {
    const activeOld = conv('active', 1000);
    const busyFolder = folder('f1', 9000);
    const order = sortAll([busyFolder, activeOld], new Set(['active']));
    expect(order).toEqual(['conv-active', 'f1']);
  });

  it('orders two active conversations by activity time, newest first', () => {
    const a = conv('a', 1000);
    const b = conv('b', 2000);
    const order = sortAll([a, b], new Set(['a', 'b']));
    expect(order).toEqual(['conv-b', 'conv-a']);
  });

  it('keeps pinned folders above active conversations', () => {
    const pinned = folder('pf', 10, 10, { isPinned: true });
    const active = conv('active', 9000);
    const order = sortAll([active, pinned], new Set(['active']));
    expect(order).toEqual(['pf', 'conv-active']);
  });

  it('is inert when no active set is supplied (legacy behavior)', () => {
    const a = conv('a', 1000);
    const b = conv('b', 2000);
    expect(sortAll([a, b])).toEqual(['conv-b', 'conv-a']);
    expect(sortAll([b, a])).toEqual(['conv-b', 'conv-a']);
  });

  it('ignores active ids that match folder ids — only conversations qualify', () => {
    const f = folder('shared-id', 1000);
    const c = conv('other', 5000);
    // 'shared-id' in the active set must not promote the folder
    const order = sortAll([f, c], new Set(['shared-id']));
    expect(order).toEqual(['conv-other', 'shared-id']);
  });

  it('a running-task conversation (also in the active set) outranks idle rows', () => {
    const taskConv = conv('task', 100);
    const idle = conv('idle', 99999);
    expect(sortAll([idle, taskConv], new Set(['task']))).toEqual(['conv-task', 'conv-idle']);
  });

  it('a folder with hasActiveDescendant=true floats above idle folders and conversations', () => {
    const busyFolder = folder('busy', 100, 100, { hasActiveDescendant: true });
    const idleFolder = folder('idle', 99999, 99999);
    const idleConv = conv('idle-conv', 99999);
    const order = sortAll([idleFolder, idleConv, busyFolder]);
    expect(order).toEqual(['busy', 'idle', 'conv-idle-conv']);
  });

  it('a folder with an active descendant still ranks below a pinned folder', () => {
    const pinned = folder('pf', 10, 10, { isPinned: true });
    const busyFolder = folder('busy', 9000, 9000, { hasActiveDescendant: true });
    expect(sortAll([busyFolder, pinned])).toEqual(['pf', 'busy']);
  });

  it('a directly-active conversation and a folder with an active descendant are both top-tier, ordered by activity time', () => {
    const activeConv = conv('active', 5000);
    const busyFolder = folder('busy', 9000, 9000, { hasActiveDescendant: true });
    const order = sortAll([activeConv, busyFolder], new Set(['active']));
    expect(order).toEqual(['busy', 'conv-active']);
  });

  it('hasActiveDescendant=false does not promote a folder (baseline no-op)', () => {
    const f = folder('f', 100, 100, { hasActiveDescendant: false });
    const c = conv('c', 5000);
    expect(sortAll([f, c])).toEqual(['conv-c', 'f']);
  });
});

describe('sortComparator baseline ordering (unchanged tiers)', () => {
  it('sorts idle conversations by max(lastAccessedAt, lastActiveAt) descending', () => {
    const a = conv('a', 1000, { lastActiveAt: 8000 });
    const b = conv('b', 5000);
    expect(sortAll([b, a])).toEqual(['conv-a', 'conv-b']);
  });

  it('items with any timestamp sort above items with none', () => {
    const dated = conv('dated', 1);
    const undated = conv('undated', 0);
    expect(sortAll([undated, dated])).toEqual(['conv-dated', 'conv-undated']);
  });

  it('folders sort above conversations when neither has a timestamp', () => {
    const f = folder('f0');
    const c = conv('c0');
    expect(sortAll([c, f])).toEqual(['f0', 'conv-c0']);
  });

  it('applies taskPlanBoost to conversation times', () => {
    const boosted = conv('boosted', 100);
    const plain = conv('plain', 5000);
    const boost = new Map([['boosted', 9000]]);
    const order = [...[plain, boosted]]
      .sort((a, b) => sortComparator(a, b, boost))
      .map(n => n.id);
    expect(order).toEqual(['conv-boosted', 'conv-plain']);
  });
});
