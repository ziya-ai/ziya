/**
 * The composer's ``/agent`` command (design/agent-overlay.md).
 *
 *   /agent            list the installed agents
 *   /agent <name>     turn one on for this conversation
 *   /agent off        turn the active one off
 *
 * The composer parses the text and announces the command; AgentBar, which
 * owns the agent state for the conversation, carries it out.  Nothing is
 * sent to the model.
 */

export type AgentCommand = { kind: 'on'; name: string } | { kind: 'off' } | { kind: 'pick' };

export const AGENT_COMMAND_EVENT = 'ziya:agent-command';

/** null when the text is not an ``/agent`` command (it is then sent as chat). */
export function parseAgentCommand(text: string): AgentCommand | null {
    const m = /^\/agent(?:\s+([\s\S]*))?$/.exec(text.trim());
    if (!m) return null;
    const arg = (m[1] ?? '').trim();
    if (!arg) return { kind: 'pick' };
    // Agent names are single tokens; "/agent is a word I use" is a message.
    if (/\s/.test(arg)) return null;
    if (arg.toLowerCase() === 'off') return { kind: 'off' };
    return { kind: 'on', name: arg };
}

export function dispatchAgentCommand(conversationId: string | null | undefined, command: AgentCommand): void {
    if (typeof window === 'undefined') return;
    window.dispatchEvent(new CustomEvent(AGENT_COMMAND_EVENT, { detail: { conversationId, command } }));
}

export interface AgentCommandDetail {
    conversationId: string | null | undefined;
    command: AgentCommand;
}
