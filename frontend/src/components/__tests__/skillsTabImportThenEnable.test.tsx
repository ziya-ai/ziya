/**
 * @jest-environment jsdom
 *
 * Reported 2026-11-17: a skill imported from SKILL.md in the Skills tab got
 * a card with an Enable button that did nothing, while older skills enabled
 * normally.
 *
 * The card comes from ProjectContext's skill list, which createSkill updates
 * at once.  The Enable button writes through the tab's useBench() view, which
 * was fetched before the skill existed and so had no `skill:<name>` item;
 * setLevel returns when it finds none.  Nothing told the bench instances to
 * re-fetch, so the button stayed dead until a reload.
 *
 * ProjectContext now announces BENCH_CHANGED_EVENT after it creates, edits or
 * deletes a skill.  This drives the real ContextsTab and the real useBench
 * with the HTTP layer and the providers mocked.
 */
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import * as fs from 'fs';
import * as path from 'path';

jest.mock('../../apis/benchApi', () => ({ fetchBench: jest.fn(), placeBench: jest.fn() }));
jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));
jest.mock('../../context/ActiveChatContext', () => ({ useActiveChatOptional: () => undefined }));
jest.mock('../../context/FolderContext', () => ({ useFolderContext: () => ({ checkedKeys: [] }) }));

const mkSkill = (name: string) => ({
    id: `id-${name}`, name, description: `About ${name}`, prompt: 'Do it.',
    color: '#000', tokenCount: 10, isBuiltIn: false, createdAt: 0, lastUsedAt: 0,
    source: 'custom', visibility: 'user_selectable',
});

// Stands in for ProjectContext: the list the cards render from, and a
// createSkill that appends to it.  `mockAnnounce` is what the fix adds.
let mockSkills: any[] = [];
let mockAnnounce = true;
const mockCreateSkill = jest.fn();
// Installed per test: CRA's jest config resets mock implementations.
const createSkillImpl = async (data: any) => {
    const s = { ...mkSkill(data.name), ...data };
    mockSkills = [...mockSkills, s];
    if (mockAnnounce) {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        require('../../hooks/useBench').announceBenchChanged('P');
    }
    return s;
};
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({
        contexts: [], skills: mockSkills, activeContextIds: [], activeSkillIds: [], additionalFiles: [],
        addContextToLens: jest.fn(), removeContextFromLens: jest.fn(), removeSkillFromLens: jest.fn(),
        createContext: jest.fn(), createSkill: mockCreateSkill, updateSkill: jest.fn(), deleteSkill: jest.fn(),
        tokenInfo: null, isCalculatingTokens: false,
        currentProject: { id: 'P', name: 'p', path: '/p' }, skillsProjectId: 'P',
    }),
}));

import { fetchBench, placeBench } from '../../apis/benchApi';
import { ContextsTab } from '../ContextsTab';

const item = (name: string, state: 'always' | null = null) => ({
    key: `skill:${name}`, kind: 'skill', tier: 'placeable', name,
    provenance: 'user', tokens: 10, health: {},
    placements: { conversation: null, project: state, user: null },
    effective: state ?? 'off', origin: state ? 'project' : 'default', default: 'off',
});
const view = (items: any[]) => ({
    items, dropped: [], lenses: ['project', 'user'],
    deltas: { conversation: 0, project: 0, user: 0 },
    weight: { always: 0, ondemand: 0, environment: 0 }, conversation_id: null,
});

const SKILL_MD = '---\nname: imported-skill\ndescription: Came from a file\n---\n\nFollow these steps.\n';

async function importSkillMd(container: HTMLElement) {
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    await act(async () => {
        fireEvent.change(input, { target: { files: [{ name: 'SKILL.md', text: async () => SKILL_MD }] } });
    });
    await waitFor(() => expect(mockCreateSkill).toHaveBeenCalledTimes(1));
}

/** The Enable button on the card of the skill called `name`. */
function enableButtonFor(name: string): HTMLButtonElement {
    const buttons = screen.getAllByText('Enable').map(el => el.closest('button')!);
    const hit = buttons.find(b => b.parentElement!.textContent!.includes(name));
    if (!hit) throw new Error(`no Enable button on a card naming ${name}`);
    return hit as HTMLButtonElement;
}

