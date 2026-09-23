/**
 * Feeds the header-button outlines (see utils/headerAttention.ts).
 *
 * Three sources, one refresh cycle:
 *   /api/mcp/status                        quarantined servers, config errors
 *   /api/mcp/shell-config                  unsigned / staged shell escalation
 *   .../task-cards/signature-summary       cards with unsigned escalation
 *
 * Re-polls on window focus because signing is out of band (the user runs
 * `ziya-approve` in a terminal and comes back), on 'mcpStatusChanged'
 * (fired by every MCP mutation in the modals), on the card-scope refresh
 * event, and on an explicit refresh() the App calls when a panel closes.
 * MCP initializes in the background after the page loads, so while
 * /api/mcp/status still reports initialized=false the hook retries a
 * few times rather than showing a clean button for a quarantine that
 * has not been computed yet.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { CARD_SCOPE_REFRESH_EVENT } from '../components/TaskCard/useCardSignatureStatus';
import {
    Attention, NO_ATTENTION,
    deriveMcpAttention, deriveShellAttention, deriveTaskCardAttention,
} from '../utils/headerAttention';

export interface HeaderAttention {
    mcp: Attention;
    shell: Attention;
    taskCards: Attention;
    refresh: () => void;
}

const INIT_RETRY_MS = 5000;
const INIT_RETRY_MAX = 12;

async function fetchJson(url: string): Promise<any | null> {
    try {
        const res = await fetch(url);
        if (!res.ok) return null;
        return await res.json();
    } catch {
        // Advisory indicator: a failed poll shows nothing rather than
        // inventing a problem.
        return null;
    }
}

export function useHeaderAttention(
    { mcpEnabled, projectId }: { mcpEnabled: boolean; projectId?: string },
): HeaderAttention {
    const [mcp, setMcp] = useState<Attention>(NO_ATTENTION);
    const [shell, setShell] = useState<Attention>(NO_ATTENTION);
    const [taskCards, setTaskCards] = useState<Attention>(NO_ATTENTION);
    const [nonce, setNonce] = useState(0);
    const initRetries = useRef(0);

    const refresh = useCallback(() => setNonce(n => n + 1), []);

    useEffect(() => {
        let cancelled = false;
        let retryTimer: ReturnType<typeof setTimeout> | null = null;
        const run = async () => {
            if (mcpEnabled) {
                const [status, shellCfg] = await Promise.all([
                    fetchJson('/api/mcp/status'),
                    fetchJson('/api/mcp/shell-config'),
                ]);
                if (cancelled) return;
                setMcp(deriveMcpAttention(status));
                setShell(deriveShellAttention(shellCfg));
                if (status && !status.disabled && !status.initialized
                        && initRetries.current < INIT_RETRY_MAX) {
                    initRetries.current += 1;
                    retryTimer = setTimeout(refresh, INIT_RETRY_MS);
                }
            } else {
                setMcp(NO_ATTENTION);
                setShell(NO_ATTENTION);
            }
            if (projectId) {
                const summary = await fetchJson(
                    `/api/v1/projects/${encodeURIComponent(projectId)}/task-cards/signature-summary`);
                if (cancelled) return;
                setTaskCards(deriveTaskCardAttention(summary));
            } else {
                setTaskCards(NO_ATTENTION);
            }
        };
        run();
        return () => {
            cancelled = true;
            if (retryTimer) clearTimeout(retryTimer);
        };
    }, [mcpEnabled, projectId, nonce, refresh]);

    useEffect(() => {
        const onEvent = () => refresh();
        window.addEventListener('mcpStatusChanged', onEvent);
        window.addEventListener(CARD_SCOPE_REFRESH_EVENT, onEvent);
        window.addEventListener('focus', onEvent);
        return () => {
            window.removeEventListener('mcpStatusChanged', onEvent);
            window.removeEventListener(CARD_SCOPE_REFRESH_EVENT, onEvent);
            window.removeEventListener('focus', onEvent);
        };
    }, [refresh]);

    return { mcp, shell, taskCards, refresh };
}
