/**
 * @jest-environment jsdom
 *
 * Custom range for the sidebar recency filter (utils/chatTreeRecency.ts).
 * (jsdom: the storage tests use sessionStorage and localStorage.)
 *
 * The presets test one thing, "last write within N".  A custom range adds
 * two choices: WHICH time is tested (last activity, first activity, or the
 * span between them) and WHAT it is tested against (the last N units, or
 * two calendar dates).  These tests pin: how a range resolves to bounds,
 * which conversations each basis shows, that a custom range overrides the
 * preset and an unusable one does not, the exempt / anchor / folder rules
 * under a range, the caption text, and that the value lives in
 * sessionStorage only.
 *
 * Dates are built with the local-time Date constructor, so the suite does
 * not depend on the machine's timezone.
 */
import {
  applyRecencyFilter, conversationInRange, conversationStartedAt, customRecencyLabel,
  loadCustomRecency, parseCustomRecency, resolveCustomRange, saveCustomRecency,
  DEFAULT_CUSTOM_RECENCY, RECENCY_BASES, RECENCY_BASIS_LABELS, RECENCY_CUSTOM_STORAGE_KEY,
  RECENCY_STORAGE_KEY, RECENCY_UNITS,
} from '../chatTreeRecency';
import type { CustomRecency, RecencyBasis, RecencyUnit } from '../chatTreeRecency';

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
const at = (y: number, m: number, d: number, h = 12) => new Date(y, m - 1, d, h).getTime();
const NOW = at(2026, 11, 11);

const rel = (amount: number, unit: RecencyUnit, basis: RecencyBasis = 'last'): CustomRecency =>
  ({ basis, mode: 'relative', amount, unit, from: '', to: '' });
const abs = (from: string, to: string, basis: RecencyBasis = 'last'): CustomRecency =>
  ({ basis, mode: 'absolute', amount: 3, unit: 'days', from, to });

const conv = (id: string, createdAt: number, lastActiveAt: number) => ({
  id: `conv-${id}`,
  conversation: { id, createdAt, lastActiveAt, lastAccessedAt: lastActiveAt },
  children: [] as any[],
});
const folder = (id: string, children: any[], touchedAt: number) => ({
  id,
  folder: { id, name: id, createdAt: touchedAt, updatedAt: touchedAt },
  children,
  conversationCount: children.filter(c => c.conversation).length,
});
const ids = (nodes: any[]) => nodes.map(n => n.id);
const none = new Set<string>();

// Five conversations placed around the range 3 Nov – 9 Nov.
const tree = () => [
  conv('spanning', at(2026, 10, 20), at(2026, 11, 10)),   // starts before, ends after
  conv('startedIn', at(2026, 11, 5), at(2026, 11, 10)),   // starts inside, ends after
  conv('endedIn', at(2026, 10, 1), at(2026, 11, 4)),      // starts before, ends inside
  conv('before', at(2026, 9, 1), at(2026, 10, 2)),        // wholly before
  conv('after', at(2026, 11, 10), at(2026, 11, 11)),      // wholly after
];
const WEEK = (basis: RecencyBasis) => abs('2026-11-03', '2026-11-09', basis);

