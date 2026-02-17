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
    // Let other unhandled rejections propagate normally
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
            </ServerStatusProvider>
        </ThemeProvider>
    </ConfigProvider>
    // </React.StrictMode>
);
