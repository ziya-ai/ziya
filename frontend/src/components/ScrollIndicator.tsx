import React from 'react';
import { Button, Tooltip } from 'antd';
import { DownOutlined, ArrowDownOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';

interface ScrollIndicatorProps {
    visible: boolean;
    isCompleted: boolean; // false = streaming (yellow), true = completed (green)
    onClick: () => void;
    isTopToBottom: boolean;
}

export const ScrollIndicator: React.FC<ScrollIndicatorProps> = ({
    visible,
    isCompleted,
    onClick,
    isTopToBottom
}) => {
    const { isDarkMode } = useTheme();

    if (!visible) return null;

    const position = isTopToBottom ? {
        bottom: '80px',
        right: '24px'
    } : {
        top: '80px',
        right: '24px'
    };

    const backgroundColor = isCompleted
        ? (isDarkMode ? '#237804' : '#52c41a')  // Green for completed
        : (isDarkMode ? '#ad6800' : '#faad14'); // Yellow for streaming

    const icon = isCompleted
        ? <ArrowDownOutlined style={{ 
            fontSize: '20px', 
            transform: isTopToBottom ? 'none' : 'rotate(180deg)' 
          }} />
        : <DownOutlined style={{ fontSize: '20px', transform: isTopToBottom ? 'none' : 'rotate(180deg)' }} />;

    const tooltipText = isCompleted
        ? 'New content available - click to scroll'
        : 'New content streaming - click to follow';

    return (
        <Tooltip title={tooltipText} placement={isTopToBottom ? 'left' : 'left'}>
            <Button
                type="primary"
                shape="circle"
                size="large"
                icon={icon}
                onClick={onClick}
                style={{
                    position: 'fixed',
                    ...position,
                    width: '56px',
                    height: '56px',
                    backgroundColor,
                    borderColor: backgroundColor,
                    boxShadow: isDarkMode
                        ? '0 4px 12px rgba(0, 0, 0, 0.5)'
                        : '0 4px 12px rgba(0, 0, 0, 0.15)',
                    zIndex: 1000,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    animation: 'slideInRight 0.3s ease-out',
                    cursor: 'pointer',
                    transition: 'all 0.3s ease'
                }}
                onMouseEnter={(e) => {
                    e.currentTarget.style.transform = 'scale(1.1)';
                }}
                onMouseLeave={(e) => {
                    e.currentTarget.style.transform = 'scale(1.0)';
                }}
            />
        </Tooltip>
    );
};

// Add keyframe animation to document head if not already present
if (typeof document !== 'undefined' && !document.getElementById('scroll-indicator-animations')) {
    const style = document.createElement('style');
    style.id = 'scroll-indicator-animations';
    style.textContent = `
        @keyframes slideInRight {
            from {
                opacity: 0;
                transform: translateX(100px);
            }
            to {
                opacity: 1;
                transform: translateX(0);
            }
        }
        
        @keyframes pulse {
            0%, 100% {
                opacity: 1;
            }
            50% {
                opacity: 0.7;
            }
        }
        
        .scroll-indicator-button {
            animation: pulse 2s infinite;
        }
    `;
    document.head.appendChild(style);
}
