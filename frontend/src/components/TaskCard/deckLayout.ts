/**
 * deckLayout — pane state for the Task Cards dialog.
 *
 * The dialog has three panes: the card list, the card's definition, and
 * the selected run.  Each collapses to a labelled strip on its own, so
 * the definition or the run can take the whole width.  This module holds
 * the state, its persistence and the divider's snap rule; it is pure so
 * the rules can be tested without mounting the dialog.
 */

/** Which of the definition / run panes are open. */
export type DeckPaneView = 'definition' | 'split' | 'run';

export interface DeckLayout {
  view: DeckPaneView;
  listCollapsed: boolean;
  /** Run pane width in layout px while the view is 'split'. */
  runWidth: number;
}

export const DECK_LAYOUT_KEY = 'ZIYA_TASK_DECK_LAYOUT';

export const RUN_DEFAULT_WIDTH = 460;
/** Narrowest either pane is drawn at while split. */
export const RUN_MIN_WIDTH = 300;
export const DEF_MIN_WIDTH = 340;
/** Dragging a pane narrower than this collapses it on release. */
export const SNAP_WIDTH = 150;

export const DEFAULT_DECK_LAYOUT: DeckLayout = {
  view: 'split', listCollapsed: false, runWidth: RUN_DEFAULT_WIDTH,
};

const VIEWS: ReadonlyArray<DeckPaneView> = ['definition', 'split', 'run'];

/** Parse a stored layout; anything malformed falls back field by field. */
export function parseDeckLayout(raw: string | null | undefined): DeckLayout {
  if (!raw) return { ...DEFAULT_DECK_LAYOUT };
  try {
    const p = JSON.parse(raw) as Partial<DeckLayout> | null;
    if (!p || typeof p !== 'object') return { ...DEFAULT_DECK_LAYOUT };
    return {
      view: VIEWS.includes(p.view as DeckPaneView)
        ? (p.view as DeckPaneView) : DEFAULT_DECK_LAYOUT.view,
      listCollapsed: p.listCollapsed === true,
      runWidth: typeof p.runWidth === 'number' && Number.isFinite(p.runWidth)
        && p.runWidth > 0
        ? p.runWidth : DEFAULT_DECK_LAYOUT.runWidth,
    };
  } catch {
    return { ...DEFAULT_DECK_LAYOUT };
  }
}

export function loadDeckLayout(
  storage: Pick<Storage, 'getItem'> | null | undefined =
    typeof localStorage === 'undefined' ? null : localStorage,
): DeckLayout {
  try {
    return parseDeckLayout(storage?.getItem(DECK_LAYOUT_KEY));
  } catch {
    return { ...DEFAULT_DECK_LAYOUT };
  }
}

export function saveDeckLayout(
  layout: DeckLayout,
  storage: Pick<Storage, 'setItem'> | null | undefined =
    typeof localStorage === 'undefined' ? null : localStorage,
): void {
  try {
    storage?.setItem(DECK_LAYOUT_KEY, JSON.stringify(layout));
  } catch {
    // Private mode / quota: the layout simply is not remembered.
  }
}

/**
 * The view actually drawn.  A card with no runs has no run pane, so the
 * stored preference is ignored (not overwritten) and the definition gets
 * the full width.
 */
export function effectivePaneView(
  view: DeckPaneView, hasRuns: boolean,
): DeckPaneView {
  return hasRuns ? view : 'definition';
}

/**
 * The view to switch to when a card is opened.  A card whose run is held
 * or live opens with the run visible; nothing else changes the view, and
 * the run pane is never closed automatically.
 */
export function viewOnCardOpen(
  view: DeckPaneView, runWantsEyes: boolean,
): DeckPaneView {
  return view === 'definition' && runWantsEyes ? 'split' : view;
}

/**
 * Run pane width while split, given the width shared by both panes.
 * Both floors are honoured when there is room; when there is not, the
 * space is halved so neither pane is squeezed to nothing.
 */
export function clampRunWidth(width: number, available: number): number {
  if (available < RUN_MIN_WIDTH + DEF_MIN_WIDTH) {
    return Math.max(0, Math.round(available / 2));
  }
  return Math.max(RUN_MIN_WIDTH, Math.min(available - DEF_MIN_WIDTH, width));
}

/**
 * Where a divider drag ends.  Dragged most of the way to an edge, the
 * pane on that side collapses and the remembered split width is kept for
 * when it reopens; otherwise the width is clamped to the floors.
 */
export function settleDivider(
  rawRunWidth: number, available: number, previousRunWidth: number,
): Pick<DeckLayout, 'view' | 'runWidth'> {
  if (rawRunWidth < SNAP_WIDTH) {
    return { view: 'definition', runWidth: previousRunWidth };
  }
  if (available - rawRunWidth < SNAP_WIDTH) {
    return { view: 'run', runWidth: previousRunWidth };
  }
  return { view: 'split', runWidth: clampRunWidth(rawRunWidth, available) };
}
