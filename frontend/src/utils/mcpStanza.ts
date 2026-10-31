/**
 * A server's entry as a complete mcp_config.json that another Ziya instance
 * loads as-is: keyed by its name under "mcpServers".
 *
 * Keys the loader adds to server_configs at load time are dropped: they are
 * Ziya's bookkeeping, not the user's entry, and "builtin": true carried into
 * another instance would change how that instance treats the server.
 */
const LOADER_ADDED_KEYS = ['builtin'];

export function portableServerStanza(
    name: string,
    config: Record<string, any>,
): { mcpServers: Record<string, Record<string, any>> } {
    const entry: Record<string, any> = { ...config };
    for (const k of LOADER_ADDED_KEYS) delete entry[k];
    return { mcpServers: { [name]: entry } };
}
