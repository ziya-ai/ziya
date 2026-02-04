class MCPManager:
    def get_all_tools(self):
        """Get all tools."""
        tools = []
        
        # Update cache
        self._tools_cache = tools
        self._tools_cache_timestamp = current_time
        logger.debug(f"MCP_MANAGER.get_all_tools: Cached {len(tools)} tools for {self._tools_cache_ttl}s")
        
        # Add tools from workspace-scoped instances
        for server_name in self.workspace_scoped_clients:
            pass
        
        return tools
