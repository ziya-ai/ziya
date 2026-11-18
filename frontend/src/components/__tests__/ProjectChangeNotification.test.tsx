/**
 * The transcript row for a server-authored `projectChange` system message
 * states the from/to projects AND their root directories as visible text.
 *
 * An earlier version of this test required the directories to be hidden
 * (they were to live only in `content`, for the model).  That left the
 * rendered transcript — what a person, a screenshot or a pasted copy shows —
 * without the half of the record that says where the conversation is rooted.
 * A tooltip is not a record: nothing that reads the page sees it.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import * as fs from 'fs';
import * as path from 'path';

jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));

import ProjectChangeNotification, { projectChangeText, projectChangeDirs } from '../ProjectChangeNotification';

const moved = {
    kind: 'moved' as const,
    from: { id: 'p1', name: 'Alpha', path: '/work/alpha' },
    to: { id: 'p2', name: 'Beta', path: '/work/beta' },
};

describe('ProjectChangeNotification', () => {
    it('names both projects and shows both root directories as text', () => {
        render(<ProjectChangeNotification change={moved} />);
        expect(screen.getByText(/Moved from project Alpha to project Beta/)).toBeInTheDocument();
        const text = document.body.textContent || '';
        expect(text).toContain('/work/alpha');
        expect(text).toContain('/work/beta');
        // From before to: the order is the information.
        expect(text.indexOf('/work/alpha')).toBeLessThan(text.indexOf('/work/beta'));
    });

    it('the directories are not carried by a title attribute alone', () => {
        const { container } = render(<ProjectChangeNotification change={moved} />);
        expect(container.querySelector('[title]')).toBeNull();
    });

    it('describes the global flips, with the owner directory', () => {
        const on = { kind: 'global_on' as const, from: moved.from, to: moved.from };
        const off = { kind: 'global_off' as const, from: moved.from, to: moved.from };
        expect(projectChangeText(on)).toMatch(/Made global.*Alpha/);
        expect(projectChangeText(off)).toMatch(/No longer global.*Alpha/);
        expect(projectChangeDirs(on)).toBe('Root directory: /work/alpha');
        render(<ProjectChangeNotification change={off} />);
        expect(document.body.textContent).toContain('/work/alpha');
    });

    it('falls back to the id when a project has no name, and says so when a directory is unknown', () => {
        const bare = { kind: 'moved' as const, from: { id: 'p1' }, to: { id: 'p2' } };
        expect(projectChangeText(bare)).toBe('Moved from project p1 to project p2');
        expect(projectChangeDirs(bare)).toBe('Root directory: unknown \u2192 unknown');
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
