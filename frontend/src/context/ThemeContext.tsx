import React, {createContext, useContext, useState, useEffect} from 'react';
import { ConfigProvider, theme } from 'antd';

interface ThemeContextType {
  isDarkMode: boolean;
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export const useTheme = () => {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider');
  }
  return context;
};

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isDarkMode, setIsDarkMode] = useState(() => {
    return localStorage.getItem('ZIYA_IS_DARK_MODE') === 'true';
});

useEffect(() => {
  document.documentElement.classList.toggle('light-mode', !isDarkMode);
  document.documentElement.classList.toggle('dark-mode', isDarkMode);
  localStorage.setItem('ZIYA_IS_DARK_MODE', isDarkMode.toString());
}, [isDarkMode]);

const toggleTheme = () => {
  setIsDarkMode(!isDarkMode);
};

return (
  <ThemeContext.Provider value={{ isDarkMode, toggleTheme }}>
    <ConfigProvider
      theme={{
        algorithm: isDarkMode ? theme.darkAlgorithm : theme.defaultAlgorithm,
      }}>
      {children}
    </ConfigProvider>
  </ThemeContext.Provider>
);
};