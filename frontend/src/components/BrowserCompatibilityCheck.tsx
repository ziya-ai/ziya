import React from 'react';
import { Alert, Button } from 'antd';
import { WarningOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { getBrowserInfo } from '../utils/browserUtils';
import { useTheme } from '../context/ThemeContext';

interface BrowserCompatibilityCheckProps {
  feature?: string; // Specific feature name for contextual warnings
  showForUnsupported?: boolean; // Only show for unsupported browsers
  compact?: boolean; // Show compact version
}

export const BrowserCompatibilityCheck: React.FC<BrowserCompatibilityCheckProps> = ({
  feature,
  showForUnsupported = true,
  compact = false
}) => {
  const { isDarkMode } = useTheme();
  const browserInfo = getBrowserInfo();

  // Only show warning for unsupported browsers if showForUnsupported is true
  if (showForUnsupported && browserInfo.isSupported) {
    return null;
  }

  // Don't show anything for supported browsers unless specifically requested
  if (!showForUnsupported && browserInfo.isSupported) {
    return null;
  }

  const getFeatureMessage = () => {
    if (feature) {
      return `${feature} may not work correctly in ${browserInfo.name}.`;
    }
    return `This application may not work correctly in ${browserInfo.name}.`;
  };

  const getRecommendation = () => {
    return 'Please consider switching to Chrome, Edge, Firefox, or another modern browser for the best experience.';
  };

  if (compact) {
    return (
      <div style={{
        backgroundColor: isDarkMode ? '#2b2111' : '#fffbe6',
        border: `1px solid ${isDarkMode ? '#d4b106' : '#d4b106'}`,
        borderRadius: '4px',
        padding: '8px 12px',
        margin: '4px 0',
        fontSize: '12px',
        color: isDarkMode ? '#faad14' : '#d46b08'
      }}>
        <InfoCircleOutlined style={{ marginRight: '4px' }} />
        {getFeatureMessage()}
      </div>
    );
  }

  return (
    <Alert
      message={`${browserInfo.name} Compatibility Warning`}
      description={`${getFeatureMessage()} ${getRecommendation()}`}
      type="warning"
      icon={<WarningOutlined />}
      showIcon
      style={{ margin: '8px 0' }}
    />
  );
};
