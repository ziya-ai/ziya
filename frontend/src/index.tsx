import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import './index.css';
import '@fortawesome/fontawesome-free/css/all.min.css';
import {App} from "./components/App";
import {Debug} from "./components/Debug";
import {ChatProvider} from "./context/ChatContext";
import {FolderProvider} from "./context/FolderContext";
import {ThemeProvider} from "./context/ThemeContext";

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
		<BrowserRouter>
                      <Routes>
                          <Route path="/" element={<App />} />
                          <Route
                              path="/debug"
                              element={<Debug />}
                          />
                      </Routes>
                  </BrowserRouter>
                </FolderProvider>
            </ChatProvider>
        </ThemeProvider>
    </React.StrictMode>
);
