"""
Dynamic Tool Loading System

Detects file types from selected files and dynamically loads appropriate
MCP tools into the tool registry.
"""

import os
from typing import Dict, List, Set, Any, Optional
from pathlib import Path
from app.utils.logging_utils import logger
from app.mcp.tools.pcap_analysis import PCAPAnalysisTool

def check_tool_dependencies(tool_class) -> tuple[bool, list[str]]:
    """
    Check if a tool's dependencies are available.
    
    Returns:
        Tuple of (dependencies_met, list_of_missing_dependencies)
    """
    # Map tool classes to their dependency check functions
    if tool_class == PCAPAnalysisTool:
        try:
            from app.utils.pcap_analyzer import is_pcap_supported
            if is_pcap_supported():
                return (True, [])
            else:
                return (False, ['scapy'])
        except Exception as e:
            logger.debug(f"Error checking PCAP dependencies: {e}")
            return (False, ['scapy'])
    
    # Default: assume dependencies are met
    return (True, [])

def get_tool_friendly_name(tool_name: str) -> str:
    """Get a friendly display name for a tool."""
    if tool_name == "analyze_pcap":
        return "PCAP Network Analyzer"
    else:
        # Default: capitalize and clean up underscores
        return tool_name.replace('_', ' ').title()


class DynamicToolLoader:
    """
    Manages dynamic loading/unloading of MCP tools based on file context.
    """
    
    # File extension to tool class mapping
    FILE_TYPE_TOOLS = {
        '.pcap': [PCAPAnalysisTool],
        '.pcapng': [PCAPAnalysisTool],
    }
    
    def __init__(self):
        self._active_tools: Dict[str, Any] = {}  # tool_name -> tool_instance
        self._active_file_types: Set[str] = set()
        self._tool_triggers: Dict[str, List[str]] = {}  # tool_name -> list of file extensions that triggered it
        
        
    def detect_file_types(self, file_paths: List[str]) -> Set[str]:
        """
        Detect file types from a list of file paths.
        
        Args:
            file_paths: List of file paths
            
        Returns:
            Set of file extensions found
        """
        extensions = set()
        for path in file_paths:
            ext = Path(path).suffix.lower()
            if ext:
                extensions.add(ext)
        return extensions
    
    def load_tools_for_files(self, file_paths: List[str]) -> Dict[str, Any]:
        """
        Load appropriate tools based on selected files.
        
        Args:
            file_paths: List of selected file paths
            
        Returns:
            Dict mapping tool names to tool instances
        """
        detected_types = self.detect_file_types(file_paths)
        logger.info(f"Detected file types: {detected_types}")
        
        newly_loaded = {}
        
        for file_type in detected_types:
            if file_type in self.FILE_TYPE_TOOLS:
                # Load tools for this file type
                for tool_class in self.FILE_TYPE_TOOLS[file_type]:
                    tool_instance = tool_class()
                    tool_name = tool_instance.name
                    
                    # Only add if not already loaded
                    if tool_name not in self._active_tools:
                        self._active_tools[tool_name] = tool_instance
                        self._tool_triggers[tool_name] = [file_type]
                        newly_loaded[tool_name] = tool_instance
                        logger.debug(f"Loaded tool {tool_name} (for {file_type} files)")
                    elif file_type not in self._tool_triggers.get(tool_name, []):
                        # Tool already loaded, but add this trigger if not already tracked
                        if tool_name not in self._tool_triggers:
                            self._tool_triggers[tool_name] = []
                        self._tool_triggers[tool_name].append(file_type)
                        logger.debug(f"Added trigger {file_type} for existing tool {tool_name}")
                    else:
                        logger.debug(f"Tool {tool_name} already loaded")
                        
                self._active_file_types.add(file_type)
        
        return newly_loaded
    
    def unload_tools_for_files(self, file_paths: List[str]) -> List[str]:
        """
        Unload tools that are no longer needed.
        
        Args:
            file_paths: Currently selected file paths
            
        Returns:
            List of unloaded tool names
        """
        current_types = self.detect_file_types(file_paths)
        unloaded = []
        
        # Find file types that are no longer selected
        types_to_remove = self._active_file_types - current_types
        
        for file_type in types_to_remove:
            if file_type in self.FILE_TYPE_TOOLS:
                for tool_class in self.FILE_TYPE_TOOLS[file_type]:
                    tool_name = tool_class().name
                    if tool_name in self._active_tools:
                        del self._active_tools[tool_name]
                        unloaded.append(tool_name)
                        self._tool_triggers.pop(tool_name, None)
                        logger.info(f"🗑️ Unloaded tool {tool_name}")
            
            self._active_file_types.discard(file_type)
                        
            self._active_file_types.discard(file_type)
        
        return unloaded
    
    def get_active_tools(self) -> Dict[str, Any]:
        """Get currently loaded dynamic tools."""
        return self._active_tools.copy()
    
    def get_tool(self, tool_name: str) -> Optional[Any]:
        """Get a specific tool by name."""
        return self._active_tools.get(tool_name)
    
    def get_tool_triggers(self, tool_name: str) -> List[str]:
        """Get the file extensions that triggered a tool to be loaded."""
        return self._tool_triggers.get(tool_name, [])
    
    def get_all_triggers(self) -> Dict[str, List[str]]:
        """Get all tool triggers."""
        return self._tool_triggers.copy()
    
    def get_available_tools_info(self) -> List[Dict[str, Any]]:
        """
        Get information about all available dynamic tools (loaded or not).
        
        Returns:
            List of tool info dictionaries with:
            - name: friendly tool name
            - tool_name: internal tool name
            - triggers: list of file extensions that trigger this tool
            - dependencies_met: whether dependencies are available
            - missing_dependencies: list of missing dependencies
            - is_active: whether the tool is currently loaded
        """
        available_tools = []
        
        # Build reverse mapping: file extension -> list of tool classes
        for file_ext, tool_classes in self.FILE_TYPE_TOOLS.items():
            for tool_class in tool_classes:
                try:
                    tool_instance = tool_class()
                    tool_name = tool_instance.name
                    
                    # Check if already in list (tool might be triggered by multiple extensions)
                    existing = next((t for t in available_tools if t['tool_name'] == tool_name), None)
                    
                    if existing:
                        # Add this trigger to the existing entry
                        if file_ext not in existing['triggers']:
                            existing['triggers'].append(file_ext)
                    else:
                        # Check dependencies
                        deps_met, missing_deps = check_tool_dependencies(tool_class)
                        
                        available_tools.append({
                            'name': get_tool_friendly_name(tool_name),
                            'tool_name': tool_name,
                            'triggers': [file_ext],
                            'dependencies_met': deps_met,
                            'missing_dependencies': missing_deps,
                            'is_active': tool_name in self._active_tools
                        })
                except Exception as e:
                    logger.warning(f"Error getting info for tool class {tool_class}: {e}")
        
        return available_tools
    
    def clear_all_tools(self):
        """Clear all dynamically loaded tools."""
        tool_count = len(self._active_tools)
        self._active_tools.clear()
        self._active_file_types.clear()
        self._tool_triggers.clear()
        logger.info(f"Cleared {tool_count} dynamic tools")


# Global instance
_dynamic_loader = None

def get_dynamic_loader() -> DynamicToolLoader:
    """Get the global dynamic tool loader instance."""
    global _dynamic_loader
    if _dynamic_loader is None:
        _dynamic_loader = DynamicToolLoader()
    return _dynamic_loader