describe('resolveCustomRange', () => {
  it('relative: starts N units back and has no upper bound', () => {
    expect(resolveCustomRange(rel(3, 'days'), NOW)).toEqual({ start: NOW - 3 * DAY, end: Infinity });
    expect(resolveCustomRange(rel(2, 'weeks'), NOW)).toEqual({ start: NOW - 14 * DAY, end: Infinity });
    expect(resolveCustomRange(rel(6, 'hours'), NOW)).toEqual({ start: NOW - 6 * HOUR, end: Infinity });
  });

  it('months are calendar months, clamped to the last day of a shorter month', () => {
    expect(resolveCustomRange(rel(1, 'months'), NOW)!.start).toBe(at(2026, 10, 11));
    // 31 March minus one month is 28 February, not 3 March.
    expect(resolveCustomRange(rel(1, 'months'), at(2026, 3, 31))!.start).toBe(at(2026, 2, 28));
  });

  it('absolute: local midnight of From through the last millisecond of To', () => {
    const r = resolveCustomRange(abs('2026-11-03', '2026-11-09'), NOW)!;
    expect(r.start).toBe(new Date(2026, 10, 3).getTime());
    expect(r.end).toBe(new Date(2026, 10, 10).getTime() - 1);
  });

  it('absolute with an empty To is open-ended ("since")', () => {
    expect(resolveCustomRange(abs('2026-11-03', ''), NOW))
      .toEqual({ start: new Date(2026, 10, 3).getTime(), end: Infinity });
  });

  it('a single day is a valid range', () => {
    const r = resolveCustomRange(abs('2026-11-03', '2026-11-03'), NOW)!;
    expect(r.end - r.start).toBeGreaterThan(22 * HOUR);
  });

  it('returns null for anything it cannot resolve', () => {
    expect(resolveCustomRange(rel(0, 'days'), NOW)).toBeNull();
    expect(resolveCustomRange(rel(-2, 'days'), NOW)).toBeNull();
    expect(resolveCustomRange(rel(1.5, 'days'), NOW)).toBeNull();
    expect(resolveCustomRange(rel(NaN, 'days'), NOW)).toBeNull();
    expect(resolveCustomRange(abs('', '2026-11-09'), NOW)).toBeNull();
    expect(resolveCustomRange(abs('2026-11-09', '2026-11-03'), NOW)).toBeNull();
    expect(resolveCustomRange(abs('2026-02-31', ''), NOW)).toBeNull();
    expect(resolveCustomRange(abs('03/11/2026', ''), NOW)).toBeNull();
  });
});

describe('conversationStartedAt', () => {
  it('prefers createdAt', () => {
    expect(conversationStartedAt({ createdAt: 5, lastActiveAt: 9, messages: [{ _timestamp: 7 }] })).toBe(5);
  });

  it('falls back to the first loaded message, but not on a shell', () => {
    expect(conversationStartedAt({ lastActiveAt: 9, messages: [{ _timestamp: 7 }, { _timestamp: 8 }] })).toBe(7);
    // A shell holds only recent messages, so its first one is not the start.
    expect(conversationStartedAt({ lastActiveAt: 9, _isShell: true, messages: [{ _timestamp: 7 }] })).toBe(9);
  });

  it('falls back to the activity time when nothing else is known', () => {
    expect(conversationStartedAt({ lastActiveAt: 9 })).toBe(9);
    expect(conversationStartedAt({ lastAccessedAt: 4 })).toBe(4);
    expect(conversationStartedAt(undefined)).toBe(0);
  });
});

describe('conversationInRange', () => {
  const r = resolveCustomRange(WEEK('last'), NOW)!;
  const c = (id: string) => tree().find(n => n.id === `conv-${id}`)!.conversation;

  it('last: the last write is inside the range', () => {
    expect(conversationInRange(c('endedIn'), 'last', r)).toBe(true);
    expect(conversationInRange(c('spanning'), 'last', r)).toBe(false);
    expect(conversationInRange(c('startedIn'), 'last', r)).toBe(false);
  });

  it('first: the start is inside the range', () => {
    expect(conversationInRange(c('startedIn'), 'first', r)).toBe(true);
    expect(conversationInRange(c('spanning'), 'first', r)).toBe(false);
    expect(conversationInRange(c('endedIn'), 'first', r)).toBe(false);
  });

  it('during: the start-to-last-write span overlaps the range', () => {
    expect(conversationInRange(c('spanning'), 'during', r)).toBe(true);
    expect(conversationInRange(c('startedIn'), 'during', r)).toBe(true);
    expect(conversationInRange(c('endedIn'), 'during', r)).toBe(true);
    expect(conversationInRange(c('before'), 'during', r)).toBe(false);
    expect(conversationInRange(c('after'), 'during', r)).toBe(false);
  });

  it('the bounds are inclusive', () => {
    expect(conversationInRange({ lastActiveAt: r.start }, 'last', r)).toBe(true);
    expect(conversationInRange({ lastActiveAt: r.end }, 'last', r)).toBe(true);
    expect(conversationInRange({ lastActiveAt: r.end + 1 }, 'last', r)).toBe(false);
    expect(conversationInRange({ lastActiveAt: r.start - 1 }, 'last', r)).toBe(false);
  });
});

