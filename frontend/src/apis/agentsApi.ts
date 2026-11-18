/**
 * Agent overlay API client (design/agent-overlay.md).  Components do not call
 * this directly; useAgent() does.
 */

export interface AgentSummary {
    name: string;
    description: string;
    package: string;
    /** Other packages shipping an agent of this name; ``package`` is the one used. */
    also_in: string[];
    model: string | null;
    /** The model here that ``model`` refers to; null when there is no exact
     *  match, in which case the conversation's own model is used. */
    model_alias?: string | null;
    skills: number;
    servers: number;
    subagents: number;
    /** False while this build cannot enforce or run part of the definition. */
    runnable: boolean;
    blockers: string[];
}

/** One line of the import view: what the source said, what Ziya does. */
export interface AgentImportRow {
    part: string;
    says: string;
    does: string;
    /** applied | mapped | unavailable | unused | startup | pending */
    outcome: string;
    items: string[];
}

export interface AgentImport extends AgentSummary { rows: AgentImportRow[]; }

export class AgentRefused extends Error {
    constructor(public status: number, message: string, public blockers: string[] = []) {
        super(message);
        this.name = 'AgentRefused';
    }
}

const base = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}/agents`;

export async function fetchAgents(
    projectId: string, conversationId?: string | null,
): Promise<{ agents: AgentSummary[]; active: string | null }> {
    const q = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
    const r = await fetch(base(projectId) + q);
    if (!r.ok) throw new Error(`agents GET failed: ${r.status}`);
    return r.json();
}

export async function fetchAgentImport(projectId: string, name: string): Promise<AgentImport> {
    const r = await fetch(base(projectId) + `/import?name=${encodeURIComponent(name)}`);
    if (!r.ok) throw new Error(`agent import failed: ${r.status}`);
    return r.json();
}

/** Turn an agent on (``name``) or off (null) for one conversation. */
export async function setActiveAgent(
    projectId: string, conversationId: string | null, name: string | null,
): Promise<{ active: string | null; agent: AgentSummary | null }> {
    const r = await fetch(base(projectId) + '/active', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: conversationId, name }),
    });
    if (!r.ok) {
        const d = (await r.json().catch(() => ({}))).detail;
        const blockers = d && typeof d === 'object' && Array.isArray(d.blockers) ? d.blockers : [];
        throw new AgentRefused(r.status, typeof d === 'string' ? d : `agent PUT failed: ${r.status}`, blockers);
    }
    return r.json();
}
