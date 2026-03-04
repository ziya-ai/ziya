import React, { useState, useCallback, useRef } from 'react';
import { ConversationGraphView } from './GraphView';
import './GraphPanel.css';

interface Props {
  projectId: string;
  chatId: string;
  onClose: () => void;
}

export function GraphPanel({ projectId, chatId, onClose }: Props) {
  const [width, setWidth] = useState(500);
  const dragging = useRef(false);

  const onResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    const startX = e.clientX;
    const startW = width;

    const move = (ev: MouseEvent) => {
      const newW = Math.max(300, Math.min(800, startW + (startX - ev.clientX)));
      setWidth(newW);
    };
    const up = () => {
      dragging.current = false;
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  }, [width]);

  return (
    <>
      {/* Blur overlay — click to close */}
      <div className="cgp-overlay" onClick={onClose} />

      {/* Panel */}
      <div className="cgp-panel cgp-open" style={{ width }}>
        <div className="cgp-resize" onMouseDown={onResizeStart} />
        <button className="cgp-close-btn" onClick={onClose} title="Close graph panel">✕</button>
        <div className="cgp-inner">
          <ConversationGraphView
            projectId={projectId}
            chatId={chatId}
          />
        </div>
      </div>
    </>
  );
}

export default GraphPanel;
