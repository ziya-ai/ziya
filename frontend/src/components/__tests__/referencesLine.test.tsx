/**
 * @jest-environment jsdom
 *
 * 📎 references line (design/conversation-references.md, slice 2).  Seams
 * pinned, outermost surface first:
 *   - ActiveContextBar mounts the line, in BOTH branches (a conversation
 *     with references but no contexts/skills still shows it);
 *   - the line is absent with no references, present with the server's
 *     counts and dot grammar (solid pinned, dashed picked-up);
 *   - the popover lists pinned items, folds picked-up ones, and 📌 / ✕ hit
 *     the promote / delete routes, after which the line re-fetches;
 *   - a references_updated event re-fetches; the "new" marker clears when
 *     the popover is opened;
 *   - the collapsed strip shows just "📎 N".
 * Red until the slice-2 diffs are applied.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));
const benchMock = { ready: true, alwaysItems: [] as any[], pickedUpItems: [] as any[] };
jest.mock('../../hooks/useBench', () => ({ useBench: () => benchMock }));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({
        contexts: [], skills: [], activeContextIds: [], activeSkillIds: [], additionalFiles: [],
        removeContextFromLens: jest.fn(), removeSkillFromLens: jest.fn(),
        tokenInfo: null, isCalculatingTokens: false,
        currentProject: { id: 'P' }, skillsProjectId: 'P',
    }),
    useProjectOptional: () => ({ currentProject: { id: 'P' } }),
}));
jest.mock('../../context/ActiveChatContext', () => ({ useActiveChatOptional: () => ({ currentConversationId: 'c1' }) }));
jest.mock('../ModelConfigButton', () => ({ ModelConfigButton: () => <span data-testid="model-config" /> }));
jest.mock('../../utils/handoffOpen', () => ({ dispatchHandoffOpen: jest.fn() }));

import { ActiveContextBar } from '../ActiveContextBar';
import { CollapsedPanelStrip } from '../CollapsedPanelStrip';
import { announceReferencesChanged, REFERENCES_CHANGED_EVENT } from '../../apis/referencesApi';

const BASE = '/api/v1/projects/P/chats/c1/references';

const ref = (over: Partial<any> = {}) => ({
    id: 'ref_a', kind: 'url', locator: 'https://example.com/spec', title: null, provenance: 'pinned',
    added_turn: 1, uses: [], useful: false, faded: false, ...over,
});

const view = (references: any[]) => ({
    turn: 5,
    summary: {
        pinned: references.filter(r => r.provenance === 'pinned').length,
        picked_up: references.filter(r => r.provenance !== 'pinned').length,
        useful: references.filter(r => r.provenance !== 'pinned' && r.useful).length,
        faded: references.filter(r => r.faded).length,
    },
    references,
});

let served: any;
const calls: Array<{ url: string; method: string }> = [];
beforeEach(() => {
    calls.length = 0;
    localStorage.clear();
    (global as any).fetch = jest.fn((url: string, init?: RequestInit) => {
        calls.push({ url, method: init?.method || 'GET' });
        const body = init?.method === 'DELETE' ? { ok: true }
            : init?.method === 'POST' ? ref({ provenance: 'pinned' })
            : served;
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    });
});

const line = () => screen.findByTestId('references-line');

describe('ActiveContextBar mounts the references line', () => {
    it('shows nothing when the conversation has no references', async () => {
        served = view([]);
        render(<ActiveContextBar />);
        await waitFor(() => expect(calls.some(c => c.url === BASE)).toBe(true));
        expect(screen.getByText('No context selected')).toBeInTheDocument();
        expect(screen.queryByTestId('references-line')).toBeNull();
    });

    it('shows the count and dot grammar even with no contexts or skills', async () => {
        served = view([
            ref(),
            ref({ id: 'ref_b', kind: 'file', locator: 'src/main.py', provenance: 'picked_up', useful: true,
                  uses: [{ kind: 'read', turn: 2 }, { kind: 'cited', turn: 3 }] }),
            ref({ id: 'ref_c', kind: 'chat', locator: 'ziya://chat/abc', provenance: 'picked_up' }),
            ref({ id: 'ref_d', kind: 'url', locator: 'https://example.com/old', provenance: 'picked_up', faded: true }),
        ]);
        render(<ActiveContextBar />);
        const el = await line();
        expect(el).toHaveTextContent('4 references · 3 picked up');
        const dots = screen.getAllByTestId('reference-dot');
        expect(dots).toHaveLength(3);                       // faded: counted, not drawn
        expect(dots[0]).toHaveStyle({ background: '#0d9488' });          // pinned: solid accent
        expect(dots[1].style.border).toMatch(/dashed #0d9488/);           // picked up, useful
        expect(dots[2].style.border).toMatch(/dashed #9ca3af/);           // picked up, not useful
        expect(screen.getByTestId('references-new')).toBeInTheDocument();  // never opened before
    });
});

describe('popover', () => {
    const data = [
        ref({ title: 'The spec', uses: [{ kind: 'cited', turn: 2 }, { kind: 'cited', turn: 4 }] }),
        ref({ id: 'ref_b', kind: 'file', locator: 'src/main.py', provenance: 'picked_up', useful: true,
              uses: [{ kind: 'read', turn: 2 }, { kind: 'cited', turn: 3 }] }),
        ref({ id: 'ref_c', kind: 'url', locator: 'https://example.com/unused', provenance: 'picked_up',
              uses: [{ kind: 'read', turn: 2 }] }),
    ];

    it('lists pinned items, folds picked-up ones, and clears the new marker', async () => {
        served = view(data);
        render(<ActiveContextBar />);
        fireEvent.click(await line());
        const pop = await screen.findByTestId('references-popover');
        expect(pop).toHaveTextContent('The spec');
        expect(pop).toHaveTextContent('×2');
        expect(pop).not.toHaveTextContent('src/main.py');     // folded
        expect(screen.getByTestId('references-picked-toggle')).toHaveTextContent('2 picked up by tools · 1 looks useful');
        fireEvent.click(screen.getByTestId('references-picked-toggle'));
        expect(pop).toHaveTextContent('src/main.py');
        expect(pop).toHaveTextContent('example.com/unused');
        expect(pop).toHaveTextContent('read once');
        await waitFor(() => expect(screen.queryByTestId('references-new')).toBeNull());
        expect(localStorage.getItem('ZIYA_REFS_SEEN:c1')).toBe('2');
    });

    it('📌 promotes through the API and ✕ removes, each followed by a re-fetch', async () => {
        served = view(data);
        render(<ActiveContextBar />);
        fireEvent.click(await line());
        fireEvent.click(await screen.findByTestId('references-picked-toggle'));
        const before = calls.length;
        fireEvent.click(screen.getByRole('button', { name: 'Pin src/main.py' }));
        await waitFor(() => expect(calls.some(c => c.url === `${BASE}/ref_b/pin` && c.method === 'POST')).toBe(true));
        await waitFor(() => expect(calls.filter(c => c.url === BASE && c.method === 'GET').length)
            .toBeGreaterThan(calls.slice(0, before).filter(c => c.url === BASE).length));
        fireEvent.click(screen.getByRole('button', { name: 'Remove The spec' }));
        await waitFor(() => expect(calls.some(c => c.url === `${BASE}/ref_a` && c.method === 'DELETE')).toBe(true));
    });
});

it('a references_updated event re-fetches this conversation only', async () => {
    served = view([ref()]);
    render(<ActiveContextBar />);
    await line();
    const gets = () => calls.filter(c => c.url === BASE && c.method === 'GET').length;
    const n = gets();
    act(() => announceReferencesChanged('other-conversation'));
    expect(gets()).toBe(n);
    act(() => { document.dispatchEvent(new CustomEvent(REFERENCES_CHANGED_EVENT, { detail: { conversationId: 'c1' } })); });
    await waitFor(() => expect(gets()).toBe(n + 1));
});

it('the collapsed strip shows only the count', async () => {
    served = view([ref(), ref({ id: 'ref_b', provenance: 'picked_up', locator: 'x.md', kind: 'file' })]);
    render(<CollapsedPanelStrip />);
    expect(await screen.findByTestId('strip-references')).toHaveTextContent('📎 2');
    expect(screen.queryByTestId('references-line')).toBeNull();
});
