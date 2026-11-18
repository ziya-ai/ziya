/**
 * @jest-environment jsdom
 *
 * Agent overlay, second frontend slice (design/agent-overlay.md): the
 * agent's model in model resolution, the transcript seam, and the Bench's
 * agent list.  The API module is mocked; the pin store, the hook and the
 * components are real.
 */
import React from 'react';
import * as fs from 'fs';
import * as path from 'path';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

const mockFetchAgents = jest.fn();
const mockFetchImport = jest.fn();
const mockSetActive = jest.fn();
jest.mock('../../apis/agentsApi', () => ({
    fetchAgents: (...a: any[]) => mockFetchAgents(...a),
    fetchAgentImport: (...a: any[]) => mockFetchImport(...a),
    setActiveAgent: (...a: any[]) => mockSetActive(...a),
}));

import { AgentBar } from '../AgentBar';
import { AgentSeam } from '../AgentSeam';
import { PackagedAgents } from '../Bench/PackagedAgents';
import { dispatchAgentCommand } from '../../utils/agentCommand';
import {
    clearAllModelPins, getAgentModel, resolveModelPin, setAgentModel, setConversationModelPin,
} from '../../utils/modelPins';

const mk = (name: string, pkg: string, over: any = {}) => ({
    name, description: `${name} does things`, package: pkg, also_in: [], model: 'claude-opus-5.5',
    model_alias: 'opus5.5', skills: 1, servers: 0, subagents: 0, runnable: true, blockers: [], ...over,
});
const AGENTS = [
    mk('helper', 'PkgA'),
    mk('plain', 'PkgA', { model: null, model_alias: null }),
    mk('guarded', 'PkgB', { runnable: false, blockers: ['its restrictions are not enforced yet'] }),
];

let active: string | null;
beforeEach(() => {
    active = null;
    clearAllModelPins();
    mockFetchAgents.mockReset().mockImplementation(async () => ({ agents: AGENTS, active }));
    mockFetchImport.mockReset().mockImplementation(async (_p: string, name: string) =>
        ({ ...mk(name, 'PkgA'), rows: [{ part: 'Prompt', says: 's', does: 'applied as written', outcome: 'applied', items: [] }] }));
    mockSetActive.mockReset().mockImplementation(async (_p: string, _c: string, name: string | null) => {
        active = name;
        return { active: name, agent: AGENTS.find(a => a.name === name) ?? null };
    });
});

async function mount(props: any = {}) {
    const view = render(<AgentBar projectId="P" conversationId="C" {...props} />);
    await waitFor(() => expect(mockFetchAgents).toHaveBeenCalled());
    await act(async () => {});
    return view;
}
const turnOn = (name: string) => act(async () => { dispatchAgentCommand('C', { kind: 'on', name }); });

describe("the agent's model", () => {
    it('is used for the conversation while the agent is on, and not after', async () => {
        await mount();
        expect(resolveModelPin({ conversationId: 'C' })).toBeNull();
        await turnOn('helper');
        await waitFor(() => expect(resolveModelPin({ conversationId: 'C' }))
            .toEqual({ model: 'opus5.5', scope: 'agent', persistent: false }));
        expect(resolveModelPin({ conversationId: 'OTHER' })).toBeNull();          // this conversation only
        expect(await screen.findByTestId('agent-chip')).toHaveTextContent('helper · opus5.5');

        fireEvent.click(screen.getByTestId('agent-leave'));
        await waitFor(() => expect(resolveModelPin({ conversationId: 'C' })).toBeNull());
    });

    it('is picked up from the server for an agent that was already on', async () => {
        active = 'helper';
        await mount();
        await waitFor(() => expect(getAgentModel('C')).toBe('opus5.5'));
    });

    it("sits below the conversation's own pin and above folder and project pins", () => {
        setAgentModel('C', 'opus5.5');
        const persisted = { folder: 'sonnet5', project: 'haiku-5.5' };
        expect(resolveModelPin({ conversationId: 'C', folderId: 'F', projectId: 'P', persisted }))
            .toEqual({ model: 'opus5.5', scope: 'agent', persistent: false });
        expect(resolveModelPin({ conversationId: 'C', persisted: { ...persisted, conversation: 'sonnet4.6' } }))
            .toEqual({ model: 'sonnet4.6', scope: 'conversation', persistent: true });
        setConversationModelPin('C', 'opus4.8');
        expect(resolveModelPin({ conversationId: 'C', persisted })?.scope).toBe('conversation');
    });

    it('the chip and the panel say when your pin wins', async () => {
        active = 'helper';
        await mount({ pinnedModel: 'haiku-5.5' });
        expect(await screen.findByTestId('agent-chip')).toHaveTextContent('helper · haiku-5.5 (your pin)');
        fireEvent.click(screen.getByTestId('agent-chip-open'));
        expect(await screen.findByTestId('agent-model-note'))
            .toHaveTextContent('The agent asks for opus5.5; this conversation is pinned to haiku-5.5.');
    });

    it('an agent with no matching model leaves the model alone', async () => {
        await mount();
        await turnOn('plain');
        expect(await screen.findByTestId('agent-chip')).toHaveTextContent(/^plain\s*×$/);
        expect(resolveModelPin({ conversationId: 'C' })).toBeNull();
    });

    it('a failed fetch does not clear what is known', async () => {
        setAgentModel('C', 'opus5.5');
        mockFetchAgents.mockRejectedValue(new Error('down'));
        await mount();
        expect(getAgentModel('C')).toBe('opus5.5');
    });
});

