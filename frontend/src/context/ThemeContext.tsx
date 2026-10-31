import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { theme, ThemeConfig } from 'antd';

// MUI theme imports
import { ThemeProvider as MUIThemeProvider, createTheme } from '@mui/material/styles';
import { initAppearance } from '../utils/appearance';

interface ThemeContextType {
    isDarkMode: boolean;
    toggleTheme: () => void;
    setTheme: (mode: 'dark' | 'light') => void;
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
                main: isDarkMode ? '#2dd4bf' : '#0d9488',
            },
            secondary: {
                main: isDarkMode ? '#49aa19' : '#52c41a',
            },
            background: {
                default: isDarkMode ? '#1c1f24' : '#f7f6f3',
                paper: isDarkMode ? '#252930' : '#fcfbf9',
            },
            text: {
                primary: isDarkMode ? '#e8eaed' : '#1a1b1e',
                secondary: isDarkMode ? '#9aa0a8' : '#72736f',
            },
            error: {
                main: isDarkMode ? '#ff4d4f' : '#f5222d',
            }
        },
    });

    useEffect(() => {
        initAppearance();
        const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
        const handleChange = (e: MediaQueryListEvent) => {
            if (localStorage.getItem(THEME_KEY) === null) {
                setIsDarkMode(e.matches);
            }
        };
        mediaQuery.addEventListener('change', handleChange);

        localStorage.setItem(THEME_KEY, JSON.stringify(isDarkMode));
        // Body background comes from theme-tokens.css (--z-canvas); an inline
        // colour here would override the paper-tone preference.
        document.body.classList.toggle('dark', isDarkMode);
        document.body.style.removeProperty('background-color');

        return () => {
            mediaQuery.removeEventListener('change', handleChange);
        };
    }, [isDarkMode]);

    const toggleTheme = () => {
        setIsDarkMode(!isDarkMode);
    };

    const setTheme = (mode: 'dark' | 'light') => {
        setIsDarkMode(mode === 'dark');
    };

    const value = {
        isDarkMode,
        toggleTheme,
        setTheme,
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
