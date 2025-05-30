import React, { useEffect, ComponentType } from 'react';

declare global {
    interface Window {
        __lastRenderedComponent: string;
    }
    interface Performance {
        memory?: {
            usedJSHeapSize: number;
            totalJSHeapSize: number;
            jsHeapSizeLimit: number;
        };
    }
}

// Track component render times and memory usage
export function instrumentComponent<P extends object>(
  Component: ComponentType<P>,
  componentName: string
): ComponentType<P> {
  return function InstrumentedComponent(props: P) {
    useEffect(() => {
      // Update last rendered component
      if (window.__lastRenderedComponent !== componentName) {
        window.__lastRenderedComponent = componentName;
      }
      
      // Log render with memory info if available
      if (process.env.NODE_ENV !== 'production') {
        const memoryInfo = window.performance?.memory ? {
          usedJSHeapSize: Math.round(window.performance.memory.usedJSHeapSize / (1024 * 1024)) + 'MB',
          totalJSHeapSize: Math.round(window.performance.memory.totalJSHeapSize / (1024 * 1024)) + 'MB'
        } : 'Memory API not available';
        
        console.debug(`[RENDER] ${componentName}`, { memoryInfo });
      }
      
      return () => {
        // Track unmounts to help identify potential memory leaks
        if (process.env.NODE_ENV !== 'production') {
          console.debug(`[UNMOUNT] ${componentName}`);
        }
      };
    });
    
    // Wrap render in try/catch to identify render errors
    try {
      return <Component {...props} />;
    } catch (error) {
      console.error(`Error rendering ${componentName}:`, error);
      return <div>Error rendering {componentName}</div>;
    }
  };
}
