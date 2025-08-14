/**
 * Environment setup script for Ziya
 * 
 * This script provides a way to check if debug logging should be enabled
 * based on the ZIYA_LOG_LEVEL environment variable
 */

// We can't modify process.env.NODE_ENV at runtime in a Create React App
// Instead, we'll export a function to check if debug logging should be enabled

// Export a function to check if debug logging should be enabled
const isDebugMode = () => {
  // Safely access process.env with fallbacks
  const ziyaLogLevel = (typeof process !== 'undefined' && process.env && process.env.ZIYA_LOG_LEVEL) || '';
  const nodeEnv = (typeof process !== 'undefined' && process.env && process.env.NODE_ENV) || 'production';
  
  // Check if ZIYA_LOG_LEVEL is set to DEBUG
  const isDebugLevel = ziyaLogLevel && ziyaLogLevel.toUpperCase() === 'DEBUG';
  
  // Check if NODE_ENV is development
  const isDevelopment = nodeEnv === 'development';
  
  return isDebugLevel || isDevelopment;
};

// Export the environment check function using ES6 syntax
export { isDebugMode };
