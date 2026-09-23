/**
 * ActiveContextBar shows the bench's always-set, not legacy list membership.
 * FAILS against the pre-slice-4 bar: it rendered every id in activeSkillIds,
 * including a model_discoverable skill whose presence there means SUPPRESSED.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';

const disc = { id: 'd1', name: 'Code Review', visibility: 'model_discoverable', color: '#111', source: 'builtin', prompt: '' };
const pick = { id: 'p1', name: 'Concise', visibility: 'user_selectable', color: '#222', source: 'builtin', prompt: '' };

const benchMock = { ready: true, alwaysItems: [] as any[] };
jest.mock('../../hooks/useBench', () => ({ useBench: () => benchMock }));
jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({
        contexts: [], skills: [disc, pick],
        activeContextIds: [],
        activeSkillIds: ['d1'],            // legacy: discoverable in list == suppressed
        additionalFiles: [],
        removeContextFromLens: jest.fn(), removeSkillFromLens: jest.fn(),
        tokenInfo: null, isCalculatingTokens: false,
        currentProject: { id: 'P' }, skillsProjectId: 'P',
    }),
}));

import { ActiveContextBar } from '../ActiveContextBar';

const skillItem = (name: string, effective: string) => ({
    key: `skill:${name}`, kind: 'skill', tier: 'placeable', name, effective,
    placements: { conversation: null, project: null, user: null }, origin: 'default', default: 'off',
});

describe('ActiveContextBar reads the bench', () => {
    it('renders only effective === always; a suppressed discoverable skill is absent', () => {
        benchMock.ready = true;
        benchMock.alwaysItems = [skillItem('Concise', 'always')];
        render(<ActiveContextBar />);
        expect(screen.getByText('Concise')).toBeInTheDocument();
        expect(screen.queryByText('Code Review')).toBeNull();
    });
    it('mcp items in the always-set are not rendered as skill pills', () => {
        benchMock.alwaysItems = [{ ...skillItem('jira', 'always'), kind: 'mcp' }];
        render(<ActiveContextBar />);
        expect(screen.queryByText('jira')).toBeNull();
    });
    it('before the bench answers, the legacy list is read through its real semantics', () => {
        benchMock.ready = false;
        render(<ActiveContextBar />);
        // d1 is discoverable => suppressed, not active; nothing to show.
        expect(screen.queryByText('Code Review')).toBeNull();
    });
});
