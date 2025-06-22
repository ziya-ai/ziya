import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useTheme } from '../context/ThemeContext';

interface PanelResizerProps {
  onResize: (newWidth: number) => void;
  isPanelCollapsed: boolean;
}

const PanelResizer: React.FC<PanelResizerProps> = ({ onResize, isPanelCollapsed }) => {
  const { isDarkMode } = useTheme();
  const [isDragging, setIsDragging] = useState(false);
  const resizerRef = useRef<HTMLDivElement>(null);
  const lastUpdateTime = useRef<number>(0);
  const animationFrameId = useRef<number | null>(null);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };

  // Throttled resize function using requestAnimationFrame
  const throttledResize = useCallback((newWidth: number) => {
    if (animationFrameId.current) {
      cancelAnimationFrame(animationFrameId.current);
    }

    animationFrameId.current = requestAnimationFrame(() => {
      const now = performance.now();
      // Throttle to ~60fps (16ms between updates)
      if (now - lastUpdateTime.current >= 16) {
        onResize(newWidth);
        lastUpdateTime.current = now;
      }
      animationFrameId.current = null;
    });
  }, [onResize]);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;

      // Validate and sanitize the width value
      const validateWidth = (width: number): number => {
        // Minimum width
        const minWidth = 200;
        // Maximum width (80% of window or 600px, whichever is larger)
        const maxWidth = Math.max(600, window.innerWidth * 0.8);
        // Ensure width is positive and within constraints
        return Math.max(minWidth, Math.min(maxWidth, Math.abs(width)));
      };

      // Calculate and validate new width
      const newWidth = validateWidth(e.clientX);

      throttledResize(newWidth);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      // Clean up any pending animation frame
      if (animationFrameId.current) {
        cancelAnimationFrame(animationFrameId.current);
        animationFrameId.current = null;
      }
    };
  }, [isDragging, throttledResize]);

  if (isPanelCollapsed) return null;

  return (
    <div
      ref={resizerRef}
      className={`panel-resizer ${isDragging ? 'dragging' : ''}`}
      onMouseDown={handleMouseDown}
      style={{
        position: 'fixed',
        left: 'var(--folder-panel-width)',
        top: 'var(--header-height)',
        bottom: 0,
        width: '10px',
        marginLeft: '-5px', // Center the handle on the border
        cursor: 'col-resize',
        zIndex: 100,
        backgroundColor: isDragging ? (isDarkMode ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.2)') : 'transparent',
        pointerEvents: isPanelCollapsed ? 'none' : 'auto',
        transition: isDragging ? 'none' : 'background-color 0.2s, left 0.5s ease-in-out'
      }}
    />
  );
};

export default PanelResizer;
