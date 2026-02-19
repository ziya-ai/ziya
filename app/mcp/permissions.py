"""
Manages MCP permission settings for servers and tools.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, Literal, Optional

from app.utils.logging_utils import logger

PermissionLevel = Literal["enabled", "disabled", "ask"]

class MCPPermissionsManager:
    """Manages MCP permission settings."""
    
    def __init__(self):
        self.config_path = Path.home() / ".ziya" / "mcp_permissions.json"
        self.permissions = self._load_permissions()

    def _get_default_permissions(self) -> Dict[str, Any]:
        """Get default permissions structure."""
        return {
            "defaults": {
                "server": "enabled",
                "tool": "enabled"
            },
            "servers": {}
        }

    def _load_permissions(self) -> Dict[str, Any]:
        """Load permissions from file."""
        if not self.config_path.exists():
            return self._get_default_permissions()
        try:
            with open(self.config_path, 'r') as f:
                data = json.load(f)
            if self._migrate_ask_permissions(data):
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.config_path, 'w') as f:
                    json.dump(data, f, indent=2)
                logger.info("Migrated 'ask' permissions to 'enabled' (ask mode removed)")
            return data
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load MCP permissions: {e}")
            return self._get_default_permissions()

    @staticmethod
    def _migrate_ask_permissions(data: Dict[str, Any]) -> bool:
        """Replace any 'ask' permission values with 'enabled'. Returns True if anything changed."""
        changed = False
        for key in ('server', 'tool'):
            if data.get('defaults', {}).get(key) == 'ask':
                data['defaults'][key] = 'enabled'
                changed = True
        for server_info in data.get('servers', {}).values():
            if server_info.get('permission') == 'ask':
                server_info['permission'] = 'enabled'
                changed = True
            for tool_info in server_info.get('tools', {}).values():
                if tool_info.get('permission') == 'ask':
                    tool_info['permission'] = 'enabled'
                    changed = True
        return changed

    def get_permissions(self) -> Dict[str, Any]:
        """Get the current permissions."""
        return self.permissions

    def save_permissions(self, permissions: Dict[str, Any]):
        """Save permissions to file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(permissions, f, indent=2)
        self.permissions = permissions
        
        # Invalidate the secure tools cache to force rebuild with new permissions
        try:
            from app.mcp.enhanced_tools import invalidate_secure_tools_cache
            invalidate_secure_tools_cache()
        except ImportError:
            logger.debug("Could not invalidate secure tools cache - module not available")

    def update_server_permission(self, server_name: str, permission: PermissionLevel):
        """Update permission for a specific server."""
        self.permissions.setdefault("servers", {}).setdefault(server_name, {})["permission"] = permission
        self.save_permissions(self.permissions)

    def update_tool_permission(self, server_name: str, tool_name: str, permission: PermissionLevel):
        """Update permission for a specific tool on a server."""
        server_permissions = self.permissions.setdefault("servers", {}).setdefault(server_name, {})
        server_permissions.setdefault("tools", {})[tool_name] = {"permission": permission}
        self.save_permissions(self.permissions)

# Global instance
_permissions_manager: Optional[MCPPermissionsManager] = None

def get_permissions_manager() -> MCPPermissionsManager:
    """Get the global permissions manager instance."""
    global _permissions_manager
    if _permissions_manager is None:
        _permissions_manager = MCPPermissionsManager()
    return _permissions_manager
