import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { AppConfig, DEFAULT_CONFIG } from '../types/config';
import { fetchConfig } from '../apis/chatApi';

interface ConfigContextType {
    config: AppConfig;
    isLoading: boolean;
    isEphemeralMode: boolean;
}

const ConfigContext = createContext<ConfigContextType | undefined>(undefined);

interface ConfigProviderProps {
    children: ReactNode;
}

export function ConfigProvider({ children }: ConfigProviderProps) {
    const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        const loadConfig = async () => {
            try {
                const fetchedConfig = await fetchConfig();
                setConfig(fetchedConfig);

                if (fetchedConfig.ephemeralMode) {
                    console.log('🔒 EPHEMERAL MODE: Conversations will not be persisted');
                }
            } catch (error) {
                console.error('Failed to load config:', error);
            } finally {
                setIsLoading(false);
            }
        };
        loadConfig();
    }, []);

    const value: ConfigContextType = {
        config,
        isLoading,
        isEphemeralMode: config.ephemeralMode || false,
    };

    return (
        <ConfigContext.Provider value={value}>
            {children}
        </ConfigContext.Provider>
    );
}

export function useConfig(): ConfigContextType {
    const context = useContext(ConfigContext);
    if (!context) {
        throw new Error('useConfig must be used within a ConfigProvider');
    }
    return context;
}
