// Import environment utilities
import './utils/logUtils';

// Initialize FormatterRegistry globally
import './utils/formatterRegistry';

import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import './index.css';
import './styles/mermaid-theme.css';
import './styles/mui-overrides.css';
import '@fortawesome/fontawesome-free/css/all.min.css';
import { App } from "./components/App";
import { RootErrorBoundary } from "./components/RootErrorBoundary";
import { Debug } from "./components/Debug";
import { SystemInfo } from "./components/SystemInfo";
import { ChatProvider } from "./context/ChatContext";
import { FolderProvider } from "./context/FolderContext";
import { ThemeProvider } from "./context/ThemeContext";
import { ConfigProvider } from './context/ConfigContext';
import { QuestionProvider } from "./context/QuestionContext";
import { ServerStatusProvider } from './context/ServerStatusContext';
import { ProjectProvider } from './context/ProjectContext';

// hide unhandled promise rejections from making console spam
window.addEventListener('unhandledrejection', (event) => {
    // Suppress extension context errors
    if (event.reason?.message?.includes('Extension context invalidated')) {
        event.preventDefault(); // Prevent the error from appearing in console
        return;
    }
    // Suppress network errors from server shutdown — not a client bug
    if (
        event.reason instanceof TypeError &&
        event.reason.message === 'Failed to fetch'
    ) {
        event.preventDefault();
        return;
    }
    // Persist to crash log for post-mortem diagnosis
    try {
        const reason = event.reason;
        const entry = {
            timestamp: new Date().toISOString(),
            message: `[UnhandledRejection] ${reason?.message || String(reason)}`,
            stack: reason?.stack || '(no stack)',
            componentStack: '',
        };
        const raw = localStorage.getItem('ZIYA_CRASH_LOG');
        const log = raw ? JSON.parse(raw) : [];
        log.push(entry);
        while (log.length > 20) log.shift();
        localStorage.setItem('ZIYA_CRASH_LOG', JSON.stringify(log));
        console.error('💥 Unhandled rejection persisted to crash log:', reason);
    } catch { /* non-fatal */ }
});

// ── WHITE SCREEN DETECTOR ──────────────────────────────────────
// Polls the DOM to detect when React's root element loses all children
// (white screen crash).  When detected, snapshots console errors and
// DOM state to localStorage before the evidence disappears on reload.
let _wsDetectorActive = false;
function startWhiteScreenDetector() {
    if (_wsDetectorActive) return;
    _wsDetectorActive = true;
    const root = document.getElementById('root');
    if (!root) return;
    let consecutiveEmpty = 0;
    let lastChildCount = 0;
    const check = () => {
        const childCount = root.childElementCount;
        // Only trigger if root HAD children (app mounted) then lost them
        if (childCount === 0 && lastChildCount > 0) {
            consecutiveEmpty++;
            if (consecutiveEmpty >= 2) {
                try {
                    const entry = {
                        timestamp: new Date().toISOString(),
                        message: '[WHITE_SCREEN] React root lost all children',
                        stack: new Error('White screen detected').stack || '',
                        componentStack: `lastChildCount=${lastChildCount}`,
                        recentErrors: (window as any).__recentErrors?.slice(-10) || [],
                    };
                    const raw = localStorage.getItem('ZIYA_CRASH_LOG');
                    const log = raw ? JSON.parse(raw) : [];
                    log.push(entry);
                    while (log.length > 20) log.shift();
                    localStorage.setItem('ZIYA_CRASH_LOG', JSON.stringify(log));
                    console.error('💥 WHITE SCREEN DETECTED — crash data saved to ZIYA_CRASH_LOG');
                } catch {}
                consecutiveEmpty = 0;
            }
        } else {
            consecutiveEmpty = 0;
        }
        lastChildCount = childCount;
    };
    setInterval(check, 500);
}
setTimeout(startWhiteScreenDetector, 3000);

// ── RECENT ERROR BUFFER ────────────────────────────────────────
// Rolling buffer of console.error calls so the white screen detector
// can capture what happened right before death.
(function() {
    const origError = console.error;
    const buffer: string[] = [];
    (window as any).__recentErrors = buffer;
    console.error = function(...args: any[]) {
        try {
            buffer.push(args.map(a => {
                if (a instanceof Error) return `${a.message}\n${a.stack}`;
                if (typeof a === 'string') return a;
                try { return JSON.stringify(a)?.slice(0, 500); } catch { return String(a); }
            }).join(' '));
            while (buffer.length > 20) buffer.shift();
        } catch {}
        return origError.apply(console, args);
    };
})();

window.addEventListener('error', (event) => {
    try {
        const entry = {
            timestamp: new Date().toISOString(),
            message: `[GlobalError] ${event.message}`,
            stack: event.error?.stack || `at ${event.filename}:${event.lineno}:${event.colno}`,
            componentStack: '',
        };
        const raw = localStorage.getItem('ZIYA_CRASH_LOG');
        const log = raw ? JSON.parse(raw) : [];
        log.push(entry);
        while (log.length > 20) log.shift();
        localStorage.setItem('ZIYA_CRASH_LOG', JSON.stringify(log));
    } catch { /* non-fatal */ }
});

// Load internal formatters if available (created by internal build)
try {
    require('./formatters/internal-formatters');
} catch (e) {
    // Not an internal build - this is fine
}

const root = ReactDOM.createRoot(
    document.getElementById('root') as HTMLElement
);
root.render(
    // StrictMode temporarily disabled to eliminate duplicate API calls in development
    // <React.StrictMode>
    <ConfigProvider>
        <ThemeProvider>
            <ServerStatusProvider>
              <RootErrorBoundary>
                <ProjectProvider>  
                    <ChatProvider>
                        <FolderProvider>
                            <QuestionProvider>
                            <BrowserRouter>
                                <Routes>
                                    <Route path="/" element={<App />} />
                                    <Route
                                    path="/info"
                                    element={<SystemInfo />}
                                    />
                                    <Route
                                    path="/debug"
                                    element={<Debug />}
                                    />
                                </Routes>
                            </BrowserRouter>
                            </QuestionProvider>
                        </FolderProvider>
                    </ChatProvider>
                </ProjectProvider>
              </RootErrorBoundary>
            </ServerStatusProvider>
        </ThemeProvider>
    </ConfigProvider>
    // </React.StrictMode>
);
