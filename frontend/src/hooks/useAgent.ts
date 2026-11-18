/**
 * useAgent() -- the agent running in one conversation
 * (design/agent-overlay.md).  Owns the fetch and the on/off writes; the
 * server holds the state (the chat record's ``activeAgent``).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchAgentImport, fetchAgents, setActiveAgent } from '../apis/agentsApi';
import type { AgentImport, AgentSummary } from '../apis/agentsApi';
import { setAgentModel } from '../utils/modelPins';

export type { AgentImport, AgentImportRow, AgentSummary } from '../apis/agentsApi';

export function useAgent(projectId: string | null | undefined, conversationId: string | null | undefined) {
    const [agents, setAgents] = useState<AgentSummary[]>([]);
    const [active, setActive] = useState<string | null>(null);
    const seq = useRef(0);

    const refresh = useCallback(async () => {
        const mine = ++seq.current;
        if (!projectId) { setAgents([]); setActive(null); return; }
        try {
            const r = await fetchAgents(projectId, conversationId);
            // A late answer for a conversation the user has left is dropped.
            if (seq.current !== mine) return;
            setAgents(r.agents);
            setActive(r.active);
            // The agent's model takes part in this conversation's model
            // resolution (utils/modelPins).  Published only from an answer:
            // a failed fetch says nothing about which agent is on.
            setAgentModel(conversationId, r.agents.find(a => a.name === r.active)?.model_alias ?? null);
        } catch {
            if (seq.current === mine) { setAgents([]); setActive(null); }
        }
    }, [projectId, conversationId]);

    useEffect(() => { void refresh(); }, [refresh]);

    const write = useCallback(async (name: string | null) => {
        if (!projectId) throw new Error('no project');
        const mine = ++seq.current;
        const r = await setActiveAgent(projectId, conversationId ?? null, name);
        if (seq.current === mine) {
            setActive(r.active);
            setAgentModel(conversationId, r.active ? (r.agent?.model_alias ?? null) : null);
        }
    }, [projectId, conversationId]);

    const turnOn = useCallback((name: string) => write(name), [write]);
    const turnOff = useCallback(() => write(null), [write]);

    const importOf = useCallback((name: string): Promise<AgentImport> => {
        if (!projectId) return Promise.reject(new Error('no project'));
        return fetchAgentImport(projectId, name);
    }, [projectId]);

    return {
        agents,
        active,
        /** The active agent's summary; null when none, or when it is no longer installed. */
        activeAgent: agents.find(a => a.name === active) ?? null,
        turnOn,
        turnOff,
        importOf,
        refresh,
    };
}
