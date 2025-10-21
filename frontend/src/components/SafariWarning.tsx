import React, { useState, useEffect } from 'react';
import { Alert, Button, Modal, Typography } from 'antd';
import { WarningOutlined, CloseOutlined } from '@ant-design/icons';
import { useTheme } from '../context/ThemeContext';

const { Title, Text, Paragraph } = Typography;

interface SafariWarningProps {
  onDismiss?: () => void;
  persistent?: boolean; // If true, warning cannot be permanently dismissed
}

const RECOMMENDED_BROWSERS = [
  { name: 'Google Chrome', url: 'https://www.google.com/chrome/' },
  { name: 'Microsoft Edge', url: 'https://www.microsoft.com/edge' },
  { name: 'Brave Browser', url: 'https://brave.com/' },
  { name: 'Opera', url: 'https://www.opera.com/' },
  { name: 'Vivaldi', url: 'https://vivaldi.com/' },
  { name: 'Mozilla Firefox', url: 'https://www.mozilla.org/firefox/' },
];

const detectSafari = (): boolean => {
  if (typeof navigator === 'undefined') return false;
  return /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
};

export const SafariWarning: React.FC<SafariWarningProps> = ({ 
  onDismiss, 
  persistent = false 
}) => {
  const { isDarkMode } = useTheme();
  const [isVisible, setIsVisible] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    const isSafari = detectSafari();
    
    // Debug logging
    console.log('SafariWarning: Browser detection:', {
      userAgent: navigator.userAgent,
      isSafari,
      persistent
    });
    
    if (isSafari) {
      // Check if user has permanently dismissed the warning
      const permanentlyDismissed = !persistent && localStorage.getItem('safari-warning-dismissed') === 'true';
      console.log('SafariWarning: Dismissal check:', { permanentlyDismissed, persistent });
      
      if (!permanentlyDismissed) {
        console.log('SafariWarning: Setting visible to true');
        setIsVisible(true);
      }
    }
  }, [persistent]);

  const handleDismiss = (permanent: boolean = false) => {
    setIsVisible(false);
    setDismissed(true);
    
    if (permanent && !persistent) {
      localStorage.setItem('safari-warning-dismissed', 'true');
    }
    
    onDismiss?.();
  };

  const handleShowDetails = () => {
    setShowDetails(true);
  };

  if (!isVisible || dismissed) {
    return null;
  }

  return (
    <>
      <Alert
        message="Safari Browser Detected"
        description={
          <div>
            <Paragraph style={{ margin: '8px 0' }}>
              This application has limited Safari support and may not function correctly. 
              For the best experience, please switch to a supported browser.
            </Paragraph>
            <div style={{ marginTop: '12px' }}>
              <Button 
                type="primary" 
                size="small" 
                onClick={handleShowDetails}
                style={{ marginRight: '8px' }}
              >
                View Recommended Browsers
              </Button>
              {!persistent && (
                <Button 
                  size="small" 
                  onClick={() => handleDismiss(false)}
                  style={{ marginRight: '8px' }}
                >
                  Dismiss for Now
                </Button>
              )}
              {!persistent && (
                <Button 
                  size="small" 
                  type="text"
                  onClick={() => handleDismiss(true)}
                >
                  Don't Show Again
                </Button>
              )}
            </div>
          </div>
        }
        type="warning"
        icon={<WarningOutlined />}
        showIcon
        closable={persistent ? false : true}
        onClose={() => handleDismiss(false)}
        style={{
          margin: '16px 0',
          backgroundColor: isDarkMode ? '#2b2111' : '#fffbe6',
          border: `1px solid ${isDarkMode ? '#d4b106' : '#d4b106'}`,
          borderRadius: '6px'
        }}
      />

      <Modal
        title={
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <WarningOutlined style={{ color: '#faad14', marginRight: '8px' }} />
            Recommended Browsers
          </div>
        }
        open={showDetails}
        onCancel={() => setShowDetails(false)}
        footer={[
          <Button key="close" onClick={() => setShowDetails(false)}>
            Close
          </Button>
        ]}
        width={600}
      >
        <div style={{ padding: '8px 0' }}>
          <Paragraph>
            For optimal performance and full feature support, we recommend using any of these browsers:
          </Paragraph>
          
          <Title level={5} style={{ marginTop: '20px' }}>Chromium-Based Browsers (Recommended)</Title>
          {RECOMMENDED_BROWSERS.slice(0, 5).map((browser) => (
            <div key={browser.name} style={{ marginBottom: '8px' }}>
              <a 
                href={browser.url} 
                target="_blank" 
                rel="noopener noreferrer"
                style={{ textDecoration: 'none', fontSize: '14px' }}
              >
                🌐 {browser.name}
              </a>
            </div>
          ))}
          
          <Title level={5} style={{ marginTop: '20px' }}>Alternative Browser</Title>
          <div style={{ marginBottom: '8px' }}>
            <a 
              href={RECOMMENDED_BROWSERS[5].url} 
              target="_blank" 
              rel="noopener noreferrer"
              style={{ textDecoration: 'none', fontSize: '14px' }}
            >
              🦊 {RECOMMENDED_BROWSERS[5].name}
            </a>
          </div>
          
          <Paragraph style={{ marginTop: '20px', fontSize: '13px', color: isDarkMode ? '#8b949e' : '#656d76' }}>
            <strong>Why not Safari?</strong> Safari has known compatibility issues with advanced visualization features, 
            WebGL rendering, and modern JavaScript APIs used in this application. These issues can cause 
            performance problems, rendering failures, and unexpected behavior.
          </Paragraph>
        </div>
      </Modal>
    </>
  );
};

// Utility function to check if current browser is Safari
export const isSafari = (): boolean => detectSafari();

// Hook for components that need Safari detection
export const useSafariDetection = () => {
  const [safariDetected, setSafariDetected] = useState(false);

  useEffect(() => {
    setSafariDetected(detectSafari());
  }, []);

  return safariDetected;
};
