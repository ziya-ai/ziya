"""
General application configuration for Ziya.

This module contains application-wide settings that are not specific to models or shell commands.
"""
import os

# Migration feature flag
USE_DIRECT_STREAMING = os.getenv('ZIYA_USE_DIRECT_STREAMING', 'false').lower() == 'true'

# Server configuration
DEFAULT_PORT = 6969
