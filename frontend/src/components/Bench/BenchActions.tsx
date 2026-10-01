/**
 * Navigation callbacks the shell (BenchModal) provides and renderers consume:
 * open the MCP status modal focused on one server, open the registry.  These
 * are callbacks only -- no data, no fetching -- so renderers stay renderers.
 * The three toolbar buttons those panels used to have fold into the Bench
 * (design/capabilities-hub.md, slice 6b); this is how the Bench reaches them.
 */
import React, { createContext, useContext } from 'react';

export interface BenchActions {
    /** Open the MCP Servers panel; ``server`` pre-expands that server's row. */
    openMcpServers?: (server?: string) => void;
    /** Open the MCP Registry (browse / install). */
    openRegistry?: () => void;
}

const Ctx = createContext<BenchActions>({});

export const BenchActionsProvider: React.FC<{ actions: BenchActions; children: React.ReactNode }> =
    ({ actions, children }) => <Ctx.Provider value={actions}>{children}</Ctx.Provider>;

export const useBenchActions = (): BenchActions => useContext(Ctx);
