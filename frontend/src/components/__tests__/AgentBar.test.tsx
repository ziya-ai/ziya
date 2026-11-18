/**
 * @jest-environment jsdom
 *
 * The agent overlay's interface (design/agent-overlay.md): the /agent
 * command, the chip, the picker, the missing-servers confirmation and the
 * import record.  The API module is mocked; the command travels through
 * the real window event the composer dispatches.
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

import { AgentBar, agentsByPackage, refusalText } from '../AgentBar';
import { dispatchAgentCommand, parseAgentCommand } from '../../utils/agentCommand';

const mk = (name: string, pkg: string, over: any = {}) => ({
    name, description: `${name} does things`, package: pkg, also_in: [], model: 'opus',
    skills: 1, servers: 2, subagents: 0, runnable: true, blockers: [], ...over,
});
const AGENTS = [
    mk('scout', 'PkgB'),
    mk('helper', 'PkgA'),
    mk('guarded', 'PkgA', { runnable: false, blockers: ['its restrictions are not enforced yet'] }),
];
const row = (part: string, outcome = 'applied', items: string[] = []) =>
    ({ part, says: `${part} says`, does: `${part} does`, outcome, items });

let active: string | null;
beforeEach(() => {
    active = null;
    mockFetchAgents.mockReset().mockImplementation(async () => ({ agents: AGENTS, active }));
    mockFetchImport.mockReset().mockImplementation(async (_p: string, name: string) => ({ ...mk(name, 'PkgA'), rows: [row('Prompt')] }));
    mockSetActive.mockReset().mockImplementation(async (_p: string, _c: string, name: string | null) => {
        active = name;
        return { active: name, agent: null };
    });
});

async function mount(conversationId = 'C') {
    const view = render(<AgentBar projectId="P" conversationId={conversationId} />);
    await waitFor(() => expect(mockFetchAgents).toHaveBeenCalledWith('P', conversationId));
    await act(async () => {});
    return view;
}
const command = (text: string, conversationId = 'C') =>
    act(async () => { dispatchAgentCommand(conversationId, parseAgentCommand(text)!); });

describe('parseAgentCommand', () => {
    it('reads on, off and the bare command', () => {
        expect(parseAgentCommand('/agent helper')).toEqual({ kind: 'on', name: 'helper' });
        expect(parseAgentCommand('  /agent   OFF ')).toEqual({ kind: 'off' });
        expect(parseAgentCommand('/agent')).toEqual({ kind: 'pick' });
    });
    it('leaves ordinary messages alone', () => {
        expect(parseAgentCommand('/agents are great')).toBeNull();
        expect(parseAgentCommand('/agent is a word I use')).toBeNull();
        expect(parseAgentCommand('tell me about /agent')).toBeNull();
        expect(parseAgentCommand('/goal fix it')).toBeNull();
    });
});

describe('helpers', () => {
    it('groups agents by package, A-Z', () => {
        expect(agentsByPackage(AGENTS as any).map(g => [g.pkg, g.agents.map(a => a.name)]))
            .toEqual([['PkgA', ['helper', 'guarded']], ['PkgB', ['scout']]]);
    });
    it('explains a refusal', () => {
        expect(refusalText({ status: 409, blockers: ['x', 'y'] }, 'a')).toBe('a cannot be turned on yet: x; y.');
        expect(refusalText({ status: 404, message: 'conversation not found: C' }, 'a')).toMatch(/not been saved yet/);
        expect(refusalText({ status: 404, message: 'no such agent: a' }, 'a')).toBe('No agent named a is installed.');
    });
});

describe('AgentBar', () => {
    it('shows nothing when no agent is active', async () => {
        const { container } = await mount();
        expect(container).toBeEmptyDOMElement();
    });

    it('shows a chip for the active agent, and × turns it off', async () => {
        active = 'helper';
        await mount();
        expect(await screen.findByTestId('agent-chip')).toHaveTextContent('helper');
        fireEvent.click(screen.getByTestId('agent-leave'));
        await waitFor(() => expect(mockSetActive).toHaveBeenCalledWith('P', 'C', null));
        await waitFor(() => expect(screen.queryByTestId('agent-chip')).toBeNull());
    });

    it('/agent <name> turns it on; /agent off turns it off', async () => {
        await mount();
        await command('/agent helper');
        await waitFor(() => expect(mockSetActive).toHaveBeenCalledWith('P', 'C', 'helper'));
        expect(await screen.findByTestId('agent-chip')).toHaveTextContent('helper');
        await command('/agent off');
        await waitFor(() => expect(mockSetActive).toHaveBeenLastCalledWith('P', 'C', null));
        await waitFor(() => expect(screen.queryByTestId('agent-chip')).toBeNull());
    });

    it('ignores a command addressed to another conversation', async () => {
        await mount();
        await command('/agent helper', 'OTHER');
        await command('/agent');                        // this one is ours: proves the listener runs
        expect(await screen.findByTestId('agent-picker')).toBeInTheDocument();
        expect(mockSetActive).not.toHaveBeenCalled();
    });

    it('says why an agent cannot be turned on yet, and writes nothing', async () => {
        await mount();
        await command('/agent guarded');
        expect(await screen.findByTestId('agent-notice'))
            .toHaveTextContent('guarded cannot be turned on yet: its restrictions are not enforced yet.');
        expect(mockSetActive).not.toHaveBeenCalled();
    });

    it('passes an unknown name to the server and reports its answer', async () => {
        mockSetActive.mockRejectedValueOnce({ status: 404, message: 'no such agent: nobody', blockers: [] });
        await mount();
        await command('/agent nobody');
        expect(await screen.findByTestId('agent-notice')).toHaveTextContent('No agent named nobody is installed.');
        expect(screen.queryByTestId('agent-chip')).toBeNull();
    });

    it('names missing servers first: Cancel writes nothing, Start anyway turns it on', async () => {
        mockFetchImport.mockImplementation(async () =>
            ({ ...mk('helper', 'PkgA'), rows: [row('Prompt'), row('Servers', 'unavailable', ['nova', 'creds'])] }));
        await mount();
        await command('/agent helper');
        expect(await screen.findByTestId('agent-confirm'))
            .toHaveTextContent('helper needs 2 servers that are not installed here: nova, creds.');
        expect(mockSetActive).not.toHaveBeenCalled();
        fireEvent.click(screen.getByTestId('agent-confirm-no'));
        expect(screen.queryByTestId('agent-confirm')).toBeNull();
        expect(mockSetActive).not.toHaveBeenCalled();

        await command('/agent helper');
        fireEvent.click(await screen.findByTestId('agent-confirm-yes'));
        await waitFor(() => expect(mockSetActive).toHaveBeenCalledWith('P', 'C', 'helper'));
        expect(await screen.findByTestId('agent-chip')).toBeInTheDocument();
        expect(screen.queryByTestId('agent-confirm')).toBeNull();
    });

    it('/agent lists agents by package; an unavailable one cannot be picked', async () => {
        await mount();
        await command('/agent');
        const picker = await screen.findByTestId('agent-picker');
        expect(picker).toHaveTextContent('PkgA');
        expect(picker).toHaveTextContent('PkgB');
        expect(screen.getByTestId('agent-pick-guarded')).toBeDisabled();
        fireEvent.click(screen.getByTestId('agent-pick-scout'));
        await waitFor(() => expect(mockSetActive).toHaveBeenCalledWith('P', 'C', 'scout'));
        await waitFor(() => expect(screen.queryByTestId('agent-picker')).toBeNull());
        expect(await screen.findByTestId('agent-chip')).toHaveTextContent('scout');
    });

    it('the chip opens the import record', async () => {
        active = 'helper';
        mockFetchImport.mockImplementation(async () => ({
            ...mk('helper', 'PkgA'),
            rows: [row('Prompt'), row('Model', 'pending'), row('Servers', 'unavailable', ['nova'])],
        }));
        await mount();
        expect(screen.queryByTestId('agent-detail')).toBeNull();
        fireEvent.click(await screen.findByTestId('agent-chip-open'));
        expect(await screen.findByTestId('agent-row-Prompt')).toHaveTextContent('Prompt does');
        expect(screen.getByTestId('agent-row-Servers')).toHaveTextContent('Servers does: nova');
        expect(screen.getByTestId('agent-detail')).toHaveTextContent('PkgA');
        expect(mockFetchImport).toHaveBeenCalledWith('P', 'helper');
        fireEvent.click(screen.getByTestId('agent-detail-leave'));
        await waitFor(() => expect(mockSetActive).toHaveBeenCalledWith('P', 'C', null));
    });

    it('re-reads the agent when the conversation changes', async () => {
        active = 'helper';
        const { rerender } = await mount('C');
        expect(await screen.findByTestId('agent-chip')).toBeInTheDocument();
        active = null;
        rerender(<AgentBar projectId="P" conversationId="D" />);
        await waitFor(() => expect(mockFetchAgents).toHaveBeenCalledWith('P', 'D'));
        await waitFor(() => expect(screen.queryByTestId('agent-chip')).toBeNull());
    });
});

describe('composer wiring', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'SendChatContainer.tsx'), 'utf8');
    it('checks for /agent before the other slash commands, and announces it', () => {
        const agentAt = src.indexOf('parseAgentCommand(text)');
        expect(agentAt).toBeGreaterThan(-1);
        expect(agentAt).toBeLessThan(src.indexOf('parseSlashCommand(text)'));
        expect(src).toContain('dispatchAgentCommand(currentConversationId, agentCmd)');
    });
    it('mounts the bar for the open conversation', () => {
        expect(src).toMatch(/<AgentBar\s+projectId=\{currentProject\?\.id\}\s+conversationId=\{currentConversationId\}/);
    });
});
