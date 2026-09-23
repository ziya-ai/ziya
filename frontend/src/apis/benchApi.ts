/**
 * Bench API client (design/capabilities-hub.md, slice 4).
 * GET returns the server-resolved view-model; PUT writes one placement and
 * returns the re-resolved view-model. Components never call this directly —
 * useBench() does.
 */
import type { Layer, Origin, PlacementState, Tier, CapabilityKind } from '../utils/activation';

export interface BenchViewItem {
    key: string;
    kind: CapabilityKind;
    tier: Tier;
    name: string;
    provenance: string;
    tokens: number;
    health: Record<string, unknown>;
    placements: Record<Layer, PlacementState | null>;
    effective: PlacementState;
    origin: Origin;
    default: PlacementState;
}

export interface BenchView {
    items: BenchViewItem[];
    dropped: Array<{ key: string; layer: string; reason: string }>;
    lenses: Layer[];
    deltas: Record<Layer, number>;
    weight: { always: number; ondemand: number; environment: number };
    conversation_id: string | null;
}

export class BenchPlacementRejected extends Error {
    constructor(public reason: string, public key: string) {
        super(`${reason}: ${key}`);
        this.name = 'BenchPlacementRejected';
    }
}

const base = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/bench`;

export async function fetchBench(projectId: string, conversationId?: string | null): Promise<BenchView> {
    const q = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
    const r = await fetch(base(projectId) + q);
    if (!r.ok) throw new Error(`bench GET failed: ${r.status}`);
    return r.json();
}

export async function placeBench(
    projectId: string, key: string, layer: Layer, state: PlacementState | null, conversationId?: string | null,
): Promise<BenchView> {
    const r = await fetch(base(projectId) + '/place', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, layer, state, conversation_id: conversationId ?? null }),
    });
    if (r.status === 409) {
        const d = (await r.json().catch(() => ({}))).detail || {};
        throw new BenchPlacementRejected(d.reason || 'rejected', d.key || key);
    }
    if (!r.ok) throw new Error(`bench PUT failed: ${r.status}`);
    return r.json();
}
