/**
 * @jest-environment jsdom
 *
 * Always-on skill pills past the cap stack behind one "+N skills" chip,
 * and the whole skill row can fold to a one-line dot strip.
 *
 * ActiveContextBar sits at the top of EVERY sidebar tab (FolderTree.tsx
 * mounts it above the file tree, the skills tab, the chat list and the
 * backlog).  A user with sixteen on-hand skills saw six rows of pills on
 * each tab, pushing the chat list below the fold on a laptop screen.
 *
 * Three states, two per-project bits (readSkillBarPrefs):
 *   capped (default)  first CAP pills + "+N skills" chip + pickups + ▴
 *   all               every pill + "fewer" chip + pickups + ▴
 *   collapsed         one line: solid dot per skill, dashed ring per pickup
 * At or under the cap there is nothing to hide, so none of the controls
 * render and a stored "collapsed" preference is ignored.
 *
 * FAILS against the unstacked bar: it rendered all sixteen as pills.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

const mockProject = { id: 'P' };
const mockSkills = Array.from({ length: 16 }, (_, i) => ({
    id: `s${i}`, name: `Skill${i}`, visibility: 'user_selectable', color: '#222', source: 'builtin', prompt: '',
}));
const mockPickup = { id: 'pk', name: 'Packet Diagrams', visibility: 'model_discoverable', color: '#333', source: 'builtin', prompt: '' };
const mockBench: any = { ready: true, alwaysItems: [], pickedUpItems: [], place: jest.fn(() => Promise.resolve()) };

jest.mock('../../hooks/useBench', () => ({ useBench: () => mockBench }));
jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));
jest.mock('../../context/ActiveChatContext', () => ({ useActiveChatOptional: () => null }));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({
        contexts: [], skills: [...mockSkills, mockPickup],
        activeContextIds: [], activeSkillIds: [],
        additionalFiles: [],
        removeContextFromLens: jest.fn(), removeSkillFromLens: jest.fn(),
        tokenInfo: null, isCalculatingTokens: false,
        currentProject: mockProject, skillsProjectId: mockProject.id,
    }),
}));

import {
    ActiveContextBar, CONTEXT_BAR_SKILL_CAP, CONTEXT_BAR_DOT_CAP, readSkillBarPrefs, writeSkillBarPrefs,
} from '../ActiveContextBar';

const always = (name: string) => ({
    key: `skill:${name}`, kind: 'skill', tier: 'placeable', name, effective: 'always',
    placements: { conversation: null, project: 'always', user: null }, origin: 'project', default: 'off',
    tokens: 10, provenance: 'user', health: {}, atLens: {} as any,
});
const sixteen = () => mockSkills.map(s => always(s.name));
const pillNames = () => mockSkills.map(s => s.name).filter(n => screen.queryByText(n) !== null);
const unloadButtons = () => screen.queryAllByLabelText(/^Unload /);

beforeEach(() => {
    localStorage.clear();
    mockProject.id = 'P';
    mockBench.alwaysItems = [];
    mockBench.pickedUpItems = [];
});

describe('capped (default)', () => {
    it('sixteen always-on skills render as the cap plus one chip naming the rest', () => {
        mockBench.alwaysItems = sixteen();
        render(<ActiveContextBar />);
        expect(CONTEXT_BAR_SKILL_CAP).toBeLessThan(16);
        expect(pillNames()).toEqual(mockSkills.slice(0, CONTEXT_BAR_SKILL_CAP).map(s => s.name));
        const chip = screen.getByTestId('skill-stack-toggle');
        expect(chip).toHaveTextContent(`+${16 - CONTEXT_BAR_SKILL_CAP} skills`);
        expect(chip).toHaveAttribute('aria-expanded', 'false');
        for (const s of mockSkills.slice(CONTEXT_BAR_SKILL_CAP)) expect(chip.getAttribute('title')).toContain(s.name);
        // Each shown pill keeps its X; the collapse control is offered.
        expect(unloadButtons()).toHaveLength(CONTEXT_BAR_SKILL_CAP);
        expect(screen.getByTestId('skill-collapse')).toBeInTheDocument();
        expect(screen.queryByTestId('skill-line')).toBeNull();
    });

    it('a skill the model picked up this turn is never stacked', () => {
        mockBench.alwaysItems = sixteen();
        mockBench.pickedUpItems = [{ ...always('Packet Diagrams'), effective: 'ondemand' }];
        render(<ActiveContextBar />);
        expect(pillNames()).toHaveLength(CONTEXT_BAR_SKILL_CAP);
        expect(screen.getByTestId('skill-pickup')).toHaveTextContent('Packet Diagrams');
    });
});

describe('all', () => {
    it('clicking the chip unstacks everything (with X buttons) and is remembered for this project', () => {
        mockBench.alwaysItems = sixteen();
        render(<ActiveContextBar />);
        fireEvent.click(screen.getByTestId('skill-stack-toggle'));
        expect(pillNames()).toHaveLength(16);
        expect(unloadButtons()).toHaveLength(16);
        const chip = screen.getByTestId('skill-stack-toggle');
        expect(chip).toHaveAttribute('aria-expanded', 'true');
        expect(chip).toHaveTextContent('fewer');
        expect(readSkillBarPrefs('P')).toEqual({ collapsed: false, expanded: true });
        expect(readSkillBarPrefs('Q').expanded).toBe(false);   // another project is untouched
        fireEvent.click(chip);                                  // and it stacks back
        expect(pillNames()).toHaveLength(CONTEXT_BAR_SKILL_CAP);
        expect(readSkillBarPrefs('P').expanded).toBe(false);
    });
});

describe('collapsed', () => {
    it('▴ folds the row to one line: dots for always-on, dashed rings for pickups, no pills', () => {
        mockBench.alwaysItems = sixteen();
        mockBench.pickedUpItems = [{ ...always('Packet Diagrams'), effective: 'ondemand' }];
        render(<ActiveContextBar />);
        fireEvent.click(screen.getByTestId('skill-collapse'));
        const line = screen.getByTestId('skill-line');
        expect(line).toHaveTextContent('16 on hand · 1 ✦');
        expect(pillNames()).toEqual([]);
        expect(unloadButtons()).toHaveLength(0);
        expect(screen.queryByTestId('skill-stack-toggle')).toBeNull();
        expect(screen.queryByTestId('skill-pickup')).toBeNull();
        expect(screen.queryByTestId('skill-collapse')).toBeNull();
        // 16 > DOT_CAP: DOT_CAP solid dots then "+N"; pickup ring drawn after them.
        expect(CONTEXT_BAR_DOT_CAP).toBeLessThan(16);
        expect(screen.getAllByTestId('skill-dot')).toHaveLength(CONTEXT_BAR_DOT_CAP);
        expect(line).toHaveTextContent(`+${16 - CONTEXT_BAR_DOT_CAP}`);
        const ring = screen.getByTestId('skill-dot-pickup');
        expect(ring).toHaveAttribute('title', '✦ Packet Diagrams');
        expect(ring.style.border).toContain('dashed');
        // Names stay discoverable on the line's tooltip.
        expect(line.getAttribute('title')).toContain('Skill15');
        expect(line.getAttribute('title')).toContain('✦ Packet Diagrams');
        expect(readSkillBarPrefs('P').collapsed).toBe(true);
    });

    it('reopening returns to whichever of capped/all the user was in', () => {
        mockBench.alwaysItems = sixteen();
        render(<ActiveContextBar />);
        fireEvent.click(screen.getByTestId('skill-stack-toggle'));   // all
        fireEvent.click(screen.getByTestId('skill-collapse'));       // collapsed
        expect(readSkillBarPrefs('P')).toEqual({ collapsed: true, expanded: true });
        fireEvent.click(screen.getByTestId('skill-line'));           // back
        expect(pillNames()).toHaveLength(16);
        expect(screen.getByTestId('skill-stack-toggle')).toHaveTextContent('fewer');
        expect(readSkillBarPrefs('P')).toEqual({ collapsed: false, expanded: true });
    });

    it('a pickup arriving while collapsed shows up as a new ring', () => {
        mockBench.alwaysItems = sixteen();
        writeSkillBarPrefs('P', { collapsed: true, expanded: false });
        const { rerender } = render(<ActiveContextBar />);
        expect(screen.queryByTestId('skill-dot-pickup')).toBeNull();
        expect(screen.getByTestId('skill-line')).toHaveTextContent(/^16 on hand$/);
        mockBench.pickedUpItems = [{ ...always('Packet Diagrams'), effective: 'ondemand' }];
        rerender(<ActiveContextBar />);
        expect(screen.getByTestId('skill-dot-pickup')).toBeInTheDocument();
        expect(screen.getByTestId('skill-line')).toHaveTextContent('· 1 ✦');
    });
});

describe('preferences are per project', () => {
    it('read on mount for the current project', () => {
        mockBench.alwaysItems = sixteen();
        writeSkillBarPrefs('Q', { collapsed: false, expanded: true });
        mockProject.id = 'Q';
        const { unmount } = render(<ActiveContextBar />);
        expect(pillNames()).toHaveLength(16);
        unmount();
        mockProject.id = 'P';
        render(<ActiveContextBar />);
        expect(pillNames()).toHaveLength(CONTEXT_BAR_SKILL_CAP);
    });

    it('unknown project or corrupt storage falls back to the default state', () => {
        expect(readSkillBarPrefs(undefined)).toEqual({ collapsed: false, expanded: false });
        localStorage.setItem('ZIYA_CONTEXT_BAR_SKILLS:P', '{not json');
        expect(readSkillBarPrefs('P')).toEqual({ collapsed: false, expanded: false });
    });
});

describe('at or under the cap nothing stacks and no controls appear', () => {
    it('exactly the cap: all pills, no chip, no ▴', () => {
        mockBench.alwaysItems = sixteen().slice(0, CONTEXT_BAR_SKILL_CAP);
        render(<ActiveContextBar />);
        expect(pillNames()).toHaveLength(CONTEXT_BAR_SKILL_CAP);
        expect(screen.queryByTestId('skill-stack-toggle')).toBeNull();
        expect(screen.queryByTestId('skill-collapse')).toBeNull();
        expect(screen.queryByTestId('skill-line')).toBeNull();
    });

    it('a stored collapsed preference does not fold three pills', () => {
        mockBench.alwaysItems = sixteen().slice(0, 3);
        writeSkillBarPrefs('P', { collapsed: true, expanded: false });
        render(<ActiveContextBar />);
        expect(pillNames()).toHaveLength(3);
        expect(screen.queryByTestId('skill-line')).toBeNull();
    });

    it('one past the cap is enough to stack', () => {
        mockBench.alwaysItems = sixteen().slice(0, CONTEXT_BAR_SKILL_CAP + 1);
        render(<ActiveContextBar />);
        expect(pillNames()).toHaveLength(CONTEXT_BAR_SKILL_CAP);
        expect(screen.getByTestId('skill-stack-toggle')).toHaveTextContent('+1 skill');
    });
});
