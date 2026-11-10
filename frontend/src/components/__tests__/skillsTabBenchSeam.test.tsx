/**
 * @jest-environment jsdom
 *
 * The seam the write-through could not cross: enabling a skill in the
 * sidebar Skills tab must show its chip in the side panel at once.
 *
 * The tab and the chip bar each mount their own useBench(); a placement
 * through the tab's instance announces BENCH_CHANGED_EVENT and the bar's
 * instance re-fetches.  This drives the real ContextsTab, the real
 * ActiveContextBar and the real useBench with only the HTTP layer and the
 * providers mocked, and asserts on the outermost surface: the chip.
 *
 * Also pins that the old path is gone: ProjectContext no longer writes
 * placements behind the hook's back, and the one-off write-through helper
 * added for the chip bug (benchWriteThrough.ts) has been removed with it.
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

const fleet = {
    id: 'proj-fleet', name: 'fleet-metrics', description: 'Query fleet telemetry', prompt: '',
    color: '#000', tokenCount: 1872, isBuiltIn: false, createdAt: 0, lastUsedAt: 0,
    source: 'project', visibility: 'user_selectable',
};
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({
        contexts: [], skills: [fleet], activeContextIds: [], activeSkillIds: [], additionalFiles: [],
        addContextToLens: jest.fn(), removeContextFromLens: jest.fn(), removeSkillFromLens: jest.fn(),
        createContext: jest.fn(), createSkill: jest.fn(), updateSkill: jest.fn(), deleteSkill: jest.fn(),
        tokenInfo: null, isCalculatingTokens: false,
        currentProject: { id: 'P', name: 'p', path: '/p' }, skillsProjectId: 'P',
    }),
}));

import { fetchBench, placeBench } from '../../apis/benchApi';
import { ContextsTab } from '../ContextsTab';
import { ActiveContextBar } from '../ActiveContextBar';

const view = (state: 'always' | null) => ({
    items: [{ key: 'skill:fleet-metrics', kind: 'skill', tier: 'placeable', name: 'fleet-metrics',
              provenance: 'project', tokens: 1872, health: {},
              placements: { conversation: null, project: state, user: null },
              effective: state ?? 'off', origin: state ? 'project' : 'default', default: 'off' }],
    dropped: [], lenses: ['project', 'user'],
    deltas: { conversation: 0, project: state ? 1 : 0, user: 0 },
    weight: { always: state ? 1872 : 0, ondemand: 0, environment: 0 }, conversation_id: null,
});

beforeEach(() => { jest.clearAllMocks(); localStorage.clear(); });

it('enabling a local skill in the Skills tab puts its chip in the side panel at once', async () => {
    (fetchBench as jest.Mock).mockResolvedValue(view(null));
    render(<><ActiveContextBar /><ContextsTab /></>);
    // By text, not role: role queries compute the accessible name through
    // jsdom's getComputedStyle, which trips over antd's injected stylesheet
    // for the tab's search Input (nwsapi "contains on Node" TypeError).
    const enable = (await screen.findByText('Enable')).closest('button')!;
    await waitFor(() => expect(enable).toBeEnabled());
    expect(screen.queryByLabelText('Unload fleet-metrics')).toBeNull();
    const before = (fetchBench as jest.Mock).mock.calls.length;   // one per instance

    (placeBench as jest.Mock).mockResolvedValue(view('always'));
    (fetchBench as jest.Mock).mockResolvedValue(view('always'));
    await act(async () => { fireEvent.click(enable); });

    expect(placeBench).toHaveBeenCalledWith('P', 'skill:fleet-metrics', 'project', 'always', null);
    // The chip bar's instance heard the announcement and re-fetched.
    await waitFor(() => expect(screen.getByLabelText('Unload fleet-metrics')).toBeInTheDocument());
    expect((fetchBench as jest.Mock).mock.calls.length).toBe(before + 1);
    // And the tab itself now reads the placement back from the bench.
    expect(screen.queryByText('Enable')).toBeNull();
    expect(screen.getByText('always on')).toBeInTheDocument();
});

it('no placement is written behind the hook any more', () => {
    const src = path.join(__dirname, '..', '..');
    const pc = fs.readFileSync(path.join(src, 'context', 'ProjectContext.tsx'), 'utf8');
    expect(pc).not.toMatch(/placeBench|isBenchLive|benchWriteThrough|announceBenchChanged|_writeThroughPrev/);
    expect(fs.existsSync(path.join(src, 'utils', 'benchWriteThrough.ts'))).toBe(false);
    expect(fs.existsSync(path.join(src, 'hooks', '__tests__', 'useBenchWriteThrough.test.tsx'))).toBe(false);
    // Positive control: the tab is where the bench is now mounted and written.
    const tab = fs.readFileSync(path.join(src, 'components', 'ContextsTab.tsx'), 'utf8');
    expect(tab).toMatch(/useBench\(/);
    expect(tab).toMatch(/bench\.place\(/);
});
