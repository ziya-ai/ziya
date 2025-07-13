/**
 * Logging utilities for Ziya
 */

// Import the environment check function
// Note: We need to use require instead of import for .js files in TypeScript
// when the file doesn't have type definitions
const envSetup = require('./envSetup');

/**
 * Check if debug logging should be enabled
 * Returns true if:
 * 1. NODE_ENV is 'development' OR
 * 2. ZIYA_LOG_LEVEL is set to 'DEBUG'
 */
export const isDebugLoggingEnabled = (): boolean => {
  return envSetup.isDebugMode();
};

/**
 * Debug log function that only logs when debug logging is enabled
 */
export const debugLog = (message: string, ...args: any[]): void => {
  if (isDebugLoggingEnabled()) {
    console.debug(`[ZIYA DEBUG] ${message}`, ...args);
  }
};

/**
 * Info log function
 */
export const infoLog = (message: string, ...args: any[]): void => {
  console.log(`[ZIYA INFO] ${message}`, ...args);
};

/**
 * Warning log function
 */
export const warnLog = (message: string, ...args: any[]): void => {
  console.warn(`[ZIYA WARN] ${message}`, ...args);
};

/**
 * Error log function
 */
export const errorLog = (message: string, ...args: any[]): void => {
  console.error(`[ZIYA ERROR] ${message}`, ...args);
};
