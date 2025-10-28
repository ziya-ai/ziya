/**
 * Browser detection and compatibility utilities
 */

export interface BrowserInfo {
  name: string;
  version: string;
  isSafari: boolean;
  isChrome: boolean;
  isFirefox: boolean;
  isEdge: boolean;
  isSupported: boolean;
}

/**
 * Detect the current browser and return detailed information
 */
export const getBrowserInfo = (): BrowserInfo => {
  if (typeof navigator === 'undefined') {
    return {
      name: 'Unknown',
      version: 'Unknown',
      isSafari: false,
      isChrome: false,
      isFirefox: false,
      isEdge: false,
      isSupported: true // Assume supported for SSR
    };
  }

  const userAgent = navigator.userAgent;
  
  // Detect Safari (but not Chrome on iOS or other WebKit browsers)
  const isSafari = /^((?!chrome|android).)*safari/i.test(userAgent);
  
  // Detect Chrome (including Chromium-based browsers)
  const isChrome = /chrome|chromium|crios/i.test(userAgent) && !/edg/i.test(userAgent);
  
  // Detect Firefox
  const isFirefox = /firefox|fxios/i.test(userAgent);
  
  // Detect Edge (Chromium-based)
  const isEdge = /edg/i.test(userAgent);

  let name = 'Unknown';
  let version = 'Unknown';

  if (isSafari) {
    name = 'Safari';
    const safariMatch = userAgent.match(/version\/(\d+(\.\d+)*)/i);
    version = safariMatch ? safariMatch[1] : 'Unknown';
  } else if (isEdge) {
    name = 'Microsoft Edge';
    const edgeMatch = userAgent.match(/edg\/(\d+(\.\d+)*)/i);
    version = edgeMatch ? edgeMatch[1] : 'Unknown';
  } else if (isChrome) {
    name = 'Chrome';
    const chromeMatch = userAgent.match(/chrome\/(\d+(\.\d+)*)/i);
    version = chromeMatch ? chromeMatch[1] : 'Unknown';
  } else if (isFirefox) {
    name = 'Firefox';
    const firefoxMatch = userAgent.match(/firefox\/(\d+(\.\d+)*)/i);
    version = firefoxMatch ? firefoxMatch[1] : 'Unknown';
  }
  // Define supported browsers (everything except Safari)
  const isSupported = !isSafari;
  return {
    name,
    version,
    isSafari,
    isChrome,
    isFirefox,
    isEdge,
    isSupported
  };
};

/**
 * Simple Safari detection function
 */
export const isSafari = (): boolean => {
  if (typeof navigator === 'undefined') return false;
  return /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
};

/**
 * Get a user-friendly browser recommendation message
 */
export const getBrowserRecommendation = (): string => {
  return 'For the best experience, please use Chrome, Edge, Firefox, or another modern browser instead of Safari.';
};
