/**
 * The transcript row for a server-authored `projectChange` system message
 * shows the from/to projects and NOT the working directories, which live in
 * `content` for the model (app/utils/project_change_note.py).
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import * as fs from 'fs';
import * as path from 'path';

jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));

import ProjectChangeNotification, { projectChangeText } from '../ProjectChangeNotification';

const moved = {
    kind: 'moved' as const,
    from: { id: 'p1', name: 'Alpha', path: '/work/alpha' },
    to: { id: 'p2', name: 'Beta', path: '/work/beta' },
};

describe('ProjectChangeNotification', () => {
    it('names both projects and never shows a directory', () => {
        render(<ProjectChangeNotification change={moved} />);
        const row = screen.getByText(/Moved from project Alpha to project Beta/);
        expect(row).toBeInTheDocument();
        expect(document.body.textContent).not.toContain('/work/alpha');
        expect(document.body.textContent).not.toContain('/work/beta');
    });

    it('describes the global flips', () => {
        expect(projectChangeText({ kind: 'global_on', from: moved.from, to: moved.from })).toMatch(/Made global.*Alpha/);
        expect(projectChangeText({ kind: 'global_off', from: moved.from, to: moved.from })).toMatch(/No longer global.*Alpha/);
    });

    it('falls back to the id when a project has no name', () => {
        expect(projectChangeText({ kind: 'moved', from: { id: 'p1' }, to: { id: 'p2' } }))
            .toBe('Moved from project p1 to project p2');
    });
});

describe('the seam: Conversation.tsx renders projectChange before the generic system branch', () => {
    const src = fs.readFileSync(path.join(__dirname, '..', 'Conversation.tsx'), 'utf8');
    it('imports the component and branches on msg.projectChange', () => {
        expect(src).toMatch(/import ProjectChangeNotification from '\.\/ProjectChangeNotification'/);
        const branch = src.indexOf("msg.role === 'system' && msg.projectChange");
        expect(branch).toBeGreaterThan(-1);
        // Before the handoffNotice branch and the generic content fallthrough.
        expect(branch).toBeLessThan(src.indexOf("msg.role === 'system' && msg.handoffNotice"));
        expect(src.slice(branch, branch + 400)).toMatch(/<ProjectChangeNotification change=\{msg\.projectChange\}/);
    });
});
