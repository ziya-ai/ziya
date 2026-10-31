/**
 * "Revised later" markers for task tiles the model moved.
 *
 * When the model revises a staged card (task_card_write) the server moves
 * the card's tile to the revising turn and records where it was in
 * binding.revisions (app/utils/task_card_revision.py).  This places one
 * marker at each former spot, by the same rules the tiles use: an anchored
 * spot below its anchor message, an unanchored one through
 * placeUnanchoredBindings.  A marker is dropped where it would sit next to
 * the tile itself (two revisions in one turn), where its anchor message is
 * gone, and when it repeats a spot already marked for that tile.
 *
 * The last message's slot is reported as "tail": the render loop puts an
 * anchored tile on the last message and an unanchored one in the tail, but
 * both are the same place on screen.
 */
import { placeUnanchoredBindings, type PlacedBindings } from './taskBindingPlacement';

export interface BindingRevision { created_at: number; anchor_message_id?: string | null; }

export interface RevisableBinding {
    id: string;
    created_at?: number;
    anchor_message_id?: string | null;
    revisions?: BindingRevision[] | null;
}

export interface RevisionMarker {
    id: string;
    bindingId: string;
    created_at: number;
    anchor_message_id: string | null;
}

interface Msg { id?: string; role?: string; _timestamp?: number; }

export interface PlacedRevisionMarkers {
    head: RevisionMarker[];
    byMessageIdx: Map<number, RevisionMarker[]>;
    tail: RevisionMarker[];
}

function slotKeys<B extends { id: string }>(p: PlacedBindings<B>, lastIdx: number): Map<string, string> {
    const out = new Map<string, string>();
    p.head.forEach(b => out.set(b.id, 'head'));
    p.byMessageIdx.forEach((arr, idx) => arr.forEach(b => out.set(b.id, idx === lastIdx ? 'tail' : 'm' + idx)));
    p.tail.forEach(b => out.set(b.id, 'tail'));
    p.inFlight.forEach(b => out.set(b.id, 'live'));
    return out;
}

export function placeRevisionMarkers(
    bindings: ReadonlyArray<RevisableBinding>, msgs: ReadonlyArray<Msg>,
): PlacedRevisionMarkers {
    const out: PlacedRevisionMarkers = { head: [], byMessageIdx: new Map(), tail: [] };
    const markers: RevisionMarker[] = [];
    for (const b of bindings) {
        (b.revisions ?? []).forEach((r, i) => markers.push({
            id: b.id + ':rev' + i, bindingId: b.id,
            created_at: r.created_at || 0, anchor_message_id: r.anchor_message_id ?? null,
        }));
    }
    if (markers.length === 0) return out;

    const lastIdx = msgs.length - 1;
    const idxById = new Map<string, number>();
    msgs.forEach((m, i) => { if (m.id) idxById.set(m.id, i); });
    const anchoredKey = (anchor: string): string | null => {
        const i = idxById.get(anchor);
        if (i === undefined) return null;
        return i === lastIdx ? 'tail' : 'm' + i;
    };

    const tilePos = slotKeys(placeUnanchoredBindings(
        bindings.filter(b => !b.anchor_message_id), msgs), lastIdx);
    const tileKey = (b: RevisableBinding) =>
        b.anchor_message_id ? anchoredKey(b.anchor_message_id) : tilePos.get(b.id) ?? null;
    const tileKeys = new Map(bindings.map(b => [b.id, tileKey(b)]));

    const markerPos = slotKeys(placeUnanchoredBindings(
        markers.filter(m => !m.anchor_message_id), msgs), lastIdx);
    const seen = new Set<string>();
    for (const m of markers) {
        const key = m.anchor_message_id ? anchoredKey(m.anchor_message_id) : markerPos.get(m.id) ?? null;
        if (!key || key === 'live' || key === tileKeys.get(m.bindingId)) continue;
        const once = m.bindingId + '@' + key;
        if (seen.has(once)) continue;
        seen.add(once);
        if (key === 'head') out.head.push(m);
        else if (key === 'tail') out.tail.push(m);
        else {
            const idx = Number(key.slice(1));
            const arr = out.byMessageIdx.get(idx) ?? [];
            arr.push(m);
            out.byMessageIdx.set(idx, arr);
        }
    }
    return out;
}
