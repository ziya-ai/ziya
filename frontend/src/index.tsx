import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import '@fortawesome/fontawesome-free/css/all.min.css';
import {App} from "./components/App";
import {ChatProvider} from "./context/ChatContext";
import {FolderProvider} from "./context/FolderContext";


const root = ReactDOM.createRoot(
    document.getElementById('root') as HTMLElement
);
root.render(
    <React.StrictMode>
        <ChatProvider>
            <FolderProvider>
                <App/>
            </FolderProvider>
        </ChatProvider>
    </React.StrictMode>
);
