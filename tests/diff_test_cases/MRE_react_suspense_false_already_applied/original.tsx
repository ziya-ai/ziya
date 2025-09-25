import React, { Suspense } from 'react';

export const App: React.FC = () => {
    const [showShellConfig, setShowShellConfig] = useState(false);
    const [showMCPStatus, setShowMCPStatus] = useState(false);
    const mcpEnabled = true;

    return (
        <div className="app">
            <div className="main-content">
                <div className="sidebar">
                    {/* Sidebar content */}
                </div>
                
                <div className="content-area">
                    {/* Main content */}
                    
                    <Suspense fallback={null}>
                        {mcpEnabled && (
                                <ShellConfigModal
                                    visible={showShellConfig}
                                    onClose={() => setShowShellConfig(false)}
                                />
                                <MCPStatusModal
                                    visible={showMCPStatus}
                                    onClose={() => setShowMCPStatus(false)}
                                />
                        )}
                    </Suspense>
                    
                    <div className="footer">
                        {/* Footer content */}
                    </div>
                </div>
            </div>
        </div>
    );
};
