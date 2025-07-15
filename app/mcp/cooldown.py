"""
Cooldown manager for MCP tool execution.

This module provides a cooldown mechanism to prevent throttling errors
by limiting the rate of tool executions.
"""

import time
import asyncio
from typing import Dict

from app.utils.logging_utils import logger

class CooldownManager:
    """Manager for tool execution cooldowns."""
    
    def __init__(self):
        """Initialize the cooldown manager."""
        self.cooldowns = {}
        self.last_execution = {}
        self.base_cooldown = 1.0  # Base cooldown in seconds
        self.max_cooldown = 30.0  # Maximum cooldown in seconds
    
    def wait_if_needed(self, tool_name: str) -> float:
        """
        Wait if a cooldown is active for the tool.
        
        Args:
            tool_name: The tool name
            
        Returns:
            The wait time in seconds
        """
        now = time.time()
        
        # Get current cooldown for this tool
        cooldown = self.cooldowns.get(tool_name, self.base_cooldown)
        
        # Check if we need to wait
        if tool_name in self.last_execution:
            elapsed = now - self.last_execution[tool_name]
            if elapsed < cooldown:
                wait_time = cooldown - elapsed
                logger.info(f"🧊 Cooldown: Waiting {wait_time:.2f}s for {tool_name}")
                time.sleep(wait_time)
                return wait_time
        
        # Update last execution time
        self.last_execution[tool_name] = time.time()
        return 0.0
    
    def increase_cooldown(self, tool_name: str):
        """
        Increase the cooldown for a tool.
        
        Args:
            tool_name: The tool name
        """
        current = self.cooldowns.get(tool_name, self.base_cooldown)
        new_cooldown = min(current * 2.0, self.max_cooldown)
        self.cooldowns[tool_name] = new_cooldown
        logger.info(f"🧊 Cooldown: Increased for {tool_name} to {new_cooldown:.2f}s")
    
    def reset_cooldown(self, tool_name: str):
        """
        Reset the cooldown for a tool.
        
        Args:
            tool_name: The tool name
        """
        if tool_name in self.cooldowns:
            del self.cooldowns[tool_name]
        logger.info(f"🧊 Cooldown: Reset for {tool_name}")
    
    def reset_all(self):
        """Reset all cooldowns."""
        self.cooldowns = {}
        logger.info("🧊 Cooldown: Reset all cooldowns")

# Global cooldown manager instance
cooldown_manager = CooldownManager()
