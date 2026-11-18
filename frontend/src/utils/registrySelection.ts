/**
 * Which MCP registries the user has switched off for searching.
 *
 * One setting shared by the registry panel (MCPRegistryModal) and the Bench
 * Find view.  The OFF set is stored, so a registry added later starts on and
 * each view can leave ids it does not know about untouched.
 */
export const REGISTRY_OFF_KEY = 'ZIYA_MCP_REGISTRIES_OFF';
export const REGISTRY_OFF_EVENT = 'ziya:mcp-registries-off-changed';

export function readRegistriesOff(): string[] {
    try {
        const v = JSON.parse(localStorage.getItem(REGISTRY_OFF_KEY) || '[]');
        return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
    } catch {
        return [];
    }
}

export const sameRegistrySet = (a: string[], b: string[]): boolean =>
    a.length === b.length && [...a].sort().join('\n') === [...b].sort().join('\n');

/** Stores the set and tells other mounted views; a no-op when unchanged. */
export function writeRegistriesOff(ids: string[]): void {
    const next = Array.from(new Set(ids));
    if (sameRegistrySet(next, readRegistriesOff())) return;
    try {
        if (next.length) localStorage.setItem(REGISTRY_OFF_KEY, JSON.stringify(next));
        else localStorage.removeItem(REGISTRY_OFF_KEY);
    } catch { /* private mode */ }
    if (typeof window !== 'undefined') window.dispatchEvent(new Event(REGISTRY_OFF_EVENT));
}
