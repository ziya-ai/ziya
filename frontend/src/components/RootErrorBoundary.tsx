import React from 'react';

/**
 * Root error boundary — catches fatal errors from context providers
 * that sit above all other error boundaries.  Persists the full stack
 * trace to localStorage so it survives the reload and can be read from
 * the JS console or the /debug page.
 */

const CRASH_LOG_KEY = 'ZIYA_CRASH_LOG';
const MAX_CRASH_ENTRIES = 20;

interface CrashEntry {
  timestamp: string;
  message: string;
  stack: string;
  componentStack: string;
}

function persistCrash(entry: CrashEntry) {
  try {
    const raw = localStorage.getItem(CRASH_LOG_KEY);
    const log: CrashEntry[] = raw ? JSON.parse(raw) : [];
    log.push(entry);
    // Keep only the most recent entries
    while (log.length > MAX_CRASH_ENTRIES) log.shift();
    localStorage.setItem(CRASH_LOG_KEY, JSON.stringify(log));
  } catch { /* localStorage full or unavailable — non-fatal */ }
}

/** Errors matching any of these patterns are benign browser noise
 *  and should not be persisted or surfaced to the user. */
const SUPPRESSED_ERRORS = [
  /ResizeObserver loop/,
];

function isSuppressedError(message: string): boolean {
  return SUPPRESSED_ERRORS.some(re => re.test(message));
}

/** Read crash log from console: `getCrashLog()` */
(window as any).getCrashLog = () => {
  try {
    const raw = localStorage.getItem(CRASH_LOG_KEY);
    const log: CrashEntry[] = raw ? JSON.parse(raw) : [];
    if (log.length === 0) {
      console.log('No crashes recorded.');
      return [];
    }
    console.log(`📋 ${log.length} crash(es) recorded:\n`);
    log.forEach((entry, i) => {
      console.group(`Crash #${i + 1} — ${entry.timestamp}`);
      console.error(entry.message);
      console.log(entry.stack);
      if (entry.componentStack) console.log('Component stack:', entry.componentStack);
      console.groupEnd();
    });
    return log;
  } catch { return []; }
};

/** Clear crash log from console: `clearCrashLog()` */
(window as any).clearCrashLog = () => {
  try {
    localStorage.removeItem(CRASH_LOG_KEY);
    console.log('🧹 Crash log cleared.');
  } catch { /* non-fatal */ }
};

interface State {
  hasError: boolean;
  error: Error | null;
  errorCount: number;
  componentStack: string;
}

export class RootErrorBoundary extends React.Component<
  { children: React.ReactNode },
  State
> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null, errorCount: 0, componentStack: '' };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Suppress benign errors — reset state so the app continues rendering
    if (isSuppressedError(error.message)) {
      this.setState({ hasError: false, error: null });
      return;
    }

    const entry: CrashEntry = {
      timestamp: new Date().toISOString(),
      message: error.message,
      stack: error.stack || '(no stack)',
      componentStack: info.componentStack?.slice(0, 2000) || '',
    };

    // Persist so it survives reload
    persistCrash(entry);

    // Also dump to console immediately
    console.error(
      '💥 ROOT CRASH — stack trace persisted to localStorage (run getCrashLog() to review)\n',
      error,
      '\n\nComponent stack:',
      info.componentStack
    );

    this.setState({ componentStack: info.componentStack || '' });
  }

  handleRetry = () => {
    this.setState(prev => ({
      hasError: false,
      error: null,
      errorCount: prev.errorCount + 1,
      componentStack: '',
    }));
  };

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      const canRetry = this.state.errorCount < 3;
      const { error, componentStack } = this.state;
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          justifyContent: 'center', height: '100vh', fontFamily: 'system-ui',
          padding: 32, textAlign: 'center',
          background: '#141414', color: '#e0e0e0',
        }}>
          <h2 style={{ marginBottom: 16 }}>Something went wrong</h2>
          <p style={{ color: '#999', maxWidth: 500, marginBottom: 24 }}>
            A background sync operation encountered an error.
            Your conversation data is safely stored — {canRetry ? 'try recovering' : 'reload to continue'}.
          </p>
          <details style={{ width: '100%', maxWidth: 700, textAlign: 'left', marginBottom: 24 }}>
            <summary style={{ cursor: 'pointer', color: '#888', fontSize: 13 }}>
              Error details (also in console → <code>getCrashLog()</code>)
            </summary>
            <pre style={{
              marginTop: 8, padding: 12, borderRadius: 6,
              background: '#1e1e1e', color: '#f48771', fontSize: 12,
              overflow: 'auto', maxHeight: 300, whiteSpace: 'pre-wrap',
              border: '1px solid #333',
            }}>
{error?.message}
{'\n\n'}
{error?.stack}
{componentStack ? `\n\nComponent stack:${componentStack}` : ''}
            </pre>
          </details>
          <div style={{ display: 'flex', gap: 12 }}>
            {canRetry && (
              <button onClick={this.handleRetry} style={{
                padding: '8px 24px', borderRadius: 6, border: '1px solid #1890ff',
                background: '#1890ff', color: '#fff', cursor: 'pointer', fontSize: 14,
              }}>Recover</button>
            )}
            <button onClick={this.handleReload} style={{
              padding: '8px 24px', borderRadius: 6, border: '1px solid #555',
              background: '#333', color: '#ccc', cursor: 'pointer', fontSize: 14,
            }}>Reload Page</button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
