/**
 * What installed packages need that is not configured here, listed by
 * package.  A renderer: the list arrives from the server (``view.missing``)
 * and "find" is reported up; nothing is installed from here.
 *
 * ``installable`` rows can be found in a registry and get the find action.
 * The rest are servers a package's own agents define; they are named so the
 * gap is visible, with no action to take from the Bench.
 */
import React from 'react';

export interface MissingCapability { kind: string; name: string; groups: string[][]; installable: boolean; }

export const NO_PACKAGE = 'Other';

const roots = (m: MissingCapability): string[] =>
    (m.groups.length ? Array.from(new Set(m.groups.map(g => g[0]))) : [NO_PACKAGE]);

/** Packages A-Z, each with the capabilities it is missing. */
export function missingByPackage(missing: MissingCapability[]): Array<{ pkg: string; items: MissingCapability[] }> {
    const by = new Map<string, MissingCapability[]>();
    for (const m of missing) {
        for (const r of roots(m)) by.set(r, [...(by.get(r) ?? []), m]);
    }
    return Array.from(by.entries())
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([pkg, items]) => ({ pkg, items }));
}

export function missingCountByRoot(missing: MissingCapability[]): Record<string, number> {
    const out: Record<string, number> = {};
    for (const { pkg, items } of missingByPackage(missing)) out[pkg] = items.length;
    return out;
}

export interface NotInstalledHereProps {
    missing: MissingCapability[];
    /** Open the Find view on this name; absent = no find action. */
    onFind?: (name: string) => void;
}

const linkBtn: React.CSSProperties = {
    border: 'none', background: 'none', color: 'inherit', cursor: 'pointer',
    textDecoration: 'underline', padding: 0, fontSize: 11,
};

export const NotInstalledHere: React.FC<NotInstalledHereProps> = ({ missing, onFind }) => {
    if (missing.length === 0) return null;
    return (
        // Capped and scrolling: many packages each missing servers must not
        // take the board's height.  No shrink, so the cap is what it gets.
        <div data-testid="bench-missing" style={{ fontSize: 11, display: 'flex', flexDirection: 'column', gap: 2,
                                                 maxHeight: 96, overflowY: 'auto', flexShrink: 0 }}>
            {missingByPackage(missing).map(({ pkg, items }) => (
                <div key={pkg} data-testid={`missing-pkg-${pkg}`}
                     style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                    <span>
                        <b>{pkg}</b> <span style={{ color: '#d46b08' }}>needs {items.length} server{items.length === 1 ? '' : 's'}
                        {' '}not installed here:</span>
                    </span>
                    {items.map(m => (m.installable && onFind ? (
                        <button key={m.name} type="button" data-testid={`missing-find-${pkg}|${m.name}`}
                                title="Search the sources for this server" onClick={() => onFind(m.name)} style={linkBtn}>
                            {m.name} · find ▸
                        </button>
                    ) : (
                        <span key={m.name} data-testid={`missing-item-${pkg}|${m.name}`} style={{ opacity: 0.7 }}
                              title={m.installable ? undefined
                                  : "Defined by the package's own agents; it is not in a registry"}>
                            {m.name}{m.installable ? '' : ' (set up by the package)'}
                        </span>
                    )))}
                </div>
            ))}
        </div>
    );
};
