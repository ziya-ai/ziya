/** Test-only builders: items go through the REAL viewAtLens, so a card's
 * atLens is exactly what useBench would hand the board. */
import { viewAtLens } from '../../context/LensContext';
import type { BenchViewItem } from '../../apis/benchApi';
import type { BenchItemView } from '../../hooks/useBench';
import type { Layer, PlacementState } from '../../utils/activation';

type P = PlacementState | null;

export function mk(
    key: string,
    opts: { conversation?: P; project?: P; user?: P; def?: PlacementState; tier?: 'placeable' | 'environment';
            provenance?: string; lens?: Layer } = {},
): BenchItemView {
    const [kind, name] = key.split(':') as ['skill' | 'mcp', string];
    const placements = { conversation: opts.conversation ?? null, project: opts.project ?? null, user: opts.user ?? null };
    const def: PlacementState = opts.def ?? (kind === 'mcp' ? 'always' : 'ondemand');
    const tier = opts.tier ?? 'placeable';
    let effective: PlacementState = def;
    let origin: BenchViewItem['origin'] = 'default';
    if (tier === 'environment') { effective = 'always'; origin = 'environment'; }
    else for (const l of ['conversation', 'project', 'user'] as Layer[]) {
        if (placements[l] !== null) { effective = placements[l] as PlacementState; origin = l; break; }
    }
    const item: BenchViewItem = {
        key, kind, tier, name, provenance: opts.provenance ?? 'builtin', tokens: 10, health: {},
        placements, effective, origin, default: def,
    };
    return { ...item, atLens: viewAtLens(item, opts.lens ?? 'project') };
}

export function mockDataTransfer() {
    const store: Record<string, string> = {};
    return {
        setData: (t: string, v: string) => { store[t] = v; },
        getData: (t: string) => store[t] ?? '',
        effectAllowed: 'all', dropEffect: 'none', types: [] as string[],
    };
}
