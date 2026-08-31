/**
 * Provider stack for the application root, branched by route.
 *
 * The chat-side providers (Project / Chat / Folder / Question) boot the whole
 * workspace: project sync, the file-tree websocket, token counting, an
 * IndexedDB open.  The headless diagram harness at /render needs none of it —
 * DiagramRenderPage and D3Renderer consume ThemeContext only, and nothing in
 * the D3 plugin subtree references the chat-side contexts — but it used to
 * mount them all anyway, because the route lived inside the stack.
 *
 * That was survivable until a project accumulated enough conversations for
 * ChatContext's server-sync loop to saturate the main thread.  At ~1,150
 * conversations, listChats repeatedly took 0.2-3.2s, and Playwright's
 * wait_for_function polls INSIDE the page, so it could not be serviced: a
 * diagram that finished in milliseconds (data-render-status reached
 * "complete", one SVG, no page errors) took 220s of wall clock for a 60s
 * in-page wait to expire, and render_diagram failed at its 300s ceiling on
 * work that had already succeeded.  Contention, not a renderer fault.
 *
 * /print deliberately keeps the full stack: PrintRenderPage drives the real
 * MarkdownRenderer over a whole conversation and does depend on chat state,
 * so narrowing it would break PDF and HTML export.
 */

import React from 'react';
import { ProjectProvider } from '../context/ProjectContext';
import { ChatProvider } from '../context/ChatContext';
import { FolderProvider } from '../context/FolderContext';
import { QuestionProvider } from '../context/QuestionContext';

/**
 * Exact match (ignoring a trailing slash) rather than a prefix test, so a
 * future /render-something route has to opt in deliberately instead of
 * inheriting the lean stack by accident.
 */
export const isHeadlessDiagramPath = (pathname: string): boolean =>
    pathname.replace(/\/+$/, '') === '/render';

interface AppProvidersProps {
    children: React.ReactNode;
    /** Overridable for tests; defaults to the live location. */
    pathname?: string;
}

export const AppProviders: React.FC<AppProvidersProps> = ({
    children,
    pathname,
}) => {
    // Read once at mount: /render is always a fresh page load in headless
    // Chromium, and a provider stack that changed shape on navigation would
    // unmount and remount the whole app.
    const path = pathname ?? window.location.pathname;

    if (isHeadlessDiagramPath(path)) {
        return <>{children}</>;
    }

    return (
        <ProjectProvider>
            <ChatProvider>
                <FolderProvider>
                    <QuestionProvider>
                        {children}
                    </QuestionProvider>
                </FolderProvider>
            </ChatProvider>
        </ProjectProvider>
    );
};
