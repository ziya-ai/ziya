// Add to existing info page or create new security section

const SecurityStatusSection = () => {
    const [securityStats, setSecurityStats] = useState<any>(null);
    
    useEffect(() => {
        fetch('/api/debug/mcp-security')
            .then(r => r.json())
            .then(setSecurityStats)
            .catch(console.error);
    }, []);
    
    if (!securityStats) return <div>Loading security status...</div>;
    
    const { live_stats, recent_failures, test_verification_passed } = securityStats;
    
    return (
        <div className="security-status">
            <h2>🔐 MCP Tool Security</h2>
            
            <div className="status-card">
                <h3>Signing Status</h3>
                <div className="status-row">
                    <span>Secret Initialized:</span>
                    <span className={securityStats.secret_initialized ? 'badge-success' : 'badge-error'}>
                        {securityStats.secret_initialized ? '✓ Active' : '✗ Missing'}
                    </span>
                </div>
                <div className="status-row">
                    <span>Self-Test:</span>
                    <span className={test_verification_passed ? 'badge-success' : 'badge-error'}>
                        {test_verification_passed ? '✓ Passed' : '✗ Failed'}
                    </span>
                </div>
            </div>
            
            {live_stats && (
                <div className="status-card">
                    <h3>Verification Statistics</h3>
                    <div className="status-row">
                        <span>Total Verifications:</span>
                        <span>{live_stats.total_verifications}</span>
                    </div>
                    <div className="status-row">
                        <span>Successful:</span>
                        <span className="badge-success">{live_stats.successful}</span>
                    </div>
                    <div className="status-row">
                        <span>Failed:</span>
                        <span className={live_stats.failed > 0 ? 'badge-warning' : 'badge-success'}>
                            {live_stats.failed}
                        </span>
                    </div>
                    <div className="status-row">
                        <span>Success Rate:</span>
                        <span>{live_stats.success_rate_pct}%</span>
                    </div>
                    <div className="status-row">
                        <span>Uptime:</span>
                        <span>{Math.floor(live_stats.uptime_seconds / 60)} minutes</span>
                    </div>
                </div>
            )}
            
            {recent_failures && recent_failures.length > 0 && (
                <div className="status-card">
                    <h3>⚠️ Recent Verification Failures</h3>
                    {recent_failures.map((failure: any, i: number) => (
                        <div key={i} className="failure-item">
                            <strong>{failure.tool}</strong>
                            <div style={{ fontSize: '12px', opacity: 0.8 }}>
                                {failure.error}
                            </div>
                            <div style={{ fontSize: '11px', opacity: 0.6 }}>
                                {failure.age_seconds}s ago
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};
