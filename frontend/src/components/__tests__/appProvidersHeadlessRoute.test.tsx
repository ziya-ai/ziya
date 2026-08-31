/**
 * @jest-environment jsdom
 *
 * The /render harness must NOT mount the chat-side provider stack.
 *
 * Regression origin: render_diagram began timing out at its 300s ceiling on
 * this workspace while the diagram itself rendered fine.  A hand-driven
 * Playwright probe showed #diagram-render-root reaching
 * data-render-status="complete" with one SVG and no page errors, yet a 60s
 * in-page wait took 220s of wall clock to notice it had expired.  The cause
 * was main-thread starvation, not the renderer: /render was a Route nested
 * inside ProjectProvider / ChatProvider / FolderProvider / QuestionProvider,
 * so the headless harness booted the entire workspace — and ChatContext's
 * server-sync loop, with ~1,150 conversations in the project, logged
 * listChats taking 0.2-3.2s over and over.  Playwright's wait_for_function
 * polls inside the page, so it could not be serviced.
 *
 * These tests assert the SEAM — which providers mount for which path —
 * because that is what regressed and what a future route refactor would
 * silently undo.  The providers are stubbed as markers: booting the real
 * ChatContext here would drag in IndexedDB and the sync loop this test
 * exists to keep off the render path.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

jest.mock('../../context/ProjectContext', () => ({
    ProjectProvider: ({ children }: any) => (
        <div data-testid="project-provider">{children}</div>
    ),
}));
jest.mock('../../context/ChatContext', () => ({
    ChatProvider: ({ children }: any) => (
        <div data-testid="chat-provider">{children}</div>
    ),
}));
jest.mock('../../context/FolderContext', () => ({
    FolderProvider: ({ children }: any) => (
        <div data-testid="folder-provider">{children}</div>
    ),
}));
jest.mock('../../context/QuestionContext', () => ({
    QuestionProvider: ({ children }: any) => (
        <div data-testid="question-provider">{children}</div>
    ),
}));

import { AppProviders, isHeadlessDiagramPath } from '../AppProviders';

const CHAT_PROVIDERS = [
    'project-provider', 'chat-provider', 'folder-provider', 'question-provider',
];

describe('AppProviders — headless diagram route', () => {
    it('mounts no chat-side provider on /render, but still renders children', () => {
        render(
            <AppProviders pathname="/render">
                <div data-testid="child">harness</div>
            </AppProviders>
        );
        // Paired positive assertion: proves the subtree rendered at all, so
        // the absence checks below cannot pass by rendering nothing.
        expect(screen.getByTestId('child')).toBeInTheDocument();
        for (const id of CHAT_PROVIDERS) {
            expect(screen.queryByTestId(id)).toBeNull();
        }
    });

    it('mounts the full chat-side stack on the app root', () => {
        render(
            <AppProviders pathname="/">
                <div data-testid="child">app</div>
            </AppProviders>
        );
        expect(screen.getByTestId('child')).toBeInTheDocument();
        for (const id of CHAT_PROVIDERS) {
            expect(screen.getByTestId(id)).toBeInTheDocument();
        }
    });

    it('keeps the full stack on /print, which renders real conversations', () => {
        // PrintRenderPage drives the real MarkdownRenderer over a whole
        // conversation and DOES depend on chat state — narrowing its
        // providers would break PDF/HTML export.
        render(
            <AppProviders pathname="/print">
                <div data-testid="child">print</div>
            </AppProviders>
        );
        for (const id of CHAT_PROVIDERS) {
            expect(screen.getByTestId(id)).toBeInTheDocument();
        }
    });

    it('recognises /render with a trailing slash and nothing else', () => {
        expect(isHeadlessDiagramPath('/render')).toBe(true);
        expect(isHeadlessDiagramPath('/render/')).toBe(true);
        expect(isHeadlessDiagramPath('/')).toBe(false);
        expect(isHeadlessDiagramPath('/print')).toBe(false);
        // Not a prefix match: a future /render-something route must opt in
        // deliberately rather than inherit the lean stack by accident.
        expect(isHeadlessDiagramPath('/rendering')).toBe(false);
    });
});
