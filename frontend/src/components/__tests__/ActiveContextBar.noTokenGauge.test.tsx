/**
 * @jest-environment jsdom
 *
 * ActiveContextBar carries no token figure.
 *
 * Observed 2026-10-20: the strip at the top of the sidebar tabs read
 * "0 files · 0 tokens" over a thin gradient bar while the footer showed
 * 581k file tokens.  The bar's tokenInfo came from ProjectContext, which
 * only prices named contexts + skills (not the file-tree selection), against
 * a hard-coded 200k limit.  The footer's TokenBreakdownBar is the one budget
 * gauge; this bar keeps only the context / skill / +N files pills.
 *
 * FAILS against the pre-fix component, which rendered "581,000 tokens" and
 * "2 files" from the mocked tokenInfo.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import * as fs from 'fs';
import * as path from 'path';

const benchMock = { ready: true, alwaysItems: [] as any[], pickedUpItems: [] as any[] };
jest.mock('../../hooks/useBench', () => ({ useBench: () => benchMock }));
jest.mock('../../context/ThemeContext', () => ({ useTheme: () => ({ isDarkMode: false }) }));
jest.mock('../../context/ActiveChatContext', () => ({ useActiveChatOptional: () => undefined }));
jest.mock('../../context/ProjectContext', () => ({
    useProject: () => ({
        contexts: [], skills: [],
        activeContextIds: [], activeSkillIds: [],
        additionalFiles: ['a.py'],
        removeContextFromLens: jest.fn(), removeSkillFromLens: jest.fn(),
        // A populated tokenInfo: if the bar still read it, these would render.
        tokenInfo: { deduplicatedTokens: 581000, fileTokens: { 'a.py': 1, 'b.py': 2 }, astTokens: 1200 },
        isCalculatingTokens: false,
        currentProject: { id: 'P' }, skillsProjectId: 'P',
    }),
}));

import { ActiveContextBar } from '../ActiveContextBar';

describe('ActiveContextBar has no token gauge', () => {
    it('renders the pills but no file count, token total, or AST figure', () => {
        render(<ActiveContextBar />);
        expect(screen.getByText('+1 files')).toBeInTheDocument();   // positive: the bar did render
        expect(screen.queryByText(/tokens/)).toBeNull();
        expect(screen.queryByText(/581/)).toBeNull();
        expect(screen.queryByText(/^2 files/)).toBeNull();
        expect(screen.queryByText(/AST/)).toBeNull();
    });

    it('does not read ProjectContext token figures at all (source contract)', () => {
        const src = fs.readFileSync(path.join(__dirname, '..', 'ActiveContextBar.tsx'), 'utf8');
        expect(src).not.toMatch(/deduplicatedTokens|isCalculatingTokens|contextLimit/);
    });
});
