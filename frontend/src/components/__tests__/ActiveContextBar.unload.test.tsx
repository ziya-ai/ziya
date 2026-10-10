/**
 * @jest-environment jsdom
 *
 * The X on a skill chip must unload the skill from the bench.  FAILS against
 * the pre-fix bar: the X called only removeSkillFromLens (the legacy list,
 * emptied by migration), so the bench's always-set -- which the chip renders
 * from -- never changed and the chip stayed.
 *
 * Names referenced inside jest.mock factories carry the ``mock`` prefix;
 * babel-plugin-jest-hoist rejects any other out-of-scope reference.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

const mockConcise = { id: 'p1', name: 'Concise', visibility: 'user_selectable', color: '#222', source: 'builtin', prompt: '' };
const mockRemoveSkillFromLens = jest.fn();
const mockPlace = jest.fn((..._args: any[]) => Promise.resolve());
const mockBench: any = { ready: true, alwaysItems: [], pickedUpItems: [], place: mockPlace };
const mockLegacy = { activeSkillIds: [] as string[] };

jest.mock('../../hooks/useBench', () => ({ useBench: () => mockBench }));
jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));
jest.mock('../../context/ActiveChatContext', () => ({ useActiveChatOptional: () => null }));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({
        contexts: [], skills: [mockConcise],
        activeContextIds: [], activeSkillIds: mockLegacy.activeSkillIds,
        additionalFiles: [],
        removeContextFromLens: jest.fn(), removeSkillFromLens: mockRemoveSkillFromLens,
        tokenInfo: null, isCalculatingTokens: false,
        currentProject: { id: 'P' }, skillsProjectId: 'P',
    }),
}));

import { ActiveContextBar, unloadPlacement } from '../ActiveContextBar';

const item = (placements: any, origin: string, def = 'off') => ({
    key: 'skill:Concise', kind: 'skill', tier: 'placeable', name: 'Concise', effective: 'always',
    placements: { conversation: null, project: null, user: null, ...placements }, origin, default: def,
    tokens: 0, provenance: '', health: {}, atLens: {} as any,
});

describe('skill chip X unloads via the bench', () => {
    beforeEach(() => {
        mockPlace.mockClear();
        mockRemoveSkillFromLens.mockClear();
        mockBench.ready = true;
        mockBench.alwaysItems = [];
        mockLegacy.activeSkillIds = [];
    });

    it('clicking X writes a bench placement at the origin layer', () => {
        mockBench.alwaysItems = [item({ project: 'always' }, 'project')];
        render(<ActiveContextBar />);
        expect(screen.getByText('Concise')).toBeInTheDocument();   // the path ran
        fireEvent.click(screen.getByTestId('skill-unload-Concise'));
        expect(mockPlace).toHaveBeenCalledWith('skill:Concise', null, 'project');
        expect(mockRemoveSkillFromLens).toHaveBeenCalledWith('p1');
    });

    it('a conversation-layer pin is cleared at the conversation layer', () => {
        mockBench.alwaysItems = [item({ conversation: 'always' }, 'conversation')];
        render(<ActiveContextBar />);
        fireEvent.click(screen.getByTestId('skill-unload-Concise'));
        expect(mockPlace).toHaveBeenCalledWith('skill:Concise', null, 'conversation');
    });

    it('before the bench answers, X clears the legacy list and writes no placement', () => {
        mockBench.ready = false;
        mockLegacy.activeSkillIds = ['p1'];   // user_selectable in list == active
        render(<ActiveContextBar />);
        expect(screen.getByText('Concise')).toBeInTheDocument();   // the path ran
        fireEvent.click(screen.getByTestId('skill-unload-Concise'));
        expect(mockRemoveSkillFromLens).toHaveBeenCalledWith('p1');
        expect(mockPlace).not.toHaveBeenCalled();
    });
});

describe('unloadPlacement', () => {
    it('clears the origin pin when nothing below pins always', () => {
        expect(unloadPlacement(item({ conversation: 'always' }, 'conversation') as any))
            .toEqual({ layer: 'conversation', state: null });
    });
    it('writes the default at the origin when a lower layer also pins always', () => {
        expect(unloadPlacement(item({ project: 'always', user: 'always' }, 'project', 'ondemand') as any))
            .toEqual({ layer: 'project', state: 'ondemand' });
    });
    it('writes off when the default itself is always', () => {
        expect(unloadPlacement(item({ user: 'always' }, 'user', 'always') as any))
            .toEqual({ layer: 'user', state: 'off' });
    });
    it('does nothing when no writable layer is responsible', () => {
        expect(unloadPlacement(item({}, 'default', 'always') as any)).toBeNull();
    });
});
