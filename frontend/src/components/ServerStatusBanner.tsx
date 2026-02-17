/**
 * ServerStatusBanner — fixed banner shown when the backend is unreachable.
 * Automatically hides once connectivity is restored.
 */
import React from 'react';
import { useServerStatus } from '../context/ServerStatusContext';
import { useTheme } from '../context/ThemeContext';
import { DisconnectOutlined } from '@ant-design/icons';

export const ServerStatusBanner: React.FC = () => {
  const { isServerReachable } = useServerStatus();
  const { isDarkMode } = useTheme();

  if (isServerReachable) return null;

  return (
    <div
      role="alert"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: '10px',
        padding: '10px 20px',
        background: isDarkMode
          ? 'linear-gradient(90deg, #4a1a1a 0%, #5c1f1f 50%, #4a1a1a 100%)'
          : 'linear-gradient(90deg, #fff1f0 0%, #ffccc7 50%, #fff1f0 100%)',
        borderBottom: isDarkMode
          ? '2px solid #ff4d4f'
          : '2px solid #ff7875',
        color: isDarkMode ? '#ff9c9c' : '#a8071a',
        fontSize: '14px',
        fontWeight: 500,
        boxShadow: isDarkMode
          ? '0 2px 12px rgba(255, 77, 79, 0.3)'
          : '0 2px 12px rgba(255, 77, 79, 0.15)',
        animation: 'server-banner-slide-in 0.3s ease-out',
      }}
    >
      <DisconnectOutlined
        style={{
          fontSize: '18px',
          animation: 'server-banner-pulse 2s ease-in-out infinite',
        }}
      />
      <span>
        Server is unreachable. Check that the Ziya backend is running, then this
        banner will clear automatically.
      </span>

      {/* Scoped keyframe animations */}
      <style>{`
        @keyframes server-banner-slide-in {
          from {
            transform: translateY(-100%);
            opacity: 0;
          }
          to {
            transform: translateY(0);
            opacity: 1;
          }
        }
        @keyframes server-banner-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
};
