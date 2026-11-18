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
    /** Where it came from, outermost first (package, then component).
     *  Several paths when more than one package ships it; empty when none. */
    groups?: string[][];
    tokens: number;
    health: Record<string, unknown>;
    placements: Record<Layer, PlacementState | null>;
    effective: PlacementState;
    origin: Origin;
    default: PlacementState;
    /** Who wrote each layer's placement; absent for layers with none, null
     *  for the conversation layer (the chat record keeps no meta). */
    meta?: Partial<Record<Layer, PlacementMeta | null>>;
    /** Epoch ms of the last use (skill loaded, MCP tool called); null when never used. */
    last_used?: number | null;
    /** Within-reach MCP only: what this conversation has loaded — '*' or tool names. */
    loaded?: string[] | '*' | null;
}

export type PlacementVia = 'hub' | 'migration' | 'palette' | 'api';
export interface PlacementMeta { via: PlacementVia | string; at: number; }

export interface BenchView {
    items: BenchViewItem[];
    dropped: Array<{ key: string; layer: string; reason: string }>;
    /** What installed packages need that is not configured here; ``installable``
     *  means a registry can supply it. */
    missing?: Array<{ kind: string; name: string; groups: string[][]; installable: boolean }>;
    lenses: Layer[];
    deltas: Record<Layer, number>;
    weight: { always: number; ondemand: number; environment: number };
    conversation_id: string | null;
    /** Set by PUT /environment when a toggle has a consequence worth saying. */
    warning?: string;
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
    projectId: string, key: string, layer: Layer, state: PlacementState | null,
    conversationId?: string | null, via: PlacementVia = 'hub',
): Promise<BenchView> {
    const r = await fetch(base(projectId) + '/place', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, layer, state, conversation_id: conversationId ?? null, via }),
    });
    if (r.status === 409) {
        const d = (await r.json().catch(() => ({}))).detail || {};
        throw new BenchPlacementRejected(d.reason || 'rejected', d.key || key);
    }
    if (!r.ok) throw new Error(`bench PUT failed: ${r.status}`);
    return r.json();
}

/** Toggle a builtin tool category (user-wide; the environment is not a lens). */
export async function setBenchEnvironment(
    projectId: string, category: string, enabled: boolean, conversationId?: string | null,
): Promise<BenchView> {
    const q = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
    const r = await fetch(base(projectId) + '/environment' + q, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category, enabled }),
    });
    if (!r.ok) throw new Error(`bench environment PUT failed: ${r.status}`);
    return r.json();
}

/** Card-back detail; heavy fields live here, not on BenchViewItem. */
export interface BenchItemDetail {
    key: string; kind: CapabilityKind; name: string; description: string;
    // skills
    prompt?: string; path?: string | null; source?: string; tokens?: number; keywords?: string[];
    /** Skill.id for the skills REST API, and whether it accepts writes (custom only). */
    skill_id?: string; editable?: boolean;
    // mcp
    tools?: string[]; enabled?: boolean; connected?: boolean; builtin?: boolean;
    tool_permissions?: Record<string, boolean>;
    /** Authorization gate; null/absent = may run.  Today: rug-pull quarantine. */
    gate?: { state: string; reason: string } | null;
    /** What a definition change does to this server and, while it is held, what changed. */
    updates?: UpdateInfo | null;
    /** Per-tool rows for the detail drawer: what each tool costs and does. */
    tool_details?: ToolDetail[];
}
export interface ToolDetail { name: string; description: string; tokens: number; enabled: boolean; }

export async function fetchBenchDetail(projectId: string, key: string): Promise<BenchItemDetail> {
    const r = await fetch(base(projectId) + `/detail?key=${encodeURIComponent(key)}`);
    if (!r.ok) throw new Error(`bench detail failed: ${r.status}`);
    return r.json();
}

/** Per-tool switch on an MCP server; returns the refreshed detail AND the re-costed view. */
export async function setBenchTool(
    projectId: string, server: string, tool: string, enabled: boolean, conversationId?: string | null,
): Promise<{ detail: BenchItemDetail; view: BenchView }> {
    const q = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
    const r = await fetch(base(projectId) + '/tool' + q, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server, tool, enabled }),
    });
    if (!r.ok) throw new Error(`bench tool PUT failed: ${r.status}`);
    return r.json();
}

// -- authorization gates ------------------------------------------------------

/** The manager's verdict on a re-authorization (app/mcp/manager.py reauthorize_server). */
export interface ReauthorizeVerdict {
    success: boolean; message?: string;
    /** Refusal: tools still matching an injection pattern, and whether an override is offered. */
    blocking_tools?: Record<string, string[]>; advisory_tools?: Record<string, string[]>; can_force?: boolean;
}
export interface ReauthorizeResult { result: ReauthorizeVerdict; detail: BenchItemDetail; view: BenchView; }

/** Lift a rug-pull quarantine; ``force`` after reviewing the flagged descriptions. */
export async function reauthorizeBenchServer(
    projectId: string, server: string, force: boolean, conversationId?: string | null,
): Promise<ReauthorizeResult> {
    const q = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
    const r = await fetch(base(projectId) + '/reauthorize' + q, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server, force }),
    });
    if (!r.ok) throw new Error(`bench reauthorize failed: ${r.status}`);
    return r.json();
}

