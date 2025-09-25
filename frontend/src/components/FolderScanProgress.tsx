import React from 'react';
import { Progress, Spin, Typography, Button, Alert } from 'antd';
import { LoadingOutlined, StopOutlined } from '@ant-design/icons';
import { useFolderContext } from '../context/FolderContext';
import { useTheme } from '../context/ThemeContext';

const { Text } = Typography;

interface FolderScanProgressProps {
  onCancel?: () => void;
}

export const FolderScanProgress: React.FC<FolderScanProgressProps> = ({ onCancel }) => {
  const { isScanning, scanProgress, scanError } = useFolderContext();
  const { isDarkMode } = useTheme();

  if (!isScanning && !scanError) {
    return null;
  }

  if (scanError) {
    return (
      <div style={{ 
        padding: '12px', 
        borderBottom: `1px solid ${isDarkMode ? '#303030' : '#e8e8e8'}`,
        backgroundColor: isDarkMode ? '#1f1f1f' : '#fff'
      }}>
        <Alert
          message="Folder Scan Error"
          description={scanError}
          type="error"
          showIcon
          style={{ marginBottom: '8px' }}
        />
        <Text type="secondary" style={{ fontSize: '12px' }}>
          You can still use Ziya with previously loaded files or try refreshing the page.
        </Text>
      </div>
    );
  }

  const formatTime = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`;
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs}s`;
  };

  const getProgressColor = (elapsed: number): string => {
    if (elapsed < 15) return '#52c41a'; // Green
    if (elapsed < 30) return '#faad14'; // Orange
    return '#ff4d4f'; // Red
  };

  return (
    <div style={{ 
      padding: '12px', 
      borderBottom: `1px solid ${isDarkMode ? '#303030' : '#e8e8e8'}`,
      backgroundColor: isDarkMode ? '#1f1f1f' : '#fff'
    }}>
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'space-between',
        marginBottom: '8px'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Spin 
            indicator={<LoadingOutlined style={{ fontSize: 14 }} spin />} 
            size="small" 
          />
          <Text strong style={{ fontSize: '12px' }}>
            Scanning Folder Structure
          </Text>
        </div>
        {onCancel && (
          <Button 
            size="small" 
            type="text" 
            icon={<StopOutlined />}
            onClick={onCancel}
            style={{ 
              color: '#ff4d4f',
              fontSize: '11px',
              height: '24px',
              padding: '0 8px'
            }}
          >
            Cancel
          </Button>
        )}
      </div>
      
      {scanProgress && (
        <>
          <div style={{ 
            display: 'flex', 
            justifyContent: 'space-between', 
            fontSize: '11px',
            color: isDarkMode ? '#888' : '#666',
            marginBottom: '4px'
          }}>
            <span>{scanProgress.directories} directories</span>
            <span>{scanProgress.files} files</span>
            <span style={{ color: getProgressColor(scanProgress.elapsed) }}>
              {formatTime(scanProgress.elapsed)}
            </span>
          </div>
          
          <Progress
            percent={Math.min(100, (scanProgress.elapsed / 45) * 100)}
            size="small"
            status={scanProgress.elapsed > 30 ? 'exception' : 'active'}
            showInfo={false}
            strokeColor={getProgressColor(scanProgress.elapsed)}
          />
        </>
      )}
    </div>
  );
};
