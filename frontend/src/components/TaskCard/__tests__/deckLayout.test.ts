/**
 * deckLayout — pane state of the Task Cards dialog.
 *
 * The rules worth pinning are the ones a user would feel if they broke:
 * a stored layout that cannot strand the dialog, a card without runs
 * never showing an empty run pane, the run pane never closing by itself,
 * and a divider that collapses a pane only when dragged to an edge.
 */

import {
  DECK_LAYOUT_KEY, DEFAULT_DECK_LAYOUT, DEF_MIN_WIDTH, RUN_MIN_WIDTH,
  SNAP_WIDTH, clampRunWidth, effectivePaneView, loadDeckLayout,
  parseDeckLayout, saveDeckLayout, settleDivider, viewOnCardOpen,
} from '../deckLayout';

describe('stored layout', () => {
  it('defaults to split with the list open', () => {
    expect(parseDeckLayout(null)).toEqual(DEFAULT_DECK_LAYOUT);
    expect(DEFAULT_DECK_LAYOUT.view).toBe('split');
    expect(DEFAULT_DECK_LAYOUT.listCollapsed).toBe(false);
  });

  it('survives malformed JSON and wrong shapes', () => {
    expect(parseDeckLayout('{not json')).toEqual(DEFAULT_DECK_LAYOUT);
    expect(parseDeckLayout('null')).toEqual(DEFAULT_DECK_LAYOUT);
    expect(parseDeckLayout('"split"')).toEqual(DEFAULT_DECK_LAYOUT);
  });

  it('falls back field by field, keeping what is valid', () => {
    const got = parseDeckLayout(JSON.stringify({
      view: 'sideways', listCollapsed: true, runWidth: -3,
    }));
    expect(got).toEqual({
      view: 'split', listCollapsed: true,
      runWidth: DEFAULT_DECK_LAYOUT.runWidth,
    });
  });

  it('round-trips through storage under its own key', () => {
    const store: Record<string, string> = {};
    const storage = {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => { store[k] = v; },
    };
    const layout = { view: 'run' as const, listCollapsed: true, runWidth: 512 };
    saveDeckLayout(layout, storage);
    expect(Object.keys(store)).toEqual([DECK_LAYOUT_KEY]);
    expect(loadDeckLayout(storage)).toEqual(layout);
  });

  it('a throwing storage degrades to the default', () => {
    const storage = { getItem: () => { throw new Error('denied'); } };
    expect(loadDeckLayout(storage)).toEqual(DEFAULT_DECK_LAYOUT);
  });
});

describe('which panes are drawn', () => {
  it('a card with no runs has no run pane, whatever was stored', () => {
    expect(effectivePaneView('run', false)).toBe('definition');
    expect(effectivePaneView('split', false)).toBe('definition');
  });

  it('a card with runs gets the stored view', () => {
    expect(effectivePaneView('run', true)).toBe('run');
    expect(effectivePaneView('definition', true)).toBe('definition');
  });

  it('opening a card whose run wants eyes reveals the run', () => {
    expect(viewOnCardOpen('definition', true)).toBe('split');
  });

  it('never closes or rearranges a view the run is already visible in', () => {
    expect(viewOnCardOpen('run', true)).toBe('run');
    expect(viewOnCardOpen('split', true)).toBe('split');
    expect(viewOnCardOpen('run', false)).toBe('run');
  });

  it('leaves the definition-only view alone for a quiet card', () => {
    expect(viewOnCardOpen('definition', false)).toBe('definition');
  });
});

describe('divider', () => {
  const AVAIL = 1000;

  it('clamps to both floors when there is room', () => {
    expect(clampRunWidth(10, AVAIL)).toBe(RUN_MIN_WIDTH);
    expect(clampRunWidth(5000, AVAIL)).toBe(AVAIL - DEF_MIN_WIDTH);
    expect(clampRunWidth(500, AVAIL)).toBe(500);
  });

  it('halves the space when both floors cannot fit', () => {
    expect(clampRunWidth(500, 400)).toBe(200);
  });

  it('collapses the run when dragged to the right edge', () => {
    expect(settleDivider(SNAP_WIDTH - 1, AVAIL, 460))
      .toEqual({ view: 'definition', runWidth: 460 });
  });

  it('collapses the definition when dragged to the left edge', () => {
    expect(settleDivider(AVAIL - SNAP_WIDTH + 1, AVAIL, 460))
      .toEqual({ view: 'run', runWidth: 460 });
  });

  it('keeps the remembered width across a collapse, for reopening', () => {
    expect(settleDivider(0, AVAIL, 612).runWidth).toBe(612);
  });

  it('stays split, clamped, anywhere in between', () => {
    expect(settleDivider(200, AVAIL, 460))
      .toEqual({ view: 'split', runWidth: RUN_MIN_WIDTH });
    expect(settleDivider(520, AVAIL, 460))
      .toEqual({ view: 'split', runWidth: 520 });
  });
});
