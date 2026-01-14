"""
General application configuration for Ziya.

This module contains application-wide settings that are not specific to models or shell commands.
"""
import os

# Migration feature flag
USE_DIRECT_STREAMING = os.getenv('ZIYA_USE_DIRECT_STREAMING', 'false').lower() == 'true'

# Server configuration
DEFAULT_PORT = 6969

# Diff validation settings
ENABLE_DIFF_VALIDATION = os.getenv('ZIYA_ENABLE_DIFF_VALIDATION', 'true').lower() == 'true'
AUTO_REGENERATE_INVALID_DIFFS = os.getenv('ZIYA_AUTO_REGENERATE_INVALID_DIFFS', 'true').lower() == 'true'
AUTO_ENHANCE_CONTEXT_ON_VALIDATION_FAILURE = os.getenv('ZIYA_AUTO_ENHANCE_CONTEXT_ON_VALIDATION_FAILURE', 'true').lower() == 'true'