beforeEach(() => {
    jest.clearAllMocks();
    localStorage.clear();
    mockSkills = [mkSkill('older-skill')];
    mockAnnounce = true;
    mockCreateSkill.mockImplementation(createSkillImpl);
});

it('a skill imported from SKILL.md can be enabled straight away', async () => {
    (fetchBench as jest.Mock).mockResolvedValue(view([item('older-skill')]));
    const { container } = render(<ContextsTab />);
    await waitFor(() => expect(enableButtonFor('older-skill')).toBeEnabled());
    const fetchesBefore = (fetchBench as jest.Mock).mock.calls.length;

    // From now on the server's bench knows the new skill.
    (fetchBench as jest.Mock).mockResolvedValue(view([item('older-skill'), item('imported-skill')]));
    await importSkillMd(container);
    expect(mockCreateSkill.mock.calls[0][0]).toMatchObject({ name: 'imported-skill', description: 'Came from a file' });

    // The tab's bench heard the announcement and re-fetched.
    await waitFor(() => expect((fetchBench as jest.Mock).mock.calls.length).toBe(fetchesBefore + 1));
    await screen.findByText('imported-skill');

    (placeBench as jest.Mock).mockResolvedValue(
        view([item('older-skill'), item('imported-skill', 'always')]));
    await act(async () => { fireEvent.click(enableButtonFor('imported-skill')); });

    expect(placeBench).toHaveBeenCalledWith('P', 'skill:imported-skill', 'project', 'always', null);
    await waitFor(() => expect(screen.getByText('always on')).toBeInTheDocument());
    // The older skill is still off and still offers Enable.
    expect(enableButtonFor('older-skill')).toBeInTheDocument();
});

it('without the announcement the new card\'s Enable does nothing (the reported bug)', async () => {
    mockAnnounce = false;
    (fetchBench as jest.Mock).mockResolvedValue(view([item('older-skill')]));
    const { container, rerender } = render(<ContextsTab />);
    await waitFor(() => expect(enableButtonFor('older-skill')).toBeEnabled());
    const fetchesBefore = (fetchBench as jest.Mock).mock.calls.length;

    (fetchBench as jest.Mock).mockResolvedValue(view([item('older-skill'), item('imported-skill')]));
    await importSkillMd(container);
    rerender(<ContextsTab />);                       // ProjectContext's list changed
    await screen.findByText('imported-skill');
    expect((fetchBench as jest.Mock).mock.calls.length).toBe(fetchesBefore);

    await act(async () => { fireEvent.click(enableButtonFor('imported-skill')); });
    expect(placeBench).not.toHaveBeenCalled();

    // Positive control: the same click on the older skill does place.
    (placeBench as jest.Mock).mockResolvedValue(view([item('older-skill', 'always')]));
    await act(async () => { fireEvent.click(enableButtonFor('older-skill')); });
    expect(placeBench).toHaveBeenCalledWith('P', 'skill:older-skill', 'project', 'always', null);
});

describe('ProjectContext announces its own skill writes to the bench', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '../../context/ProjectContext.tsx'), 'utf8');
    const body = (fn: string): string => {
        const start = src.indexOf(`const ${fn} = useCallback(`);
        expect(start).toBeGreaterThan(-1);
        const end = src.indexOf('}, [currentProject]);', start);
        expect(end).toBeGreaterThan(start);
        return src.slice(start, end);
    };

    it.each(['createSkillFn', 'updateSkillFn', 'deleteSkillFn'])('%s', fn => {
        const text = body(fn);
        expect(text).toContain(`skillApi.${fn.replace('Fn', '')}(`);
        expect(text).toContain('announceBenchChanged(currentProject.id)');
    });

    it('imports the announcer from the bench hook', () => {
        expect(src).toMatch(/import\s*\{[^}]*\bannounceBenchChanged\b[^}]*\}\s*from\s*'..\/hooks\/useBench'/);
    });
});
