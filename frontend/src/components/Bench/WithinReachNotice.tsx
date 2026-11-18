/**
 * Says once, per server, that an MCP server sits within reach by default
 * rather than on hand -- the open-core default for MCP is on hand, so a
 * server whose tools are missing from a turn would otherwise be a surprise.
 *
 * A renderer: it reads the items it is handed and reports "keep on hand"
 * through ``place``.  What it has already said is remembered per server key,
 * so a server installed later is announced on its own.
 */
import React, { useState } from 'react';
import type { BenchItemView } from '../../hooks/useBench';
import type { Layer, PlacementState } from '../../utils/activation';

export const REACH_NOTICE_KEY = 'ZIYA_BENCH_REACH_NOTICE_SEEN';

/** MCP servers whose within-reach placement is a declared default nobody chose. */
export function reachDefaults(items: BenchItemView[]): BenchItemView[] {
    return items.filter(i =>
        i.kind === 'mcp' && i.tier !== 'environment' && i.default === 'ondemand' && i.origin === 'default');
}

function loadSeen(): string[] {
    try {
        const v = JSON.parse(localStorage.getItem(REACH_NOTICE_KEY) || '[]');
        return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
    } catch {
        return [];
    }
}

export interface WithinReachNoticeProps {
    items: BenchItemView[];
    place: (key: string, state: PlacementState | null, layer?: Layer) => Promise<void>;
}

const linkBtn: React.CSSProperties = {
    border: 'none', background: 'none', color: 'inherit', cursor: 'pointer',
    textDecoration: 'underline', padding: 0, fontSize: 12,
};

export const WithinReachNotice: React.FC<WithinReachNoticeProps> = ({ items, place }) => {
    const [seen, setSeen] = useState<string[]>(loadSeen);
    const [busy, setBusy] = useState(false);
    const fresh = reachDefaults(items).filter(i => !seen.includes(i.key));
    if (fresh.length === 0) return null;

    const markSeen = (keys: string[]) => {
        const next = Array.from(new Set([...seen, ...keys]));
        setSeen(next);
        try { localStorage.setItem(REACH_NOTICE_KEY, JSON.stringify(next)); } catch { /* private mode */ }
    };
    // Pinned at the user layer: "on hand" here means in every project.
    const keepOnHand = async () => {
        setBusy(true);
        const done: string[] = [];
        for (const i of fresh) {
            try { await place(i.key, 'always', 'user'); done.push(i.key); } catch { /* stays listed */ }
        }
        setBusy(false);
        markSeen(done);
    };

    const n = fresh.length;
    const names = fresh.slice(0, 3).map(i => i.name).join(', ') + (n > 3 ? ', …' : '');
    return (
        <div role="status" data-testid="reach-notice" title={fresh.map(i => i.name).join('\n')}
             style={{ fontSize: 12, padding: '6px 10px', borderRadius: 6, display: 'flex', gap: 10,
                      alignItems: 'center', flexWrap: 'wrap',
                      border: '1px solid #d48806', background: 'rgba(250,173,20,0.12)' }}>
            <span>
                <b>{n} MCP server{n === 1 ? '' : 's'}</b> sit{n === 1 ? 's' : ''} within reach by default
                {' '}({names}): the model loads {n === 1 ? 'it' : 'one'} when needed, so {n === 1 ? 'its' : 'their'} tools
                are not sent with every turn.
            </span>
            <button type="button" data-testid="reach-keep" disabled={busy} onClick={() => void keepOnHand()} style={linkBtn}>
                Keep {n === 1 ? 'it' : 'all'} on hand
            </button>
            <button type="button" data-testid="reach-dismiss" disabled={busy}
                    onClick={() => markSeen(fresh.map(i => i.key))} style={linkBtn}>
                Dismiss
            </button>
        </div>
    );
};
