export const App: React.FC = () => {
    // ... other code ...

                    <Suspense fallback={null}>
                        {mcpEnabled && (
                            <>
                                <ShellConfigModal
                                    visible={showShellConfig}
                                    onClose={() => setShowShellConfig(false)}
                                />
                                <MCPStatusModal
                                    visible={showMCPStatus}
                                    onClose={() => setShowMCPStatus(false)}
                                />
                            </>
                        )}
                    </Suspense>

    // ... rest of component ...
};
