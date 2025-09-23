"""
Configuration settings for diff utilities.

This module provides centralized configuration for diff application parameters
that can be adjusted based on the specific use case or environment.
"""

# Context extraction settings
DEFAULT_SMALL_CONTEXT_SIZE = 20   # For small context extractions (e.g., single line changes)
DEFAULT_MEDIUM_CONTEXT_SIZE = 50  # For medium context extractions (default for most operations)
DEFAULT_LARGE_CONTEXT_SIZE = 100  # For large context extractions (complex changes)
DEFAULT_FULL_CONTEXT_SIZE = 200   # For full context extractions (when maximum context is needed)

# Search radius settings for finding best position
DEFAULT_SEARCH_RADIUS = 50        # Default search radius for position finding
EXTENDED_SEARCH_RADIUS = 100      # Extended search radius for difficult cases
MAXIMUM_SEARCH_RADIUS = 200       # Maximum search radius for very difficult cases

# Confidence thresholds
EXACT_MATCH_THRESHOLD = 1.0       # Threshold for exact matches
HIGH_CONFIDENCE_THRESHOLD = 0.75   # Threshold for high confidence matches
MEDIUM_CONFIDENCE_THRESHOLD = 0.52  # Threshold for medium confidence matches (default for fuzzy matching)
LOW_CONFIDENCE_THRESHOLD = 0.4    # Threshold for low confidence matches (lowered to handle function collision cases)
MINIMUM_CONFIDENCE_THRESHOLD = 0.3 # Minimum threshold to consider a match

# Adaptive context settings
ADAPTIVE_CONTEXT_ENABLED = True   # Whether to use adaptive context sizing
ADAPTIVE_CONTEXT_MIN_LINES = 3    # Minimum number of context lines to use
ADAPTIVE_CONTEXT_MAX_LINES = 10   # Maximum number of context lines to use
ADAPTIVE_CONTEXT_RATIO = 0.2      # Ratio of hunk size to use for context

# Environment variable names for configuration overrides
ENV_PREFIX = "ZIYA_DIFF_"
ENV_SEARCH_RADIUS = f"{ENV_PREFIX}SEARCH_RADIUS"
ENV_CONTEXT_SIZE = f"{ENV_PREFIX}CONTEXT_SIZE"
ENV_CONFIDENCE_THRESHOLD = f"{ENV_PREFIX}CONFIDENCE_THRESHOLD"
ENV_ADAPTIVE_CONTEXT = f"{ENV_PREFIX}ADAPTIVE_CONTEXT"
ENV_MAX_OFFSET = f"{ENV_PREFIX}MAX_OFFSET"  # New environment variable for MAX_OFFSET

import os

def get_config_value(env_var: str, default_value):
    """
    Get a configuration value from environment variable or use default.
    
    Args:
        env_var: The environment variable name
        default_value: The default value to use if env var is not set
        
    Returns:
        The configuration value
    """
    value = os.environ.get(env_var)
    if value is None:
        return default_value
    
    # Try to convert to the same type as default_value
    try:
        if isinstance(default_value, bool):
            return value.lower() in ('true', 'yes', '1', 'y')
        elif isinstance(default_value, int):
            return int(value)
        elif isinstance(default_value, float):
            return float(value)
        else:
            return value
    except (ValueError, TypeError):
        return default_value

def get_search_radius():
    """Get the configured search radius."""
    radius = get_config_value(ENV_SEARCH_RADIUS, DEFAULT_SEARCH_RADIUS)
    return int(radius) if isinstance(radius, str) else radius

def get_context_size(size='medium'):
    """
    Get the configured context size.
    
    Args:
        size: The context size category ('small', 'medium', 'large', 'full')
        
    Returns:
        The context size in lines
    """
    # First check if there's an override in the environment
    env_override = get_config_value(ENV_CONTEXT_SIZE, None)
    if env_override is not None:
        return int(env_override) if isinstance(env_override, str) else env_override
    
    # Otherwise use the default for the specified size
    if size == 'small':
        return DEFAULT_SMALL_CONTEXT_SIZE
    elif size == 'medium':
        return DEFAULT_MEDIUM_CONTEXT_SIZE
    elif size == 'large':
        return DEFAULT_LARGE_CONTEXT_SIZE
    elif size == 'full':
        return DEFAULT_FULL_CONTEXT_SIZE
    else:
        return DEFAULT_MEDIUM_CONTEXT_SIZE

def get_confidence_threshold(level='medium'):
    """
    Get the configured confidence threshold.
    
    Args:
        level: The confidence level ('exact', 'high', 'medium', 'low', 'minimum', 'very_low')
               'medium' is the default used for fuzzy matching in the difflib implementation
        
    Returns:
        The confidence threshold value
    """
    # First check if there's an override in the environment
    env_override = get_config_value(ENV_CONFIDENCE_THRESHOLD, None)
    if env_override is not None:
        return float(env_override) if isinstance(env_override, str) else env_override
    
    # Otherwise use the default for the specified level
    if level == 'exact':
        return EXACT_MATCH_THRESHOLD
    elif level == 'high':
        return HIGH_CONFIDENCE_THRESHOLD
    elif level == 'medium':
        return MEDIUM_CONFIDENCE_THRESHOLD
    elif level == 'low':
        return LOW_CONFIDENCE_THRESHOLD
    elif level == 'minimum':
        return MINIMUM_CONFIDENCE_THRESHOLD
    elif level == 'very_low':
        return 0.2  # Even lower threshold for desperate cases
    else:
        return MEDIUM_CONFIDENCE_THRESHOLD

def get_max_offset():
    """Get the configured maximum offset for hunk application."""
    max_offset = get_config_value(ENV_MAX_OFFSET, 500)  # Increased from 100 to 500 for large files
    return int(max_offset) if isinstance(max_offset, str) else max_offset

def is_adaptive_context_enabled():
    """Check if adaptive context sizing is enabled."""
    return get_config_value(ENV_ADAPTIVE_CONTEXT, ADAPTIVE_CONTEXT_ENABLED)

def calculate_adaptive_context_size(hunk_size):
    """
    Calculate an adaptive context size based on hunk size.
    
    Args:
        hunk_size: The size of the hunk in lines
        
    Returns:
        The adaptive context size
    """
    if not is_adaptive_context_enabled():
        return get_context_size('medium')
    
    # Calculate context size as a ratio of hunk size
    context_size = int(hunk_size * ADAPTIVE_CONTEXT_RATIO)
    
    # Ensure it's within the min/max bounds
    context_size = max(ADAPTIVE_CONTEXT_MIN_LINES, min(context_size, ADAPTIVE_CONTEXT_MAX_LINES))
    
    return context_size
