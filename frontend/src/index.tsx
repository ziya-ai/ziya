import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import './index.css';
import './styles/mermaid-theme.css';
import './styles/mui-overrides.css';
import '@fortawesome/fontawesome-free/css/all.min.css';
import {App} from "./components/App";
import {Debug} from "./components/Debug";
import {ChatProvider} from "./context/ChatContext";
import {FolderProvider} from "./context/FolderContext";
import {ThemeProvider} from "./context/ThemeContext";
import {QuestionProvider} from "./context/QuestionContext";

// hide unhandled promise rejections from making console spam
window.addEventListener('unhandledrejection', (event) => {
    // Suppress extension context errors
    if (event.reason?.message?.includes('Extension context invalidated')) {
        event.preventDefault(); // Prevent the error from appearing in console
        return;
    }
    // Let other unhandled rejections propagate normally
});


const root = ReactDOM.createRoot(
    document.getElementById('root') as HTMLElement
);
root.render(
    <React.StrictMode>
        <ThemeProvider>
            <ChatProvider>
                <FolderProvider>
                    <QuestionProvider>
                        <BrowserRouter>
                            <Routes>
                                <Route path="/" element={<App />} />
                                <Route
                                    path="/debug"
                                    element={<Debug />}
                                />
                            </Routes>
                        </BrowserRouter>
                    </QuestionProvider>
                </FolderProvider>
            </ChatProvider>
        </ThemeProvider>
    </React.StrictMode>
);
