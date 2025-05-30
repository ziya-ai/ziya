import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { theme, ThemeConfig } from 'antd';

// MUI theme imports
import { ThemeProvider as MUIThemeProvider, createTheme } from '@mui/material/styles';

interface ThemeContextType {
    isDarkMode: boolean;
    toggleTheme: () => void;
    themeAlgorithm: ThemeConfig['algorithm'];
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

const THEME_KEY = 'ZIYA_THEME_PREFERENCE';

const getInitialTheme = () => {
    const savedTheme = localStorage.getItem(THEME_KEY);
    if (savedTheme !== null) {
        return JSON.parse(savedTheme);
    }
    // Check system preference
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
};

export const ThemeProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const [isDarkMode, setIsDarkMode] = useState(getInitialTheme);

    const muiTheme = createTheme({
        palette: {
            mode: isDarkMode ? 'dark' : 'light',
            primary: {
                main: isDarkMode ? '#177ddc' : '#1890ff',
            },
            secondary: {
                main: isDarkMode ? '#49aa19' : '#52c41a',
            },
            background: {
                default: isDarkMode ? '#141414' : '#ffffff',
                paper: isDarkMode ? '#1f1f1f' : '#ffffff',
            },
            text: {
                primary: isDarkMode ? '#ffffff' : '#000000',
                secondary: isDarkMode ? '#a0a0a0' : '#666666',
            },
            error: {
                main: isDarkMode ? '#ff4d4f' : '#f5222d',
            }
        },
    });

    useEffect(() => {
        const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
        const handleChange = (e: MediaQueryListEvent) => {
            if (localStorage.getItem(THEME_KEY) === null) {
                setIsDarkMode(e.matches);
            }
        };
        mediaQuery.addEventListener('change', handleChange);

        localStorage.setItem(THEME_KEY, JSON.stringify(isDarkMode));
        // Update body background color
        document.body.classList.toggle('dark', isDarkMode);
        document.body.style.backgroundColor = isDarkMode ? '#141414' : '#f5f5f5';

        return () => {
            mediaQuery.removeEventListener('change', handleChange);
        };
    }, [isDarkMode]);

    const toggleTheme = () => {
        setIsDarkMode(!isDarkMode);
    };

    const value = {
        isDarkMode,
        toggleTheme,
        themeAlgorithm: isDarkMode ? theme.darkAlgorithm : theme.defaultAlgorithm,
    };

    return <ThemeContext.Provider value={value}>
        <MUIThemeProvider theme={muiTheme}>{children}</MUIThemeProvider>
    </ThemeContext.Provider>;
};

export const useTheme = () => {
    const context = useContext(ThemeContext);
    if (context === undefined) {
        throw new Error('useTheme must be used within a ThemeProvider');
    }
    return context;
};
