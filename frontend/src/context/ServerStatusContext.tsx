/**
 * ServerStatusContext — tracks whether the backend server is reachable.
 *
 * Polls a lightweight endpoint (/api/config) and exposes a boolean. Uses generous
 * timeouts and failure thresholds to avoid false "unreachable" banners during load.
 * Components use this to show a connectivity banner and disable actions
 * that require the server (e.g. sending messages).
 */
import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useRef,
  useCallback,
  ReactNode,
} from 'react';

interface ServerStatusContextType {
  /** True when the last health check succeeded. */
  isServerReachable: boolean;
}

const ServerStatusContext = createContext<ServerStatusContextType>({
  isServerReachable: true,
});

// How often to poll when healthy vs unhealthy (ms)
const POLL_INTERVAL_HEALTHY = 30_000;
const POLL_INTERVAL_UNHEALTHY = 5_000;

export const ServerStatusProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  // Assume reachable on first render so there's no flash of the banner
  const [isServerReachable, setIsServerReachable] = useState(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Track consecutive failures to avoid flashing the banner on a single
  // dropped request (e.g. browser GC pause).
  const consecutiveFailures = useRef(0);
  // Require 3 consecutive failures before showing the banner.
  // During heavy model operations (streaming, retries) the server event loop
  // may be briefly busy, so a single or double timeout is not a true outage.
  const FAILURE_THRESHOLD = 3;

  const checkHealth = useCallback(async () => {
    try {
      const controller = new AbortController();
      // 12-second timeout: boto3 Bedrock calls can block the event loop for
      // several seconds during streaming.  A 5s timeout caused false
      // "unreachable" banners during normal model retries.
      const timeoutId = setTimeout(() => controller.abort(), 12_000);

      const res = await fetch('/api/config', {
        signal: controller.signal,
        // Bypass browser cache so we always hit the server
        cache: 'no-store',
      });
      clearTimeout(timeoutId);

      if (res.ok) {
        consecutiveFailures.current = 0;
        setIsServerReachable(true);
      } else {
        consecutiveFailures.current += 1;
        if (consecutiveFailures.current >= FAILURE_THRESHOLD) {
          setIsServerReachable(false);
        }
      }
    } catch {
      consecutiveFailures.current += 1;
      if (consecutiveFailures.current >= FAILURE_THRESHOLD) {
        setIsServerReachable(false);
      }
    }
  }, []);

  useEffect(() => {
    // Initial check
    checkHealth();

    const schedule = () => {
      const interval = consecutiveFailures.current >= FAILURE_THRESHOLD
        ? POLL_INTERVAL_UNHEALTHY
        : POLL_INTERVAL_HEALTHY;
      timerRef.current = setTimeout(async () => {
        await checkHealth();
        schedule();
      }, interval);
    };

    schedule();

    // Also re-check immediately when the browser comes back online
    const handleOnline = () => {
      checkHealth();
    };
    window.addEventListener('online', handleOnline);

    // When any streaming response arrives, the server is definitely alive.
    // Reset consecutive failures so the banner doesn't appear mid-stream.
    const handleStreamProof = () => {
      if (consecutiveFailures.current > 0) {
        consecutiveFailures.current = 0;
        setIsServerReachable(true);
      }
    };
    // chatApi dispatches this whenever it receives a data chunk from the server
    document.addEventListener('serverAliveProof', handleStreamProof);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      window.removeEventListener('online', handleOnline);
      document.removeEventListener('serverAliveProof', handleStreamProof);
    };
  }, [checkHealth]);

  return (
    <ServerStatusContext.Provider value={{ isServerReachable }}>
      {children}
    </ServerStatusContext.Provider>
  );
};

export const useServerStatus = (): ServerStatusContextType =>
  useContext(ServerStatusContext);
