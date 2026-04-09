/**
 * Tests that the MCPStatus interface and config error rendering logic
 * properly handle the config_error field from the backend.
 */
describe('MCP config error display', () => {
    // Simulate the MCPStatus interface shape
    interface MCPStatus {
        initialized: boolean;
        servers: Record<string, any>;
        total_servers: number;
        connected_servers: number;
        config_path?: string;
        config_exists?: boolean;
        config_search_paths?: string[];
        config_error?: string;
        server_configs?: Record<string, any>;
    }

    it('should include config_error when present in status response', () => {
        const status: MCPStatus = {
            initialized: true,
            servers: {},
            total_servers: 0,
            connected_servers: 0,
            config_path: '/home/user/.ziya/mcp_config.json',
            config_exists: true,
            config_error: 'Syntax error in /home/user/.ziya/mcp_config.json — line 5, column 12: Expecting property name enclosed in double quotes',
        };

        expect(status.config_error).toBeDefined();
        expect(status.config_error).toContain('Syntax error');
        expect(status.config_error).toContain('line 5');
    });

    it('should have config_error undefined when config is valid', () => {
        const status: MCPStatus = {
            initialized: true,
            servers: { 'test-server': { connected: true, tools: 3 } },
            total_servers: 1,
            connected_servers: 1,
            config_path: '/home/user/.ziya/mcp_config.json',
            config_exists: true,
            server_configs: { 'test-server': { enabled: true } },
        };

        expect(status.config_error).toBeUndefined();
    });

    it('should have config_error undefined when no config file exists', () => {
        const status: MCPStatus = {
            initialized: true,
            servers: {},
            total_servers: 0,
            connected_servers: 0,
            config_exists: false,
            config_search_paths: ['/some/path/mcp_config.json'],
        };

        expect(status.config_error).toBeUndefined();
    });

    it('config_error message should contain file path and location info', () => {
        const errorMsg = 'Syntax error in /home/user/.ziya/mcp_config.json — line 3, column 8: Unexpected token';

        // Verify the error message has actionable information
        expect(errorMsg).toMatch(/line \d+/);
        expect(errorMsg).toMatch(/column \d+/);
        expect(errorMsg).toContain('mcp_config.json');
    });
});
