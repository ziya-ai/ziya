import React, { memo } from 'react';
import { useTheme } from '../context/ThemeContext';

interface ModelChangeNotificationProps {
  previousModel: string;
  newModel: string;
  changeKey?: string;
  key?: string;
}

const ModelChangeNotification: React.FC<ModelChangeNotificationProps> = memo(({ previousModel, newModel }) => {
  const { isDarkMode } = useTheme();
  
  // Define colors that work well in both light and dark modes
  const styles = {
    container: {
      padding: '12px 16px',
      borderRadius: '8px',
      backgroundColor: isDarkMode ? 'rgba(25, 118, 210, 0.15)' : 'rgba(25, 118, 210, 0.08)',
      border: `1px solid ${isDarkMode ? '#2c6cb0' : '#90caf9'}`,
      marginBottom: '12px',
      marginTop: '4px',
      display: 'flex',
      alignItems: 'center',
      boxShadow: isDarkMode ? '0 2px 8px rgba(0, 0, 0, 0.15)' : '0 1px 3px rgba(0, 0, 0, 0.1)'
    },
    icon: {
      fontSize: '20px',
      marginRight: '12px',
      color: isDarkMode ? '#90caf9' : '#1976d2'
    },
    text: {
      color: isDarkMode ? '#e3f2fd' : '#0d47a1',
      fontWeight: 500,
      fontSize: '14px'
    },
    modelName: {
      fontWeight: 600,
      color: isDarkMode ? '#bbdefb' : '#1565c0'
    }
  };
  
  return (
    <div style={styles.container}>
      <span style={styles.icon}>🔄</span>
      <span style={styles.text}>
        Model changed from <span style={styles.modelName}>{previousModel}</span> to <span style={styles.modelName}>{newModel}</span>
      </span>
    </div>
  );
}, (prevProps, nextProps) => {
  // Only re-render if the models actually change
  return prevProps.previousModel === nextProps.previousModel && 
         prevProps.newModel === nextProps.newModel &&
         prevProps.changeKey === nextProps.changeKey;
});

ModelChangeNotification.displayName = 'ModelChangeNotification';
export default ModelChangeNotification;
