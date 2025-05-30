import { useEffect, useRef, useCallback } from 'react';

interface PerformanceMonitorProps {
  componentName: string;
  enabled?: boolean;
}

export const PerformanceMonitor: React.FC<PerformanceMonitorProps> = ({ 
  componentName, 
  enabled = process.env.NODE_ENV === 'development' 
}) => {
  const renderCountRef = useRef(0);
  const lastRenderTimeRef = useRef(Date.now());
  
  useEffect(() => {
    if (!enabled) return;
    
    renderCountRef.current++;
    const now = Date.now();
    const timeSinceLastRender = now - lastRenderTimeRef.current;
    
    if (timeSinceLastRender < 16) { // Less than 60fps
      console.warn(`🐌 ${componentName} rendered ${renderCountRef.current} times, last render was ${timeSinceLastRender}ms ago (potential performance issue)`);
    } else if (renderCountRef.current % 10 === 0) {
      console.log(`📊 ${componentName} render count: ${renderCountRef.current}`);
    }
    
    lastRenderTimeRef.current = now;
  });
  
  return null;
};

// Hook version for easier use
export const usePerformanceMonitor = (componentName: string, enabled = process.env.NODE_ENV === 'development') => {
  const renderCountRef = useRef(0);
  const lastRenderTimeRef = useRef(Date.now());
  
  if (enabled) {
    renderCountRef.current++;
    const now = Date.now();
    const timeSinceLastRender = now - lastRenderTimeRef.current;
    
    if (timeSinceLastRender < 16 && renderCountRef.current > 1) {
      console.warn(`🐌 ${componentName} frequent re-renders detected: ${timeSinceLastRender}ms between renders`);
    }
    lastRenderTimeRef.current = now;
  }
};

// Specialized hook for monitoring input performance
export const useInputPerformanceMonitor = (componentName: string) => {
  const inputMetrics = useRef({
    totalInputs: 0,
    slowInputs: 0,
    averageTime: 0,
    maxTime: 0,
    lastReportTime: Date.now()
  });

  const measureInputPerformance = useCallback((inputFn: () => void) => {
    const start = performance.now();
    inputFn();
    const duration = performance.now() - start;
    
    const metrics = inputMetrics.current;
    metrics.totalInputs++;
    metrics.averageTime = (metrics.averageTime * (metrics.totalInputs - 1) + duration) / metrics.totalInputs;
    metrics.maxTime = Math.max(metrics.maxTime, duration);
    
    if (duration > 5) {
      metrics.slowInputs++;
      console.warn(`🐌 ${componentName} slow input: ${duration.toFixed(2)}ms`);
    }
    
    // Report metrics every 50 inputs or every 10 seconds
    const now = Date.now();
    if (metrics.totalInputs % 50 === 0 || now - metrics.lastReportTime > 10000) {
      console.log(`📊 ${componentName} Input Performance:`, {
        totalInputs: metrics.totalInputs,
        slowInputs: metrics.slowInputs,
        slowPercentage: ((metrics.slowInputs / metrics.totalInputs) * 100).toFixed(1) + '%',
        averageTime: metrics.averageTime.toFixed(2) + 'ms',
        maxTime: metrics.maxTime.toFixed(2) + 'ms'
      });
      metrics.lastReportTime = now;
    }
    
    return duration;
  }, [componentName]);

  return measureInputPerformance;
};