describe('change reporting and the seam', () => {
    it('reports started and ended, and nothing for a refusal', async () => {
        const onChange = jest.fn();
        await mount({ onChange });
        await turnOn('guarded');
        expect(await screen.findByTestId('agent-notice')).toBeInTheDocument();
        expect(onChange).not.toHaveBeenCalled();
        await turnOn('helper');
        await waitFor(() => expect(onChange).toHaveBeenCalledWith('started', 'helper', 'C'));
        fireEvent.click(await screen.findByTestId('agent-leave'));
        await waitFor(() => expect(onChange).toHaveBeenLastCalledWith('ended', 'helper', 'C'));
        expect(onChange).toHaveBeenCalledTimes(2);
    });

    it('draws the seam for each kind', () => {
        const { rerender } = render(<AgentSeam notice={{ kind: 'started', name: 'helper' }} />);
        expect(screen.getByTestId('agent-seam')).toHaveTextContent('helper from here');
        rerender(<AgentSeam notice={{ kind: 'ended', name: 'helper' }} />);
        expect(screen.getByTestId('agent-seam')).toHaveTextContent('helper ended');
    });
});

describe('Bench agent list', () => {
    const model = (over: any = {}) => ({
        agents: AGENTS as any, active: null, canUse: true, onUse: jest.fn(),
        importOf: (name: string) => mockFetchImport('P', name), ...over,
    });

    it('renders nothing when no agent is installed', () => {
        const { container } = render(<PackagedAgents model={model({ agents: [] })} />);
        expect(container).toBeEmptyDOMElement();
    });

    it('is one line until opened, then lists agents by package', () => {
        render(<PackagedAgents model={model({ active: 'plain' })} />);
        expect(screen.getByTestId('bench-agents-toggle')).toHaveTextContent('Agents · 3 installed · plain is on in this chat');
        expect(screen.queryByTestId('bench-agent-helper')).toBeNull();
        fireEvent.click(screen.getByTestId('bench-agents-toggle'));
        expect(screen.getByTestId('bench-agents')).toHaveTextContent('PkgA');
        expect(screen.getByTestId('bench-agent-guarded')).toHaveTextContent('not available yet: its restrictions are not enforced yet');
        expect(screen.getByTestId('bench-agent-use-guarded')).toBeDisabled();
        expect(screen.getByTestId('bench-agent-use-plain')).toBeDisabled();       // already on
        expect(screen.getByTestId('bench-agent-use-plain')).toHaveTextContent('on in this chat');
    });

    it('use reports the name; without a saved conversation it is disabled', () => {
        const m = model();
        const { unmount } = render(<PackagedAgents model={m} />);
        fireEvent.click(screen.getByTestId('bench-agents-toggle'));
        fireEvent.click(screen.getByTestId('bench-agent-use-helper'));
        expect(m.onUse).toHaveBeenCalledWith('helper');
        unmount();
        render(<PackagedAgents model={model({ canUse: false })} />);
        fireEvent.click(screen.getByTestId('bench-agents-toggle'));
        expect(screen.getByTestId('bench-agent-use-helper')).toBeDisabled();
    });

    it('inspect loads and shows the import record', async () => {
        render(<PackagedAgents model={model()} />);
        fireEvent.click(screen.getByTestId('bench-agents-toggle'));
        fireEvent.click(screen.getByTestId('bench-agent-inspect-helper'));
        expect(await screen.findByTestId('agent-row-Prompt')).toHaveTextContent('applied as written');
        expect(mockFetchImport).toHaveBeenCalledWith('P', 'helper');
        fireEvent.click(screen.getByTestId('bench-agent-inspect-helper'));
        expect(screen.queryByTestId('bench-agent-import-helper')).toBeNull();
    });
});

describe('wiring', () => {
    const read = (...p: string[]) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
    it('the transcript draws the seam for an agentNotice message', () => {
        const src = read('Conversation.tsx');
        expect(src).toMatch(/msg\.role === 'system' && msg\.agentNotice \?/);
        expect(src).toContain('<AgentSeam notice={msg.agentNotice} />');
    });
    it('the composer authors a muted system note and passes the conversation pin', () => {
        const src = read('SendChatContainer.tsx');
        const at = src.indexOf('agentNotice: { kind, name }');
        expect(at).toBeGreaterThan(-1);
        expect(src.slice(at - 300, at)).toContain('muted: true');
        expect(src).toContain("pinnedModel={activeModelPin?.scope === 'conversation' ? activeModelPin.model : null}");
        expect(src).toContain('onChange={noteAgentChange}');
    });
    it('the Bench hands "use in this chat" to the composer and lists agents in the panel', () => {
        expect(read('Bench', 'BenchModal.tsx')).toContain("dispatchAgentCommand(conversationId, { kind: 'on', name })");
        expect(read('Bench', 'BenchPanel.tsx')).toContain('<PackagedAgents model={agents} />');
    });
});