describe('applyRecencyFilter with a custom range', () => {
  const run = (custom: CustomRecency | null, window: 'all' | '1d' | '7d' = 'all', exempt = none) =>
    applyRecencyFilter(tree(), { window, custom, now: NOW, exempt });

  it('each basis shows a different set for the same dates, and counts the rest as hidden', () => {
    const last = run(WEEK('last'));
    expect(ids(last.nodes)).toEqual(['conv-endedIn']);
    expect(last.hiddenConversations).toBe(4);

    const first = run(WEEK('first'));
    expect(ids(first.nodes)).toEqual(['conv-startedIn']);
    expect(first.hiddenConversations).toBe(4);

    const during = run(WEEK('during'));
    expect(ids(during.nodes)).toEqual(['conv-spanning', 'conv-startedIn', 'conv-endedIn']);
    expect(during.hiddenConversations).toBe(2);
  });

  it('overrides the preset window while set', () => {
    // endedIn was last written 7 days ago: outside 1d, inside the custom week.
    expect(ids(run(WEEK('last'), '1d').nodes)).toEqual(['conv-endedIn']);
    // Positive control: without the custom range the preset decides.
    const preset = ids(run(null, '1d').nodes);
    expect(preset).toContain('conv-after');
    expect(preset).not.toContain('conv-endedIn');
  });

  it('an unusable custom range falls back to the preset instead of hiding everything', () => {
    expect(ids(run(rel(0, 'days'), '7d').nodes)).toEqual(ids(run(null, '7d').nodes));
    const input = tree();
    const out = applyRecencyFilter(input, { window: 'all', custom: rel(0, 'days'), now: NOW, exempt: none });
    expect(out.nodes).toBe(input);
  });

  it('"last 7 days" as a custom range shows what the 7d preset shows', () => {
    expect(ids(run(rel(7, 'days')).nodes)).toEqual(ids(run(null, '7d').nodes));
    expect(run(rel(7, 'days')).nodes.length).toBeGreaterThan(0);
  });

  it('no custom range and window all returns the same array', () => {
    const input = tree();
    expect(applyRecencyFilter(input, { window: 'all', custom: null, now: NOW, exempt: none }).nodes).toBe(input);
    expect(applyRecencyFilter(input, { window: 'all', now: NOW, exempt: none }).nodes).toBe(input);
  });

  it('exempt ids show even when outside a bounded range', () => {
    const out = run(WEEK('last'), 'all', new Set(['before']));
    expect(ids(out.nodes)).toEqual(['conv-endedIn', 'conv-before']);
    expect(out.hiddenConversations).toBe(3);
  });

  it('keeps a conversation outside the range that anchors a visible descendant', () => {
    const parent = conv('parent', at(2026, 8, 1), at(2026, 8, 2));
    parent.children = [conv('fork', at(2026, 11, 4), at(2026, 11, 5))];
    const out = applyRecencyFilter([parent, conv('loner', at(2026, 8, 1), at(2026, 8, 2))],
      { window: 'all', custom: WEEK('last'), now: NOW, exempt: none });
    expect(ids(out.nodes)).toEqual(['conv-parent']);
    expect(ids(out.nodes[0].children)).toEqual(['conv-fork']);
    expect(out.hiddenConversations).toBe(1);
  });

  it('folders: hidden when emptied, recounted, and an empty one shows if touched since the range began', () => {
    const input = [
      folder('emptied', [conv('old', at(2026, 9, 1), at(2026, 9, 2))], at(2026, 9, 1)),
      folder('mixed', [conv('in', at(2026, 10, 1), at(2026, 11, 4)), conv('out', at(2026, 9, 1), at(2026, 9, 2))], at(2026, 9, 1)),
      // Made after the range ended: still shown, so "New folder" never vanishes.
      folder('justMade', [], NOW),
      folder('oldEmpty', [], at(2026, 9, 1)),
    ];
    const out = applyRecencyFilter(input, { window: 'all', custom: WEEK('last'), now: NOW, exempt: none });
    expect(ids(out.nodes)).toEqual(['mixed', 'justMade']);
    expect(ids(out.nodes[0].children)).toEqual(['conv-in']);
    expect(out.nodes[0].conversationCount).toBe(1);
    expect(out.hiddenConversations).toBe(2);
  });

  it('never mutates the input tree', () => {
    const input = [folder('f', tree(), at(2026, 9, 1))];
    const snapshot = JSON.stringify(input);
    applyRecencyFilter(input, { window: 'all', custom: WEEK('during'), now: NOW, exempt: none });
    expect(JSON.stringify(input)).toBe(snapshot);
  });
});

