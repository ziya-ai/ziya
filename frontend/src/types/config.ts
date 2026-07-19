export interface McpRegistryPreset {
    id: string;
    label: string;
    tooltip?: string;
    // Exact provider ids to select. Takes precedence over internalOnly.
    providerIds?: string[];
    // When true (and no providerIds given), select every provider whose
    // isInternal flag is set.
    internalOnly?: boolean;
    // When set, also constrain the Support Level filter (e.g. "Supported").
    supportLevel?: string;
}

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
    memoryEnabled?: boolean;
    // Plugin-injected frontend config (from the /api/config merge of
    // provider.get_defaults()['frontend']). Community edition supplies none.
    frontend?: {
        formatters?: string[];
        mcpRegistryPresets?: McpRegistryPreset[];
    };
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
    memoryEnabled: false,
}
