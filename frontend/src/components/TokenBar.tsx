/**
 * TokenBar - Visual token usage indicator
 */
import React from 'react';

interface TokenSegment {
  color: string;
  tokens: number;
}

interface TokenBarProps {
  used: number;
  limit: number;
  segments?: TokenSegment[];
}

export const TokenBar: React.FC<TokenBarProps> = ({ used, limit, segments }) => {
  const percentage = Math.min(100, (used / limit) * 100);
  
  // If we have segments, show them proportionally
  if (segments && segments.length > 0) {
    const total = segments.reduce((sum, seg) => sum + seg.tokens, 0);
    
    return (
      <div style={{ 
        height: '6px', 
        background: '#252525', 
        borderRadius: '3px', 
        overflow: 'hidden',
        display: 'flex'
      }}>
        {segments.map((seg, idx) => {
          const segmentPercentage = (seg.tokens / limit) * 100;
          return (
            <div
              key={idx}
              style={{
                width: `${segmentPercentage}%`,
                height: '100%',
                background: seg.color,
              }}
            />
          );
        })}
      </div>
    );
  }
  
  // Simple single-color bar
  return (
    <div style={{ height: '6px', background: '#252525', borderRadius: '3px', overflow: 'hidden' }}>
      <div style={{ 
        height: '100%', 
        width: `${percentage}%`, 
        background: 'linear-gradient(90deg, #2563eb, #7c3aed)',
        borderRadius: '3px',
        transition: 'width 0.3s ease'
      }} />
    </div>
  );
};
