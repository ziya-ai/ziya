/**
 * Lane folding (design/capabilities-hub.md, slice 6a).  Two rules:
 *
 *   * A BUILT-IN skill sitting at its default -- no placement at any layer --
 *     is ALWAYS folded into one "N built-in skills" row.  The shipped catalog
 *     is dozens of cards nobody arranged; it clutters every lane regardless of
 *     screen size.  Pin one anywhere and it becomes a card: that is a decision.
 *   * Everything else (MCP servers, project- and user-installed skills) folds
 *     to SPACE: as many cards as fit the lane's height, least deliberate last.
 *
 * Pure, so order and cut are asserted without a DOM.
 */
import type { BenchItemView } from '../../hooks/useBench';

export function isBuiltinDefault(item: BenchItemView): boolean {
    return item.kind === 'skill' && item.provenance === 'builtin' && item.origin === 'default';
}

/** Lower is shown first.  Ties keep the server's (stable) order. */
export function deliberateness(item: BenchItemView): number {
    if (!item.atLens.ghost) return 0;                           // pinned at this lens
    if (item.provenance === 'project' || item.provenance === 'custom') return 1;
    if (item.kind === 'mcp') return 2;                          // real per-turn cost
    return 3;                                                   // at its default
}

export function orderForLane(cards: BenchItemView[]): BenchItemView[] {
    return cards
        .map((c, i) => ({ c, i, d: deliberateness(c), t: c.kind === 'mcp' ? -c.tokens : 0 }))
        .sort((a, b) => a.d - b.d || a.t - b.t || a.i - b.i)
        .map(x => x.c);
}

export interface FoldedLane {
    shown: BenchItemView[];
    spaceFolded: BenchItemView[];     // did not fit
    builtinFolded: BenchItemView[];   // always folded
}

/**
 * ``capacity`` is cards that fit; null = unmeasured (space-fold nothing).
 * ``expanded`` shows everything (the lane scrolls).  The built-in row takes a
 * slot when present; so does the "N more" row.  Never space-folds exactly one.
 */
export function foldLane(cards: BenchItemView[], capacity: number | null, expanded = false): FoldedLane {
    if (expanded) return { shown: orderForLane(cards), spaceFolded: [], builtinFolded: [] };
    const builtin = cards.filter(isBuiltinDefault);
    const rest = orderForLane(cards.filter(c => !isBuiltinDefault(c)));
    const slots = capacity === null ? Infinity : Math.max(0, capacity - (builtin.length ? 1 : 0));
    if (rest.length <= slots) return { shown: rest, spaceFolded: [], builtinFolded: builtin };
    const cut = Math.max(0, Math.min(slots - 1, rest.length - 2));
    return { shown: rest.slice(0, cut), spaceFolded: rest.slice(cut), builtinFolded: builtin };
}

export const sumTokens = (items: BenchItemView[]): number => items.reduce((a, i) => a + i.tokens, 0);

export function foldSummary(folded: BenchItemView[]): string {
    const names = folded.slice(0, 3).map(i => i.name).join(', ');
    const more = folded.length > 3 ? ', …' : '';
    const tokens = sumTokens(folded);
    const cost = tokens >= 1000 ? ` · ${(tokens / 1000).toFixed(1)}K` : tokens > 0 ? ` · ${tokens}t` : '';
    return `${names}${more}${cost}`;
}
