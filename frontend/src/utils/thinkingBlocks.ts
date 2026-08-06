/**
 * Ephemeral positional thinking blocks.
 *
 * The executor emits reasoning as a discrete ``thinking`` chunk rather than
 * mixing ``<thinking-data>`` tags into the text stream.  This module holds
 * the frontend half: a pure reducer that folds those chunks into a block
 * list, and a self-describing marker that records WHERE each block occurred
 * relative to tool calls and answer text.
 *
 * Three properties are load-bearing, each fixing a specific defect:
 *
 *  1. The reducer is PURE and the caller owns the array.  Index assignment
 *     therefore happens synchronously at the call site, not inside a React
 *     setState callback.  Computing it inside the callback while appending
 *     the marker outside meant two chunks in one React batch both read the
 *     same array length and produced DUPLICATE indices.
 *
 *  2. The marker carries a ``turnId``, so it resolves unambiguously.  Keyed
 *     by conversation alone, a marker committed to message N's content could
 *     resolve against message N+1's blocks -- showing the wrong reasoning,
 *     which is worse than showing none.
 *
 *  3. Blocks live in session state keyed by ``turnId`` and are NOT cleared
 *     when a stream ends.  They were, which deleted the content while the
 *     marker stayed in the committed message -- reasoning vanished the
 *     instant a response completed.  They are never persisted to a message
 *     record, so a reload clears them: ephemeral by construction, with no
 *     path by which thinking can be resubmitted as context.
 */

export interface ThinkingBlockData {
    content: string;
    /** False while the closing chunk has not arrived yet (mid-stream). */
    complete: boolean;
}

/** A ``thinking`` chunk from the backend. */
export interface ThinkingEvent {
    content?: string;
    done?: boolean;
}

export interface ThinkingReduction {
    blocks: ThinkingBlockData[];
    /**
     * Index of a block newly OPENED by this event, or null.  The caller
     * appends a marker for it, which is what places the block at the
     * position in the answer where the reasoning actually happened.
     */
    openedIndex: number | null;
}

// Mathematical angle brackets (U+27E8/U+27E9) are neither markdown- nor
// HTML-significant, so the marker survives the transform chain between
// insertion and the lexer, and survives lexing as a plain text token.
const THINK_OPEN = '\u27E8';
const THINK_CLOSE = '\u27E9';

let _turnCounter = 0;

/**
 * Mint an id for one streaming turn.  Base36 only -- no hyphens or other
 * markdown-significant characters that a transform could mangle.  The
 * counter guarantees uniqueness even if two turns start in the same
 * millisecond and draw the same random suffix.
 */
export function newThinkingTurnId(): string {
    _turnCounter += 1;
    return Math.random().toString(36).slice(2, 8) + _turnCounter.toString(36);
}

export const thinkingMarker = (turnId: string, index: number): string =>
    `${THINK_OPEN}THINKING:${turnId}:${index}${THINK_CLOSE}`;

export const THINKING_MARKER_RE = new RegExp(
    `${THINK_OPEN}THINKING:([a-z0-9]+):(\\d+)${THINK_CLOSE}`);

/**
 * Fold one ``thinking`` chunk into the block list.  Pure: never mutates
 * ``blocks``, and depends on nothing but its arguments.
 */
export function applyThinkingEvent(
    blocks: ThinkingBlockData[],
    event: ThinkingEvent,
): ThinkingReduction {
    const next = blocks.slice();
    const last = next.length - 1;

    if (event.done) {
        // Closing an already-closed block, or closing with none open, is a
        // no-op rather than an error: the executor closes on transition to
        // text AND at message_stop, so a redundant close is expected.
        if (last >= 0 && !next[last].complete) {
            next[last] = { ...next[last], complete: true };
        }
        return { blocks: next, openedIndex: null };
    }

    if (!event.content) return { blocks: next, openedIndex: null };

    if (last >= 0 && !next[last].complete) {
        next[last] = { ...next[last], content: next[last].content + event.content };
        return { blocks: next, openedIndex: null };
    }

    next.push({ content: event.content, complete: false });
    return { blocks: next, openedIndex: next.length - 1 };
}

/**
 * Cap on retained turns.  Blocks are session-scoped and never persisted, so
 * an unbounded map would grow for the life of the tab.  Evicted markers
 * resolve to nothing and render as nothing, which is the same graceful
 * degradation as after a reload.
 */
export const MAX_RETAINED_THINKING_TURNS = 50;

export function evictOldThinkingTurns<T>(map: Map<string, T>): Map<string, T> {
    if (map.size <= MAX_RETAINED_THINKING_TURNS) return map;
    const next = new Map(map);
    const excess = next.size - MAX_RETAINED_THINKING_TURNS;
    let dropped = 0;
    for (const key of next.keys()) {
        if (dropped++ >= excess) break;
        next.delete(key);
    }
    return next;
}
