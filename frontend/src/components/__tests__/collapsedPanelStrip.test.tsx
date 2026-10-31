/**
 * @jest-environment jsdom
 *
 * CollapsedPanelStrip (design/visual-refresh.md, stage 3, option A).  Seam
 * under test: what TokenCountDisplay / FolderTree publish is what the strip
 * shows — the same total the footer last computed, the same model label,
 * and the skill pills capped at STRIP_SKILL_CAP with a +N overflow.  Red
 * until the stage-3 diffs (panelStatus.ts, CollapsedPanelStrip.tsx, the
 * useActiveSkillRows extraction) are applied.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';

jest.mock('../ActiveContextBar', () => ({
    useActiveSkillRows: () => ({
        activeSkills: [
            { skill: { id: 's1', name: 'Tests for Everything', color: '#7c3aed' } },
            { skill: { id: 's2', name: 'Continuous Documentation', color: '#0d9488' } },
            { skill: { id: 's3', name: 'kuiper-rpp-notes', color: '#c58a1a' } },
        ],
        pickedUpSkills: [{ id: 'p1', name: 'statistical_charts', color: '#a21caf' }],
        unloadSkill: () => {},
    }),
}));
jest.mock('../ModelConfigButton', () => ({ ModelConfigButton: () => <span data-testid="model-config" /> }));
jest.mock('../../context/ActiveChatContext', () => ({ useActiveChatOptional: () => ({ currentConversationId: 'c1' }) }));
jest.mock('../../utils/handoffOpen', () => ({ dispatchHandoffOpen: jest.fn() }));

import { CollapsedPanelStrip, STRIP_SKILL_CAP } from '../CollapsedPanelStrip';
import { _resetPanelStatusForTests, publishPanelStatus } from '../../utils/panelStatus';
import { dispatchHandoffOpen } from '../../utils/handoffOpen';

beforeEach(() => _resetPanelStatusForTests());

it('renders nothing numeric until a producer has published', () => {
    render(<CollapsedPanelStrip />);
    expect(screen.queryByTestId('strip-tokens')).toBeNull();
    expect(screen.queryByTestId('strip-model')).toBeNull();
});

it('shows the published model label and token total exactly', () => {
    publishPanelStatus({
        tokens: { files: 31200, chat: 9400, ast: 1100, tools: 400, total: 42100, limit: 200000, cacheWorking: true },
        model: { id: 'anthropic.claude-sonnet-4.5', display: 'claude-sonnet-4.5', pinned: true },
    });
    render(<CollapsedPanelStrip />);
    expect(screen.getByTestId('strip-model').textContent).toContain('claude-sonnet-4.5');
    expect(screen.getByTestId('strip-token-total').textContent).toBe('42.1k / 200k');
    expect(screen.getByText('● cache')).toBeTruthy();
    expect(screen.getByTestId('model-config')).toBeTruthy();
});

it('caps skill pills and reports the remainder as +N', () => {
    render(<CollapsedPanelStrip />);
    expect(screen.getByText('Tests for Everything')).toBeTruthy();
    expect(screen.getByText('Continuous Documentation')).toBeTruthy();
    // 3 always-on + 1 picked-up = 4 pills; cap leaves 2 in the overflow chip.
    expect(screen.getByTestId('strip-skill-overflow').textContent).toBe(`+${4 - STRIP_SKILL_CAP}`);
    expect(screen.queryByText('kuiper-rpp-notes')).toBeNull();
});

it('hand off dispatches for the current conversation', () => {
    publishPanelStatus({ tokens: { files: 0, chat: 0, ast: 0, tools: 0, total: 0, limit: 200000, cacheWorking: null } });
    render(<CollapsedPanelStrip />);
    screen.getByText('⇢ hand off').click();
    expect(dispatchHandoffOpen).toHaveBeenCalledWith('c1');
});