describe('customRecencyLabel', () => {
  const label = (c: CustomRecency) => customRecencyLabel(c, NOW, 'en-US');

  it('relative ranges name the basis and the span', () => {
    expect(label(rel(3, 'days'))).toBe('Active in last 3 days');
    expect(label(rel(3, 'weeks', 'during'))).toBe('Open in last 3 weeks');
    expect(label(rel(1, 'weeks', 'first'))).toBe('Started in last 1 week');
  });

  it('date ranges name the basis and both days', () => {
    expect(label(WEEK('last'))).toBe('Last active Nov 3 – Nov 9');
    expect(label(WEEK('during'))).toBe('Open during Nov 3 – Nov 9');
    expect(label(WEEK('first'))).toBe('Started Nov 3 – Nov 9');
  });

  it('an open-ended range reads as "since"', () => {
    expect(label(abs('2026-11-03', ''))).toBe('Active since Nov 3');
    expect(label(abs('2026-11-03', '', 'first'))).toBe('Started since Nov 3');
  });

  it('adds the year only to a day outside the current year', () => {
    expect(label(abs('2025-12-30', '2026-01-02'))).toBe('Last active Dec 30, 2025 – Jan 2');
  });

  it('every basis has a selector label', () => {
    for (const b of RECENCY_BASES) expect(RECENCY_BASIS_LABELS[b]).toBeTruthy();
    expect(RECENCY_UNITS).toEqual(['hours', 'days', 'weeks', 'months']);
  });
});

describe('custom range storage', () => {
  beforeEach(() => { sessionStorage.clear(); localStorage.clear(); });

  it('round-trips through sessionStorage and never touches localStorage', () => {
    saveCustomRecency(WEEK('during'));
    expect(loadCustomRecency()).toEqual(WEEK('during'));
    expect(sessionStorage.getItem(RECENCY_CUSTOM_STORAGE_KEY)).toBeTruthy();
    expect(localStorage.getItem(RECENCY_CUSTOM_STORAGE_KEY)).toBeNull();
    expect(localStorage.getItem(RECENCY_STORAGE_KEY)).toBeNull();
  });

  it('saving null removes the stored value', () => {
    saveCustomRecency(rel(3, 'days'));
    saveCustomRecency(null);
    expect(sessionStorage.getItem(RECENCY_CUSTOM_STORAGE_KEY)).toBeNull();
    expect(loadCustomRecency()).toBeNull();
  });

  it('rejects a stored value it cannot use', () => {
    sessionStorage.setItem(RECENCY_CUSTOM_STORAGE_KEY, '{not json');
    expect(loadCustomRecency()).toBeNull();
    expect(parseCustomRecency({ ...rel(3, 'days'), basis: 'middle' })).toBeNull();
    expect(parseCustomRecency({ ...rel(3, 'days'), unit: 'years' })).toBeNull();
    expect(parseCustomRecency(rel(0, 'days'))).toBeNull();
    expect(parseCustomRecency('7d')).toBeNull();
    // Positive control.
    expect(parseCustomRecency(rel(3, 'days'))).toEqual(rel(3, 'days'));
  });

  it('the default draft is itself a usable range', () => {
    expect(resolveCustomRange(DEFAULT_CUSTOM_RECENCY, NOW)).not.toBeNull();
  });
});
