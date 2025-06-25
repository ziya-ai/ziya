/**
 * AST Status Monitoring
 * 
 * This module checks the status of AST indexing and updates the UI accordingly.
 */

// Types
interface AstStatus {
  is_indexing: boolean;
  completion_percentage: number;
  is_complete: boolean;
  indexed_files: number;
  total_files: number;
  elapsed_seconds: number | null;
  error: string | null;
}

// Configuration
const AST_STATUS_CHECK_INTERVAL: number = 3000; // 3 seconds
const AST_STATUS_ENDPOINT: string = '/api/ast/status';

// State
let astStatusCheckTimer: number | null = null;
let lastPercentage: number = -1;
let hasSeenActiveIndexing: boolean = false;

/**
 * Format elapsed time in a human-readable format
 * @param seconds - Elapsed time in seconds
 * @returns Formatted time string
 */
function formatElapsedTime(seconds: number | null): string {
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
}

/**
 * Create or update the AST status indicator in the UI
 * @param status - AST indexing status object
 */
function updateAstStatusUI(status: AstStatus): void {
  // Find or create the status container
  let statusContainer = document.getElementById('ast-status-container');
  
  // Don't show UI unless we've seen active indexing or there's actual indexing happening
  if (!hasSeenActiveIndexing && !status.is_indexing && !status.is_complete) {
    return;
  }
  
  if (!statusContainer) {
    // Create the status container if it doesn't exist
    statusContainer = document.createElement('div');
    statusContainer.id = 'ast-status-container';
    statusContainer.className = 'ast-status-container';
    
    // Add styles
    statusContainer.style.position = 'fixed';
    statusContainer.style.bottom = '10px';
    statusContainer.style.right = '10px';
    statusContainer.style.padding = '8px 12px';
    statusContainer.style.backgroundColor = 'rgba(0, 0, 0, 0.7)';
    statusContainer.style.color = 'white';
    statusContainer.style.borderRadius = '4px';
    statusContainer.style.fontSize = '12px';
    statusContainer.style.zIndex = '1000';
    statusContainer.style.transition = 'opacity 0.3s ease-in-out';
    
    document.body.appendChild(statusContainer);
  }
  
  // Update the content based on status
  if (status.is_indexing && !status.is_complete) {
    const elapsedStr = status.elapsed_seconds ? ` (${formatElapsedTime(status.elapsed_seconds)})` : '';
    statusContainer.innerHTML = `
      <div>
        <strong>AST Indexing:</strong> ${status.completion_percentage}%${elapsedStr}
        <div class="progress-bar" style="height: 4px; background-color: #333; margin-top: 4px; border-radius: 2px;">
          <div style="width: ${status.completion_percentage}%; height: 100%; background-color: #4CAF50; border-radius: 2px;"></div>
        </div>
        <div style="font-size: 10px; margin-top: 2px;">
          ${status.indexed_files}/${status.total_files} files processed
        </div>
      </div>
    `;
    statusContainer.style.opacity = '1';
  } else if (status.is_complete) {
    statusContainer.innerHTML = `
      <div>
        <strong>AST Indexing:</strong> Complete
        <div style="font-size: 10px; margin-top: 2px;">
          ${status.indexed_files} files indexed
        </div>
      </div>
    `;
    // Fade out after 5 seconds
    setTimeout(() => {
      statusContainer.style.opacity = '0';
      // Remove from DOM after fade out
      setTimeout(() => {
        if (statusContainer && statusContainer.parentNode) {
          statusContainer.parentNode.removeChild(statusContainer);
        }
      }, 300);
    }, 5000);
  } else if (status.error) {
    statusContainer.innerHTML = `
      <div>
        <strong>AST Indexing Error:</strong> ${status.error}
      </div>
    `;
    statusContainer.style.backgroundColor = 'rgba(220, 53, 69, 0.8)';
  }
}

/**
 * Check the status of AST indexing
 */
function checkAstStatus(): void {
  fetch(AST_STATUS_ENDPOINT)
    .then(response => {
      if (!response.ok) {
        throw new Error(`HTTP error! Status: ${response.status}`);
      }
      return response.json() as Promise<AstStatus>;
    })
    .then(status => {
      // Only update UI if status has changed
      
      // Track if we've ever seen active indexing
      if (status.is_indexing || status.is_complete) {
        hasSeenActiveIndexing = true;
      }
      
      // Only show UI if we've seen active indexing or there's an actual indexing process
      if (status.completion_percentage !== lastPercentage) {
        updateAstStatusUI(status);
        lastPercentage = status.completion_percentage;
      }
      
      // Continue checking if indexing is still in progress
      if (status.is_indexing && !status.is_complete) {
        if (astStatusCheckTimer !== null) {
          window.clearTimeout(astStatusCheckTimer);
        }
        astStatusCheckTimer = window.setTimeout(checkAstStatus, AST_STATUS_CHECK_INTERVAL);
      } else {
        // Stop checking if indexing is complete or failed
        if (astStatusCheckTimer !== null) {
          window.clearTimeout(astStatusCheckTimer);
          astStatusCheckTimer = null;
        }
      }
    })
    .catch(error => {
      console.error('Error checking AST status:', error);
      
      // Only show error UI if we've previously seen active indexing
      if (hasSeenActiveIndexing) {
        // Try again after a delay
        if (astStatusCheckTimer !== null) {
          window.clearTimeout(astStatusCheckTimer);
        }
        astStatusCheckTimer = window.setTimeout(checkAstStatus, AST_STATUS_CHECK_INTERVAL);
      }
    });
}

/**
 * Initialize AST status monitoring
 */
export function initAstStatusMonitoring(): void {
  // Start checking AST status
  checkAstStatus();
  
  // Add event listener for page visibility changes
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      // Resume checking when page becomes visible
      if (astStatusCheckTimer === null) {
        checkAstStatus();
      }
    } else {
      // Pause checking when page is hidden
      if (astStatusCheckTimer !== null) {
        window.clearTimeout(astStatusCheckTimer);
        astStatusCheckTimer = null;
      }
    }
  });
}

/**
 * Trigger immediate AST status check (useful after config changes)
 */
export function triggerAstStatusCheck(): void {
  // Reset the tracking flag to allow fresh detection
  hasSeenActiveIndexing = false;
  checkAstStatus();
}

// Initialize when the DOM is ready
document.addEventListener('DOMContentLoaded', initAstStatusMonitoring);
