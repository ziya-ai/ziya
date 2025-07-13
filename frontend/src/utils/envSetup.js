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
  // Check if ZIYA_LOG_LEVEL is set to DEBUG
  const isDebugLevel = process.env.ZIYA_LOG_LEVEL && 
                      process.env.ZIYA_LOG_LEVEL.toUpperCase() === 'DEBUG';
  
  // Check if NODE_ENV is development
  const isDevelopment = process.env.NODE_ENV === 'development';
  
  return isDebugLevel || isDevelopment;
};

// Export the environment check function
module.exports = {
  isDebugMode
};
