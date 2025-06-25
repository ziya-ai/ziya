import React, { useEffect, useState } from 'react';
import { Progress, Tooltip, Card } from 'antd';
import { AstStatus, fetchAstStatus } from '../apis/astApi';
import { useTheme } from '../context/ThemeContext';

// Configuration
const AST_STATUS_CHECK_INTERVAL = 3000; // 3 seconds

/**
 * Format elapsed time in a human-readable format
 * @param seconds - Elapsed time in seconds
 * @returns Formatted time string
 */
const formatElapsedTime = (seconds: number | null): string => {
  if (!seconds) return '';

  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  } else if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = Math.round(seconds % 60);
    return `${minutes}m ${remainingSeconds}s`;
  } else {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  }
};

/**
 * Trigger immediate AST status check (can be called from other components)
 */
export const triggerAstStatusCheck = () => {
  // This could dispatch a custom event that the component listens for
  window.dispatchEvent(new CustomEvent('ast-config-changed'));
};

/**
 * AST Status Indicator Component
 * Shows the current status of AST indexing
 */
const AstStatusIndicator: React.FC = () => {
  const [status, setStatus] = useState<AstStatus | null>(null);
  const [visible, setVisible] = useState<boolean>(false);
  const [hasSeenActiveIndexing, setHasSeenActiveIndexing] = useState<boolean>(false);
  const { isDarkMode } = useTheme();

  useEffect(() => {
    let timer: number | null = null;
    let mounted = true;

    const checkStatus = async () => {
      try {
        const astStatus = await fetchAstStatus();

        if (mounted) {
          setStatus(astStatus);
          
          // Track if we've ever seen active indexing
          if (astStatus.is_indexing || astStatus.is_complete) {
            setHasSeenActiveIndexing(true);
          }

          // Show the indicator if indexing is in progress
          if (astStatus.is_indexing || astStatus.error) {
            setVisible(true);
          }

          // Hide the indicator 5 seconds after indexing completes
          if (astStatus.is_complete && !astStatus.error) {
            setTimeout(() => {
              if (mounted) setVisible(false);
            }, 5000);
          }

          // Continue checking if indexing is still in progress
          if (astStatus.is_indexing && !astStatus.is_complete) {
            timer = window.setTimeout(checkStatus, AST_STATUS_CHECK_INTERVAL);
          }
        }
      } catch (error) {
        console.error('Error checking AST status:', error);
        
        // Only continue checking if we've previously seen active indexing
        if (hasSeenActiveIndexing) {
          // Try again after a delay
          if (mounted) {
            timer = window.setTimeout(checkStatus, AST_STATUS_CHECK_INTERVAL);
          }
        }
      }
    };

    // Start checking AST status
    checkStatus();

    // Add event listener for page visibility changes
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        // Resume checking when page becomes visible
        if (!timer && mounted) {
          checkStatus();
        }
      } else {
        // Pause checking when page is hidden
        if (timer) {
          window.clearTimeout(timer);
          timer = null;
        }
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    // Listen for AST config changes
    const handleConfigChange = () => {
      setHasSeenActiveIndexing(false); // Reset to allow fresh detection
      checkStatus(); // Immediately check status
    };

    window.addEventListener('ast-config-changed', handleConfigChange);

    // Cleanup
    return () => {
      mounted = false;
      if (timer) {
        window.clearTimeout(timer);
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('ast-config-changed', handleConfigChange);
    };
  }, [hasSeenActiveIndexing]);

  // Don't render anything if there's no status, if it shouldn't be visible, or if we haven't seen active indexing
  if (!status || !visible) {
    return null;
  }

  // Render error state
  if (status.error) {
    // Only show error if we've previously seen active indexing
    if (!hasSeenActiveIndexing) {
      return null;
    }
    
    return (
      <Card
        className="ast-status-error"
        style={{
          position: 'fixed',
          bottom: '10px',
          right: '10px',
          width: '300px',
          backgroundColor: isDarkMode ? '#2a1f1f' : '#fff1f0',
          borderColor: isDarkMode ? '#a61d24' : '#ffa39e',
          color: isDarkMode ? '#ff7875' : undefined
        }}
      >
        <div>
          <strong>AST Indexing Error</strong>
          <p>{status.error}</p>
        </div>
      </Card>
    );
  }

  // Render indexing in progress
  if (status.is_indexing && !status.is_complete) {
    const elapsedStr = status.elapsed_seconds ? ` (${formatElapsedTime(status.elapsed_seconds)})` : '';

    return (
      <Card
        className="ast-status-indicator"
        style={{
          position: 'fixed',
          bottom: '10px',
          right: '10px',
          width: '300px',
          backgroundColor: isDarkMode ? '#141414' : '#ffffff',
          borderColor: isDarkMode ? '#303030' : '#d9d9d9',
          color: isDarkMode ? '#ffffff' : undefined
        }}
      >
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
            <strong>AST Indexing</strong>
            <span>{status.completion_percentage}%{elapsedStr}</span>
          </div>
          <Tooltip title={`${status.indexed_files}/${status.total_files} files processed`}>
            <Progress
              percent={status.completion_percentage}
              size="small"
              status="active"
              showInfo={false}
            />
          </Tooltip>
          <div style={{ fontSize: '12px', color: '#8c8c8c', marginTop: '4px' }}>
            {status.indexed_files}/{status.total_files} files processed
          </div>
        </div>
      </Card>
    );
  }

  // Render completed state
  if (status.is_complete) {
    return (
      <Card
        className="ast-status-complete"
        style={{
          position: 'fixed',
          bottom: '10px',
          right: '10px',
          width: '300px',
          backgroundColor: isDarkMode ? '#162312' : '#f6ffed',
          borderColor: isDarkMode ? '#389e0d' : '#b7eb8f',
          color: isDarkMode ? '#ffffff' : undefined
        }}
      >
        <div>
          <strong>AST Indexing Complete</strong>
          <div style={{ fontSize: '12px', color: '#8c8c8c', marginTop: '4px' }}>
            {status.indexed_files} files indexed
          </div>
        </div>
      </Card>
    );
  }

  return null;
};

export default AstStatusIndicator;