/** Difference between the approved definitions and what the server lists now. */
export interface UpdateChanges {
    /** False when the approved definitions were never recorded; the lists are then empty, not "no change". */
    known: boolean;
    added: string[]; removed: string[]; changed: { name: string; what: string[] }[]; summary: string;
}
/** app/utils/mcp_update_policy.py describe(). */
export interface UpdateInfo {
    /** ask = hold on every change; follow = accept a change that scans clean. */
    policy: 'ask' | 'follow';
    via?: 'server' | 'publisher' | null;
    /** Registry provider the server was installed from; null for a hand-configured one. */
    publisher?: string | null;
    /** That registry's display name; the id when the registry is not loaded. */
    publisher_name?: string | null;
    publisher_follows?: boolean;
    /** A per-server "follow" no longer applies because the launch command changed. */
    lapsed?: boolean;
    accepted_at?: number | null; accepted_by?: string | null; last_change?: string | null;
    changes?: UpdateChanges;
}

/** Set the update policy for one server, or (scope publisher) every server from its registry provider. */
export async function setBenchUpdatePolicy(
    projectId: string, server: string, policy: 'ask' | 'follow', scope: 'server' | 'publisher' = 'server',
): Promise<BenchItemDetail> {
    const r = await fetch(base(projectId) + '/update-policy', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server, policy, scope }),
    });
    if (!r.ok) throw new Error(`bench update-policy failed: ${r.status}`);
    return (await r.json()).detail;
}

// -- find (slice 8b) --------------------------------------------------------
// Browse is a query, not a shelf: empty q returns panels derived from this
// bench; a query returns one column per source.  Nothing enumerates a catalog.

export interface FindHit {
    source: string; id: string; name: string; description: string;
    version?: string; requires?: string[];
    /** Registry support level -- a badge on a hit, never a gate. */
    support_level?: string | null;
    /** Skills in a package hit (AIM) or tools in an MCP server hit that matched. */
    matched?: string[];
    /** MCP server hits: the registry's tags for the server. */
    tags?: string[];
    installed: boolean; missing_requires?: string[];
    provider?: string | null;
}
export interface FindRegistry { id: string; name: string; selected: boolean; }
export interface FindColumn { source: string; name: string; kind: string; total: number | null; hits: FindHit[]; error?: string;
    /** MCP column only: every registry, and whether this search asked it. */
    registries?: FindRegistry[]; }
export interface FindHome {
    needs: Array<{ key: string; name: string; missing: string[] }>;
    updates: Array<{ key: string; name: string; source: string; id: string;
                     installed_version?: string | null; current_version?: string | null }>;
    pairs_with: FindHit[];
}
export interface FindResults { q: string; per_source: FindColumn[]; }
export type FindResponse = FindHome | FindResults;
export const isFindResults = (r: FindResponse): r is FindResults => 'per_source' in r;
/** The MCP registry's column id; its hits link to the registry panel rather than installing here. */
export const MCP_FIND_COLUMN = 'mcp-registry';

export async function fetchBenchFind(projectId: string, q: string, n = 8, registriesOff?: string[]): Promise<FindResponse> {
    const off = registriesOff && registriesOff.length
        ? `&registries_off=${encodeURIComponent(registriesOff.join(','))}` : '';
    const r = await fetch(base(projectId) + `/browse?q=${encodeURIComponent(q)}&n=${n}${off}`);
    if (!r.ok) throw new Error(`bench browse failed: ${r.status}`);
    return r.json();
}

export interface InstallResult {
    installed: { name: string; path: string; source: string; remote_id: string; version: string; installed: string[] };
    missing_requires: string[];
    view: BenchView;
}

/** Fetch + write + (optionally) place in one call. */
export async function installBenchSkill(
    projectId: string, source: string, id: string, layer: 'user' | 'project',
    state: PlacementState | null, conversationId?: string | null,
): Promise<InstallResult> {
    const r = await fetch(base(projectId) + '/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source, id, layer, state, conversation_id: conversationId ?? null }),
    });
    if (r.status === 409) {
        const d = (await r.json().catch(() => ({}))).detail || {};
        throw new BenchPlacementRejected(d.reason || 'rejected', d.key || id);
    }
    if (!r.ok) throw new Error(`bench install failed: ${r.status}`);
    return r.json();
}

export interface ServerInstallResult {
    status: string; server_name?: string; service_id?: string;
    warnings?: string[]; connected?: boolean | null;
}

/** Install an MCP server from its registry: the registry panel's installer. */
export async function installBenchServer(serviceId: string, providerId?: string | null): Promise<ServerInstallResult> {
    const r = await fetch('/api/mcp/registry/services/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service_id: serviceId, provider_id: providerId ?? null }),
    });
    const body = await r.json().catch(() => null);
    if (!r.ok) {
        // A failure carries the installer's own diagnosis in detail.message.
        const d = body?.detail;
        throw new Error(typeof d === 'string' ? d : d?.message || `install failed: HTTP ${r.status}`);
    }
    return body;
}
