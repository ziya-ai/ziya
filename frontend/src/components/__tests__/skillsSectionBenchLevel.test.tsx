/**
 * @jest-environment jsdom
 *
 * The Skills tab reads activation from the bench and writes placements to
 * it (design/capabilities-hub.md, slice 6 fold-in; stage 1).  Before this
 * it read ``activeSkillIds`` through a two-meaning encoding (a discoverable
 * skill in the list meant OFF, any other meant ON) and wrote that list,
 * which ProjectContext then translated into a placement through a raw API
 * call that nothing else observed.
 *
 * Pinned here:
 *   - the level a card shows is the bench item's ``effective`` state, for
 *     every visibility (an always-on discoverable skill reads "active", not
 *     "off" as the legacy encoding had it);
 *   - before the bench has answered, a card shows its default and its
 *     controls are disabled rather than writing against nothing;
 *   - a selector click writes the project layer: an explicit state, or a
 *     cleared pin when the chosen level is what the layer would inherit
 *     anyway (the lane rule, so the tab and the Board agree);
 *   - no legacy prop remains on SkillsSection.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import * as fs from 'fs';
import * as path from 'path';

jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));

import {
    SkillsSection, TAB_LAYER, benchKeyForSkill, tabLevel, tabPlacementFor,
} from '../SkillsSection';

const skill = (over: Partial<any> = {}) => ({
    id: 'proj-fleet', name: 'fleet-metrics', description: 'Query fleet telemetry', prompt: '',
    color: '#000', tokenCount: 1872, isBuiltIn: false, createdAt: 0, lastUsedAt: 0,
    source: 'project', visibility: 'user_selectable', ...over,
});

const item = (over: Partial<any> = {}) => ({
    key: 'skill:fleet-metrics', kind: 'skill', tier: 'placeable', name: 'fleet-metrics',
    provenance: 'project', tokens: 1872, health: {},
    placements: { conversation: null, project: null, user: null },
    effective: 'off', origin: 'default', default: 'off', atLens: {} as any, ...over,
});

const noop = async () => {};
const editorProps = {
    createSkill: jest.fn(), updateSkill: jest.fn(), deleteSkill: jest.fn(), searchQuery: '',
};

describe('pure level mapping', () => {
    it('shows the bench effective state for every visibility', () => {
        expect(TAB_LAYER).toBe('project');
        expect(benchKeyForSkill(skill())).toBe('skill:fleet-metrics');
        for (const visibility of ['user_selectable', 'model_discoverable', undefined]) {
            const s = skill({ visibility });
            expect(tabLevel(s, item({ effective: 'always' }))).toBe('active');
            expect(tabLevel(s, item({ effective: 'ondemand' }))).toBe('on-demand');
            expect(tabLevel(s, item({ effective: 'off' }))).toBe('off');
        }
    });

    it('falls back to the visibility default when the bench has no item yet', () => {
        expect(tabLevel(skill({ visibility: 'model_discoverable' }))).toBe('on-demand');
        expect(tabLevel(skill({ visibility: 'user_selectable' }))).toBe('off');
        expect(tabLevel(skill({ visibility: undefined }))).toBe('off');
    });

    it('writes an explicit state, or clears the pin when the level is inherited anyway', () => {
        const unpinned = item();                      // default off, nothing set
        expect(tabPlacementFor(unpinned, 'active')).toBe('always');
        expect(tabPlacementFor(unpinned, 'on-demand')).toBe('ondemand');
        expect(tabPlacementFor(unpinned, 'off')).toBeNull();          // == default
        const pinnedHere = item({ placements: { conversation: null, project: 'always', user: null },
                                  effective: 'always', origin: 'project' });
        expect(tabPlacementFor(pinnedHere, 'off')).toBeNull();        // back to the default
        expect(tabPlacementFor(pinnedHere, 'on-demand')).toBe('ondemand');
        const fromUser = item({ placements: { conversation: null, project: null, user: 'always' },
                                effective: 'always', origin: 'user' });
        expect(tabPlacementFor(fromUser, 'active')).toBeNull();       // user layer already gives it
        expect(tabPlacementFor(fromUser, 'off')).toBe('off');         // project pin overrides user
    });
});

describe('SkillsSection rendering the bench', () => {
    it('Enable on an off card writes always to the project layer', () => {
        const onPlace = jest.fn(noop);
        render(<SkillsSection {...editorProps} skills={[skill()]} benchItems={[item()]} benchReady onPlace={onPlace} />);
        fireEvent.click(screen.getByRole('button', { name: 'Enable' }));
        expect(onPlace).toHaveBeenCalledWith('skill:fleet-metrics', 'always');
    });

    it('a discoverable skill pinned always reads active, and Off from the selector clears the pin', () => {
        const onPlace = jest.fn(noop);
        const s = skill({ visibility: 'model_discoverable', name: 'Packet Diagrams' });
        const it_ = item({ key: 'skill:Packet Diagrams', name: 'Packet Diagrams', default: 'ondemand',
                           placements: { conversation: null, project: 'always', user: null },
                           effective: 'always', origin: 'project' });
        render(<SkillsSection {...editorProps} skills={[s]} benchItems={[it_]} benchReady onPlace={onPlace} />);
        expect(screen.getByText('always on')).toBeInTheDocument();     // not "off"
        expect(screen.queryByRole('button', { name: 'Enable' })).toBeNull();
        fireEvent.click(screen.getByText('Packet Diagrams'));           // expand
        fireEvent.click(screen.getByRole('button', { name: /^On-demand/ }));
        expect(onPlace).toHaveBeenCalledWith('skill:Packet Diagrams', null);   // == default: clear
        fireEvent.click(screen.getByRole('button', { name: /^Off/ }));
        expect(onPlace).toHaveBeenCalledWith('skill:Packet Diagrams', 'off');
    });

    it('names where an inherited placement comes from', () => {
        const fromUser = item({ placements: { conversation: null, project: null, user: 'always' },
                                effective: 'always', origin: 'user' });
        render(<SkillsSection {...editorProps} skills={[skill()]} benchItems={[fromUser]} benchReady onPlace={jest.fn(noop)} />);
        fireEvent.click(screen.getByText('fleet-metrics'));
        expect(screen.getByTestId('skill-effective-fleet-metrics')).toHaveTextContent(/your defaults/);
    });

    it('before the bench answers, controls are disabled and nothing is written', () => {
        const onPlace = jest.fn(noop);
        render(<SkillsSection {...editorProps} skills={[skill()]} benchItems={[]} benchReady={false} onPlace={onPlace} />);
        const enable = screen.getByRole('button', { name: 'Enable' });
        expect(enable).toBeDisabled();
        fireEvent.click(enable);
        expect(onPlace).not.toHaveBeenCalled();
    });
});

it('the legacy lens props are gone from SkillsSection', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'SkillsSection.tsx'), 'utf8');
    expect(src).not.toMatch(/activeSkillIds|addSkillToLens|removeSkillFromLens/);
});
