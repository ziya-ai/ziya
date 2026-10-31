/**
 * Lanes and drop semantics for the Bench board (design/capabilities-hub.md,
 * slice 6).  Pure: the board and the card both route every move (drag, menu,
 * keyboard) through ``dropIntent`` so the three gestures cannot disagree.
 *
 * Rules:
 *   * environment items are never placed (they are not on the board at all);
 *   * MCP servers may sit within reach (slice 9, design/mcp-on-demand.md);
 *   * dropping an item on the lane it already occupies is a no-op;
 *   * dropping a PINNED item on the lane it would inherit (its ghost outline)
 *     CLEARS the pin rather than pinning the inherited value — a pin equal to
 *     what you would inherit anyway is redundant and would silently stop
 *     following later changes to the lower layer.
 */
import type { PlacementState } from '../../utils/activation';
import type { BenchItemView } from '../../hooks/useBench';

export interface Lane { state: PlacementState; label: string; hint: string; }

// Names are strings only; the API speaks always|ondemand|off.
export const LANES: readonly Lane[] = [
    { state: 'always', label: 'On hand', hint: 'In every turn' },
    { state: 'ondemand', label: 'Within reach', hint: 'The model loads it when needed' },
    { state: 'off', label: 'Shelved', hint: 'Never offered' },
];

export const DRAG_MIME = 'application/x-ziya-bench-key';

export type DropIntent =
    | { kind: 'place'; key: string; toState: PlacementState | null }
    | { kind: 'noop' }
    | { kind: 'rejected'; reason: string };

export function dropIntent(item: BenchItemView, lane: PlacementState): DropIntent {
    if (item.tier === 'environment') return { kind: 'rejected', reason: 'environment_not_placeable' };
    const v = item.atLens;
    if (lane === v.state) return { kind: 'noop' };
    if (!v.ghost && lane === v.inherited) return { kind: 'place', key: item.key, toState: null };
    return { kind: 'place', key: item.key, toState: lane };
}

export interface MoveOption {
    label: string;
    intent: DropIntent;
    disabled: boolean;
    /** Why a disabled option is disabled; shown as the menu item's tooltip. */
    hint?: string;
}

/** The keyboard / context-menu equivalents of every drop the card allows. */
export function moveOptions(item: BenchItemView): MoveOption[] {
    const out: MoveOption[] = [];
    let clears = false;
    for (const lane of LANES) {
        const intent = dropIntent(item, lane.state);
        if (intent.kind === 'noop') continue;
        if (intent.kind === 'place' && intent.toState === null) clears = true;
        out.push({
            label: intent.kind === 'place' && intent.toState === null
                ? `Move to ${lane.label} (inherit)`
                : intent.kind === 'rejected' ? `${lane.label} (not available)` : `Move to ${lane.label}`,
            intent,
            disabled: intent.kind === 'rejected',
        });
    }
    // A pin equal to the inherited value has no lane to drop on; offer the clear.
    if (!item.atLens.ghost && !clears) {
        out.push({ label: 'Clear placement', intent: { kind: 'place', key: item.key, toState: null }, disabled: false });
    }
    return out;
}

export function rejectionText(reason: string, name: string): string {
    if (reason === 'environment_not_placeable') return `${name} is part of the environment and is always available`;
    return `${name}: ${reason}`;
}
