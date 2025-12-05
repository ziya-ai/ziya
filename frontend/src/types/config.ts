export interface AppConfig {
    theme?: string;
    defaultModel?: string;
    endpoint?: string;
    port?: number;
    mcpEnabled?: boolean;
    version?: string;
    // UI preferences
    showTokenCount?: boolean;
    showModelInHeader?: boolean;
    // Feature flags
    enableVoiceInput?: boolean;
    enableImageUpload?: boolean;
    enableCodeExecution?: boolean;
    // Conversation settings
    maxConversationHistory?: number;
    autoSaveInterval?: number;
    // Privacy/Storage settings
    ephemeralMode?: boolean;
}

export const DEFAULT_CONFIG: AppConfig = {
    theme: 'light',
    defaultModel: 'anthropic.claude-sonnet-4-20250514-v1:0',
    endpoint: 'bedrock',
    port: 7001,
    mcpEnabled: true,
    showTokenCount: true,
    showModelInHeader: true,
    ephemeralMode: false,
}
