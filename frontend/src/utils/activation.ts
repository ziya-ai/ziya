/**
 * Activation resolver — TS mirror of app/services/activation.py
 * (design/capabilities-hub.md, slice 2).
 *
 * The server resolves and the hub renders its view-model; this mirror exists
 * because the LENS switch is client-only (GET returns every layer, the UI
 * re-derives ghost vs pin when the user changes which layer they are
 * editing) and because the slice-4 localStorage migration needs the default
 * rule (discoverable ⇒ off/ondemand, else ⇒ always) without a round trip.
 *
 * Both implementations assert tests/fixtures/activation_cases.json.  The
 * Python side is the source of truth: when the parity test fails, change the
 * fixture only if the server's behaviour genuinely changed, then port it.
 */

export type PlacementState = 'always' | 'ondemand' | 'off';
export const PLACEMENT_STATES: readonly PlacementState[] = ['always', 'ondemand', 'off'];

export type Layer = 'conversation' | 'project' | 'user';
export const WRITABLE_LAYERS: readonly Layer[] = ['conversation', 'project', 'user'];
export type Origin = Layer | 'default' | 'environment';

export type CapabilityKind = 'skill' | 'mcp';
export type Tier = 'placeable' | 'environment';

export interface ItemSpec {
    key: string;
    kind: CapabilityKind;
    tier?: Tier;
    discoverable?: boolean;
}

export interface ResolvedItem {
    key: string;
    kind: CapabilityKind;
    tier: Tier;
    placements: Record<Layer, PlacementState | null>;
    effective: PlacementState;
    origin: Origin;
    default: PlacementState;
}

export interface DroppedEntry { key: string; layer: string; reason: string; }
export interface ResolveResult { items: ResolvedItem[]; dropped: DroppedEntry[]; }

/** Layer name -> key -> state (null = inherit). Missing layers inherit. */
export type Layers = Partial<Record<string, Record<string, string | null | undefined>>>;

export const REASON_UNKNOWN_KEY = 'unknown_key';
export const REASON_INVALID_STATE = 'invalid_state';
export const REASON_INVALID_LAYER = 'invalid_layer';
export const REASON_ENVIRONMENT = 'environment_not_placeable';
export const REASON_MCP_ONDEMAND = 'mcp_ondemand_unsupported';

export class PlacementRejected extends Error {
    constructor(public reason: string, public key: string) {
        super(`${reason}: ${key}`);
        this.name = 'PlacementRejected';
    }
}

export function makeKey(kind: CapabilityKind, name: string): string { return `${kind}:${name}`; }

export function parseKey(key: unknown): [CapabilityKind, string] | null {
    if (typeof key !== 'string') return null;
    const idx = key.indexOf(':');
    if (idx < 0) return null;
    const kind = key.slice(0, idx);
    const name = key.slice(idx + 1);
    if ((kind !== 'skill' && kind !== 'mcp') || !name) return null;
    return [kind, name];
}

export function defaultPlacement(item: ItemSpec): PlacementState {
    if (item.tier === 'environment') return 'always';
    if (item.kind === 'mcp') return 'always';
    return item.discoverable ? 'ondemand' : 'off';
}

function rejectionReason(item: ItemSpec | undefined, state: string | null | undefined): string | null {
    if (!item) return REASON_UNKNOWN_KEY;
    if (item.tier === 'environment') return REASON_ENVIRONMENT;
    if (state === null || state === undefined) return null;
    if (!(PLACEMENT_STATES as readonly string[]).includes(state)) return REASON_INVALID_STATE;
    if (item.kind === 'mcp' && state === 'ondemand') return REASON_MCP_ONDEMAND;
    return null;
}

export function validatePlacement(items: ItemSpec[], key: string, layer: string, state: string | null): void {
    if (!(WRITABLE_LAYERS as readonly string[]).includes(layer)) throw new PlacementRejected(REASON_INVALID_LAYER, key);
    const reason = rejectionReason(items.find(i => i.key === key), state);
    if (reason !== null) throw new PlacementRejected(reason, key);
}

export function resolveActivation(layers: Layers, items: ItemSpec[]): ResolveResult {
    const byKey = new Map(items.map(i => [i.key, i]));
    const dropped: DroppedEntry[] = [];
    const honoured: Record<Layer, Record<string, PlacementState>> = { conversation: {}, project: {}, user: {} };

    for (const layer of WRITABLE_LAYERS) {
        for (const [key, state] of Object.entries(layers[layer] || {})) {
            if (state === null || state === undefined) continue;
            const reason = rejectionReason(byKey.get(key), state);
            if (reason !== null) { dropped.push({ key, layer, reason }); continue; }
            honoured[layer][key] = state as PlacementState;
        }
    }

    const resolved: ResolvedItem[] = items.map(item => {
        const tier: Tier = item.tier ?? 'placeable';
        const def = defaultPlacement(item);
        const placements: Record<Layer, PlacementState | null> = {
            conversation: honoured.conversation[item.key] ?? null,
            project: honoured.project[item.key] ?? null,
            user: honoured.user[item.key] ?? null,
        };
        let effective: PlacementState = def;
        let origin: Origin = 'default';
        if (tier === 'environment') {
            effective = 'always'; origin = 'environment';
        } else {
            for (const layer of WRITABLE_LAYERS) {
                const p = placements[layer];
                if (p !== null) { effective = p; origin = layer; break; }
            }
        }
        return { key: item.key, kind: item.kind, tier, placements, effective, origin, default: def };
    });
    return { items: resolved, dropped };
}

export const alwaysSet = (r: ResolveResult): string[] => r.items.filter(i => i.effective === 'always').map(i => i.key);
export const catalogSet = (r: ResolveResult): string[] => r.items.filter(i => i.effective === 'ondemand').map(i => i.key);
