"""
Model configuration for Ziya.

This module contains model-specific configuration constants and settings.
It should be importable without triggering any side effects or initializations.
"""
import os

# Model configuration
DEFAULT_ENDPOINT = "bedrock"
DEFAULT_MODELS = {
    "bedrock": "sonnet4.5",
    "google": "gemini-3-pro"
}

# Default regions for specific models
MODEL_DEFAULT_REGIONS = {
    # Add more model-specific defaults as needed
    "sonnet4.0": "us-east-1",
}

# Default region when not specified
DEFAULT_REGION = "us-west-2"

# Global model defaults that apply to all models unless overridden
GLOBAL_MODEL_DEFAULTS = {
    "enforce_size_limit": False,
    "max_request_size_mb": None,
    "temperature": 0.3,
    "supports_thinking": False,
    "supports_vision": False,  # Default: no vision support
    "supports_max_input_tokens": False,
    "default_max_output_tokens": 32768,  # Default value for max_output_tokens
    "parameter_mappings": {
        "max_output_tokens": ["max_tokens"],  # Some APIs use max_tokens instead
        "temperature": ["temperature"],
        "top_k": ["top_k"],
        "max_tokens": ["max_tokens", "max_output_tokens"]
    }
}

# Model families define common characteristics and parameter ranges
MODEL_FAMILIES = {
    "claude": {
        "wrapper_class": "ThrottleSafeBedrock",
        "supported_parameters": ["temperature", "top_k", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 0.3},
            "top_k": {"min": 0, "max": 200, "default": 15},
            "max_tokens": {"min": 1, "max": 4096, "default": 1024}
        },
        "internal_parameters": {
            "stop_sequences": {"default": []}
        },
        "token_limit": 200000
    },
    "nova": {
        "wrapper_class": "NovaBedrock",
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.00001, "max": 1.0, "default": 0.7},
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.9},
            "max_tokens": {"min": 1, "max": 5000, "default": 1000}
        },
        "internal_parameters": {
            "stop_sequences": {"default": []}
        },
        "message_format": "nova",  # Add message format for Nova family
        "max_output_tokens": 5000,
        "supports_max_input_tokens": True,
        "supports_vision": True,  # Nova supports multimodal
        "supports_multimodal": True,
        "context_window": 300000,
        "inference_parameters": {
            "temperature": 0.7,
            "topP": 0.9,
            "maxTokens": 1000
        }
    },
    "nova-pro": { 
        "wrapper_class": "NovaBedrock",
        "parent": "nova",
        "supports_thinking": True  # Only Nova-Pro supports thinking
    },
    "nova-lite": {
        "wrapper_class": "NovaBedrock",
        "parent": "nova",
        "supports_thinking": False
    },
    "nova-premier": {
        "wrapper_class": "NovaBedrock",
        "parent": "nova",
        "supports_thinking": True,
        "token_limit": 1000000,  # Explicitly set token limit for nova-premier
        "supports_multimodal": True,
        "supports_vision": True,
        "context_window": 1000000
    },
    "deepseek": {
        "wrapper_class": "DeepSeekWrapper",
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 0.7},
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.9},
            "max_tokens": {"min": 1, "max": 8192, "default": 2048}
        },
        "internal_parameters": {
            "stop_sequences": {"default": []}
        },
        "supports_thinking": True,
        "token_limit": 128000
    },
    "gemini-pro": {
        "supported_parameters": ["temperature", "top_k", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 1.0},
            "topP": {"min": 0.0, "max": 1.0, "default": 0.95},
            "maxOutputTokens": {"min": 1000, "max": 65535, "default": 20000},
            "frequencyPenalty": {"min": -2, "max": 1.99, "default": 0},
            "presencePenalty": {"min": -2, "max": 1.99, "default": 0}
        }
    },
    "gemini-flash": {
        "supported_parameters": ["temperature", "top_k", "top_p"] 
    },
    "gemini-3": {
        "supported_parameters": ["temperature", "top_k", "top_p", "thinking_level"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 1.0},
            "topP": {"min": 0.0, "max": 1.0, "default": 0.95},
            "maxOutputTokens": {"min": 1000, "max": 65535, "default": 20000}
        },
        "thinking_level": "high",  # Default thinking level for Gemini 3
        "supports_thinking": True,
        "native_function_calling": True,
        "token_limit": 1048576
    },
    "oss_openai_gpt": {
        "supported_parameters": ["temperature", "top_k", "top_p"],
        "token_limit": 128000,
        "region": "us-east-1",
        "inference_parameters": {
            "temperature": 0.7,
            "topP": 0.9,
            "maxTokens": 1000
        }
    }
}

# Endpoint-specific defaults that override globals
ENDPOINT_DEFAULTS = {
    "bedrock": {
        "token_limit": 200000,
        "max_output_tokens": 4096,
        "default_max_output_tokens": 4096,
        "top_k": 15,
        "supported_parameters": ["temperature", "max_tokens", "top_p"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 0.3},
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.9},
            "max_tokens": {"min": 1, "max": 4096, "default": 1024}
        },
        "parameter_mappings": {
            "max_output_tokens": ["max_tokens"]  # Bedrock uses max_tokens
        },
        "region": "us-west-2",  # Default region for Bedrock
        "service_name": "bedrock-runtime"
    },
    "google": {
        "token_limit": 30720,
        "max_output_tokens": 20048,
        "default_max_output_tokens": 20048,
        "supported_parameters": ["temperature", "top_p"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 0.3},
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.9},
        },
        "convert_system_message_to_human": True,
        "enforce_size_limit": True,
        "max_request_size_mb": 10
    }
}

# Model-specific configs that override endpoint defaults
MODEL_CONFIGS = {
    "bedrock": {
        "sonnet4.0": {
            "model_id": {
                "us": "us.anthropic.claude-sonnet-4-20250514-v1:0",
                "eu": "eu.anthropic.claude-sonnet-4-20250514-v1:0"  # Available in EU regions too
            },
            "available_regions": [
                "ap-northeast-1", "ap-northeast-2", "ap-northeast-3", "ap-south-1", 
                "ap-southeast-1", "ap-southeast-2", "eu-central-1", "eu-north-1", 
                "eu-south-2", "eu-west-1", "eu-west-3", "us-east-1", "us-east-2", 
                "us-west-1", "us-west-2"
            ],
            "preferred_region": "us-east-1",  # Default preference but not restricted
            "token_limit": 200000,  # Total context window size
            "max_output_tokens": 64000,  # Maximum output tokens
            "default_max_output_tokens": 36000,  # Default value for max_output_tokens
            "supports_max_input_tokens": True,
            "supports_thinking": True,  # Override global default
            "supports_vision": True,  # Sonnet 4.0+ supports vision
            "family": "claude",
            "supports_context_caching": True,
            "supports_extended_context": True,  # Supports 1M token context window
            "extended_context_limit": 1000000,  # Extended context window size
            "extended_context_header": "context-1m-2025-08-07"  # Beta header for extended context
        },
        "sonnet4.5": {
            "model_id": {
                "us": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "eu": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
            },
            "available_regions": [
                "us-east-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-1"
            ],
            "preferred_region": "us-east-1",  # Default preference
            "token_limit": 200000,  # Total context window size
            "max_output_tokens": 64000,  # Maximum output tokens
            "default_max_output_tokens": 36000,  # Default value for max_output_tokens
            "supports_max_input_tokens": True,
            "supports_thinking": True,  # Override global default
            "supports_vision": True,  # Sonnet 4.5 supports vision
            "family": "claude",
            "supports_context_caching": True,
            "supports_extended_context": True,  # Supports 1M token context window
            "extended_context_limit": 1000000,  # Extended context window size
            "extended_context_header": "context-1m-2025-08-07"  # Same header as sonnet4.0
        },
        "sonnet3.7": {
            "model_id": "eu.anthropic.claude-3-7-sonnet-20250219-v1:0",
            "available_regions": ["eu-west-1", "eu-central-1"],
            "region_restricted": True,  # Only available in EU regions
            "preferred_region": "eu-west-1",
            "token_limit": 200000,  # Total context window size
            "max_output_tokens": 64000,  # Maximum output tokens
            "default_max_output_tokens": 10000,  # Default value for max_output_tokens
            "supports_max_input_tokens": True,
            "supports_thinking": True,  # Override global default
            "supports_vision": True,  # Sonnet 3.7 supports vision
            "family": "claude",
            "supports_context_caching": True,
            "region": "eu-west-1"  # Ensure sonnet3.7 uses EU region
        },
        "sonnet3.5-v2": {
            "model_id": {
                "us": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                # Only available in US regions presently
            },
            "family": "claude",
            "supports_vision": True,
            "supports_context_caching": True,
        },
        "sonnet3.5": {
            "model_id": {
                "us": "us.anthropic.claude-3-5-sonnet-20240620-v1:0",
                "eu": "anthropic.claude-3-5-sonnet-20240620-v1:0"
            },
            "family": "claude",
            "supports_vision": True,
            "supports_context_caching": True,
        },
        "opus3": {
            "model_id": {
                "us": "us.anthropic.claude-3-opus-20240229-v1:0"
            },
            "available_regions": ["us-east-1", "us-west-2"],
            "region_restricted": True,  # Only available in US regions
            "preferred_region": "us-east-1",
            "family": "claude",
            "supports_vision": True,
            "region": "us-east-1"  # Model-specific region preference
        },
        "opus4": {
            "max_output_tokens": 64000,  # Add explicit output token limits
            "default_max_output_tokens": 32000,  # Higher default for opus4
            "max_iterations": 8,  # Higher iteration limit for advanced model
            "timeout_multiplier": 6,  # Longer timeouts for complex responses
            "is_advanced_model": True,  # Flag for 4.0+ capabilities
            "token_limit": 200000,  # Add context window
            "supports_max_input_tokens": True,
            "model_id": {
                "us": "us.anthropic.claude-opus-4-20250514-v1:0"
            },
            "available_regions": ["us-east-1", "us-east-2", "us-west-2"],
            "region_restricted": True,  # Only available in US regions
            "preferred_region": "us-east-1",
            "family": "claude",
            "supports_context_caching": True,
            "supports_vision": True,
        },
        "opus4.1": {
            "model_id": {
                "us": "us.anthropic.claude-opus-4-1-20250805-v1:0"
            },
            "available_regions": ["us-east-1", "us-east-2", "us-west-2"],
            "region_restricted": True,  # Only available in US regions
            "preferred_region": "us-east-1",
            "token_limit": 200000,  # Total context window size
            "max_output_tokens": 64000,  # Maximum output tokens
            "default_max_output_tokens": 32000,  # Increased from 10k to 32k for longer responses
            "max_iterations": 8,
            "timeout_multiplier": 6,
            "is_advanced_model": True,
            "supports_max_input_tokens": True,
            "supports_thinking": True,  # Override global default
            "family": "claude",
            "supports_context_caching": True,
            "supports_vision": True,
        },
        "opus4.5": {
            "model_id": "global.anthropic.claude-opus-4-5-20251101-v1:0",  # Global inference profile
            "token_limit": 200000,
            "max_output_tokens": 64000,
            "default_max_output_tokens": 32000,
            "max_iterations": 8,
            "timeout_multiplier": 6,
            "is_advanced_model": True,
            "supports_max_input_tokens": True,
            "supports_thinking": True,
            "family": "claude",
            "supports_context_caching": True,
            "supports_vision": True,
            "supports_extended_context": True,  # Test if Bedrock allows it
            "extended_context_limit": 1000000,
            "extended_context_header": "context-1m-2025-08-07",
        },
        "sonnet": {
            "model_id": {
                "us": "us.anthropic.claude-3-sonnet-20240229-v1:0",
                "eu": "anthropic.claude-3-sonnet-20240229-v1:0"
            },
            "family": "claude",
            "supports_vision": True,
            "supports_context_caching": True,
        },
        "haiku": {
            "model_id": {
                "us": "us.anthropic.claude-3-haiku-20240307-v1:0",
                "eu": "anthropic.claude-3-haiku-20240307-v1:0"
            },
            "family": "claude",
            "supports_vision": True,
            "supports_context_caching": True,
        },
        "haiku-4.5": {
            "model_id": {
                "us": "us.anthropic.claude-haiku-4-5-20251001-v1:0"
            },
            "token_limit": 200000,
            "max_output_tokens": 64000,
            "default_max_output_tokens": 10000,
            "supports_max_input_tokens": True,
            "supports_thinking": False,
            "family": "claude",
            "supports_context_caching": True,
            "supports_vision": True,
        },
        "nova-pro": {
            "model_id": {
                "us": "us.amazon.nova-pro-v1:0"
            },
            "family": "nova-pro"  # Use nova-pro family which includes top_k
        },
        "nova-lite": {
            "model_id": {
                "us": "us.amazon.nova-lite-v1:0"
            },
            "family": "nova",  # Use nova family which doesn't include top_k
            "supported_parameters": ["temperature", "top_p", "max_tokens"]  # Adding temperature back as supported
        },
        "nova-micro": {
            "model_id": {
                "us": "us.amazon.nova-micro-v1:0"
            },
            "family": "nova",  # Use nova family which doesn't include top_k
            "supports_multimodal": False,  # Override the family default
            "context_window": 128000,  # Override the family default
            "parameter_mappings": {
                "max_tokens": "maxTokens"  # Nova uses maxTokens instead of max_tokens
            }
        },
        "nova-premier": {
            "model_id": {
                "us": "us.amazon.nova-premier-v1:0"
            },
            "family": "nova-premier", 
            "supports_multimodal": True,
            "token_limit": 1000000,  # Total context window size
            "context_window": 1000000
        },
        "deepseek-r1": {
            "model_id": {
                "us": "us.deepseek.r1-v1:0"
            },
            "family": "deepseek",
            "max_input_tokens": 128000,
            "context_window": 128000
        },
        "openai-gpt-120b": {
            "model_id": {
                "us": "openai.gpt-oss-120b-1:0"
            },
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "region": "us-west-2"  # OpenAI models only available in us-west-2
        },
        "openai-gpt-20b": {
            "model_id": {
                "us": "openai.gpt-oss-20b-1:0"
            },
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,  # Match 120B model for consistency
            "region": "us-west-2"  # OpenAI models only available in us-west-2
        },
        "qwen3-coder-480b": {
            "model_id": {
                "us": "qwen.qwen3-coder-480b-a35b-v1:0"
            },
            "available_regions": ["us-west-2"],
            "region_restricted": True,
            "preferred_region": "us-west-2",
            "family": "oss_openai_gpt",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096
        }
    },
    "google": {
        "gemini-2.5-pro": {
            "model_id": "gemini-2.5-pro",
            "token_limit": 1048576,
            "family": "gemini-pro",
            "max_output_tokens": 65536,  # Gemini 2.5 Pro supports up to 65K output tokens
            "convert_system_message_to_human": False,
            "supports_vision": True,  # Gemini Pro supports vision
            "supports_function_calling": True,
            "native_function_calling": True,
        },
        "gemini-flash": {
            "model_id": "gemini-2.5-flash-preview-05-20",
            "token_limit": 1048576,
            "family": "gemini-flash",
            "max_output_tokens": 65535,
            "convert_system_message_to_human": False,
            "supports_vision": True,  # Gemini Flash supports vision
            "supports_function_calling": True,
            "native_function_calling": True,
        },

        "gemini-2.0-flash": {
            "model_id": "gemini-2.0-flash",
            "token_limit": 1048576,
            "family": "gemini-flash",
            "max_output_tokens": 8192,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": False,
        },
        "gemini-2.0-flash-lite": {
            "model_id": "gemini-2.0-flash-lite",
            "token_limit": 1048576,
            "family": "gemini-flash",
            "max_output_tokens": 8192,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": False,  # This model doesn't support function calling per Google docs
            "native_function_calling": False,
        },
        "gemini-1.5-flash": {
            "model_id": "gemini-1.5-flash",
            "token_limit": 1048576,
            "family": "gemini-flash",
            "max_output_tokens": 8192,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": False,
        },
        "gemini-1.5-pro": {
            "model_id": "gemini-1.5-pro",
            "token_limit": 1000000,
            "family": "gemini-pro",
            "max_output_tokens": 2048,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": False,
        },
        "gemini-3-pro": {
            "model_id": "gemini-3-pro-preview",
            "token_limit": 1048576,
            "family": "gemini-3",
            "max_output_tokens": 65536,
            "default_max_output_tokens": 8192,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": True,
            "thinking_level": "medium"
        },
    }
}

# Environment variable mapping to config keys
ENV_VAR_MAPPING = {
    "ZIYA_TEMPERATURE": "temperature",
    "ZIYA_TOP_K": "top_k",
    "ZIYA_TOP_P": "top_p",
    "ZIYA_MAX_OUTPUT_TOKENS": "max_output_tokens",
    "ZIYA_THINKING_MODE": "thinking_mode",
    "ZIYA_MAX_TOKENS": "max_tokens",
    "ZIYA_MODEL_ID_OVERRIDE": "model_id",
    "AWS_REGION": "region",
    "ZIYA_THINKING_LEVEL": "thinking_level"
}

# Default request size limits
DEFAULT_MAX_REQUEST_SIZE_MB = 10

# MCP Tool sentinel configuration - single env var for tag name
TOOL_SENTINEL_TAG = os.environ.get("ZIYA_TOOL_SENTINEL", "TOOL_SENTINEL")
TOOL_SENTINEL_OPEN = f"<{TOOL_SENTINEL_TAG}>"
TOOL_SENTINEL_CLOSE = f"</{TOOL_SENTINEL_TAG}>"

# Shell command configuration
DEFAULT_SHELL_CONFIG = {
    "enabled": True,
    "allowedCommands": [
        "ls", "cat", "pwd", "grep", "wc", "touch", "find", "date", "od", "df", 
        "netstat", "lsof", "ps", "sed", "awk", "cut", "sort", "which", "hexdump", 
        "xxd", "tail", "head", "echo", "printf", "tr", "uniq", "column", "nl", 
        "tee", "base64", "md5sum", "sha1sum", "sha256sum", "bc", "expr", "seq", 
        "paste", "join", "fold", "expand", "cd", "tree", "less", "xargs", "curl", 
        "ping", "du", "file"
    ],
    "gitOperationsEnabled": True,
    "safeGitOperations": [
        "status", "log", "show", "diff", "branch", "remote", "config --get",
        "ls-files", "ls-tree", "blame", "tag", "stash list", "reflog", 
        "rev-parse", "describe", "shortlog", "whatchanged"
    ],
    "timeout": 90  # Increased base timeout to support longer operations
}

def get_default_shell_config():
    """Get the default shell configuration."""
    return DEFAULT_SHELL_CONFIG.copy()

# Helper functions for model parameter validation

def get_supported_parameters(endpoint, model_name):
    """
    Get the supported parameters for a model.
    
    Args:
        endpoint: The endpoint name (e.g., "bedrock", "google")
        model_name: The model name (e.g., "nova-lite", "sonnet3.5")
        
    Returns:
        dict: Dictionary of parameter names to their constraints
    """
    if endpoint not in MODEL_CONFIGS or model_name not in MODEL_CONFIGS[endpoint]:
        return {}
    
    model_config = MODEL_CONFIGS[endpoint][model_name]
    
    # If no family is specified, return empty dict
    if "family" not in model_config:
        return {}
    
    family_name = model_config["family"]
    if family_name not in MODEL_FAMILIES:
        return {}
    
    # Get all configurations we need
    endpoint_config = ENDPOINT_DEFAULTS.get(endpoint, {})
    model_specific_config = MODEL_CONFIGS[endpoint][model_name]
    family_config = MODEL_FAMILIES.get(family_name, {})
    parent_family_config = {}
    if "parent" in family_config and family_config["parent"] in MODEL_FAMILIES:
        parent_family_config = MODEL_FAMILIES[family_config["parent"]]
    
    # Collect supported parameters from all levels
    supported_params = set()
    
    # Start with endpoint defaults
    supported_params.update(endpoint_config.get("supported_parameters", []))
    
    # Add parent family parameters if available
    if parent_family_config:
        supported_params.update(parent_family_config.get("supported_parameters", []))
    
    # Add family parameters
    supported_params.update(family_config.get("supported_parameters", []))
    
    # Add model-specific parameters
    supported_params.update(model_specific_config.get("supported_parameters", []))
    
    # Now collect parameter ranges from all levels
    param_ranges = {}
    
    # Start with endpoint defaults
    param_ranges.update(endpoint_config.get("parameter_ranges", {}))
    
    # Add parent family ranges if available
    if parent_family_config:
        param_ranges.update(parent_family_config.get("parameter_ranges", {}))
    
    # Add family ranges
    param_ranges.update(family_config.get("parameter_ranges", {}))
    
    # Add model-specific ranges
    param_ranges.update(model_specific_config.get("parameter_ranges", {}))
    
    # Create final parameter dictionary with ranges
    result = {}
    for param in supported_params:
        if param in param_ranges:
            result[param] = param_ranges[param]
        else:
            # Default empty constraints if no range is defined
            result[param] = {}
    
    return result

def validate_model_parameters(endpoint, model_name, params):
    """
    Validate parameters for a model.
    
    Args:
        endpoint: The endpoint name (e.g., "bedrock", "google")
        model_name: The model name (e.g., "nova-lite", "sonnet3.5")
        params: Dictionary of parameter names to values
        
    Returns:
        tuple: (is_valid, error_message, filtered_params)
    """
    supported_params = get_supported_parameters(endpoint, model_name)
    
    # Check for unsupported parameters
    unsupported = []
    for param_name in params:
        if param_name not in supported_params:
            unsupported.append(param_name)
    
    # If there are unsupported parameters, return an error
    if unsupported:
        # Get the family name for better error messages
        family_name = MODEL_CONFIGS[endpoint][model_name].get("family", "unknown")
        
        # Build a helpful error message
        error_msg = f"The following parameters are not supported by the {model_name} model: {', '.join(unsupported)}"
        
        # Add information about which family supports the parameters
        for param in unsupported:
            for family, config in MODEL_FAMILIES.items():
                if family == family_name:
                    continue
                
                # Check if this family supports the parameter
                has_param = False
                if "supported_parameters" in config and param in config["supported_parameters"]:
                    has_param = True
                elif "parent" in config and config["parent"] in MODEL_FAMILIES:
                    parent = MODEL_FAMILIES[config["parent"]]
                    if "supported_parameters" in parent and param in parent["supported_parameters"]:
                        has_param = True
                
                if has_param:
                    error_msg += f"\nParameter '{param}' is only available in the {family} family."
                    break
        
        # Add information about supported parameters
        error_msg += f"\n\nSupported parameters for {model_name}:"
        for param, constraints in supported_params.items():
            param_info = f"\n  --{param}"
            if "min" in constraints and "max" in constraints:
                param_info += f" ({constraints['min']}-{constraints['max']}"
                if "default" in constraints:
                    param_info += f", default: {constraints['default']}"
                param_info += ")"
            elif "default" in constraints:
                param_info += f" (default: {constraints['default']})"
            error_msg += param_info
        
        return False, error_msg, {}
    
    # Filter out unsupported parameters and validate ranges
    filtered_params = {}
    for param_name, value in params.items():
        constraints = supported_params[param_name]
        
        # Validate range if min/max are specified
        if "min" in constraints and value < constraints["min"]:
            return False, f"Parameter '{param_name}' value {value} is below the minimum of {constraints['min']}", {}
        if "max" in constraints and value > constraints["max"]:
            return False, f"Parameter '{param_name}' value {value} is above the maximum of {constraints['max']}", {}
        
        # Add to filtered parameters
        filtered_params[param_name] = value
    
    return True, "", filtered_params

def get_cli_parameter_name(param_name):
    """
    Convert a parameter name to its CLI argument form.
    
    Args:
        param_name: The parameter name
        
    Returns:
        str: The CLI argument name
    """
    # Map internal parameter names to CLI argument names
    param_map = {
        "temperature": "--temperature",
        "top_p": "--top-p",
        "top_k": "--top-k",
        "max_tokens": "--max-output-tokens",
        "stop_sequences": "--stop-sequences",
    }
    
    return param_map.get(param_name, f"--{param_name.replace('_', '-')}")

def list_model_capabilities(endpoint=None, model_name=None):
    """
    List the capabilities of models.
    
    Args:
        endpoint: Optional endpoint name to filter by
        model_name: Optional model name to filter by
        
    Returns:
        str: Formatted string with model capabilities
    """
    result = []
    
    # Filter by endpoint if specified
    endpoints = [endpoint] if endpoint else MODEL_CONFIGS.keys()
    
    for ep in endpoints:
        if ep not in MODEL_CONFIGS:
            continue
        
        # Filter by model if specified
        models = [model_name] if model_name and model_name in MODEL_CONFIGS[ep] else MODEL_CONFIGS[ep].keys()
        
        for model in models:
            if model not in MODEL_CONFIGS[ep]:
                continue
            
            model_config = MODEL_CONFIGS[ep][model]
            result.append(f"Model: {model} ({ep})")
            
            # Add model ID
            if "model_id" in model_config:
                result.append(f"  Model ID: {model_config['model_id']}")
            
            # Add family
            if "family" in model_config:
                result.append(f"  Family: {model_config['family']}")
            
            # Add supported parameters
            params = get_supported_parameters(ep, model)
            if params:
                result.append("  Supported parameters:")
                for param, constraints in params.items():
                    param_info = f"    {get_cli_parameter_name(param)}"
                    if "min" in constraints and "max" in constraints:
                        param_info += f" ({constraints['min']}-{constraints['max']}"
                        if "default" in constraints:
                            param_info += f", default: {constraints['default']}"
                        param_info += ")"
                    elif "default" in constraints:
                        param_info += f" (default: {constraints['default']})"
                    result.append(param_info)
            
            # Add other capabilities
            capabilities = []
            if model_config.get("supports_thinking", False):
                capabilities.append("thinking mode")
            if model_config.get("supports_multimodal", False):
                capabilities.append("multimodal")
            if model_config.get("supports_max_input_tokens", False):
                capabilities.append("max input tokens")
            
            if capabilities:
                result.append(f"  Capabilities: {', '.join(capabilities)}")
            
            # Add context window
            if "context_window" in model_config:
                result.append(f"  Context window: {model_config['context_window']} tokens")
            
            result.append("")  # Empty line between models
    
    return "\n".join(result)

def get_model_capabilities(endpoint=None, model_name=None):
    """
    Get comprehensive capabilities for a model endpoint.
    Single source of truth for model capabilities.
    
    Args:
        endpoint: Endpoint name (e.g., "bedrock", "google"). If None, uses environment.
        model_name: Model name. If None, uses environment.
        
    Returns:
        dict: Dictionary of capability flags
    """
    # Get from environment if not provided
    if endpoint is None:
        endpoint = os.environ.get("ZIYA_ENDPOINT", DEFAULT_ENDPOINT)
    if model_name is None:
        model_name = os.environ.get("ZIYA_MODEL", DEFAULT_MODELS.get(endpoint))
    
    # Get model config
    config = MODEL_CONFIGS.get(endpoint, {}).get(model_name, {})
    endpoint_config = ENDPOINT_DEFAULTS.get(endpoint, {})
    
    # Build capabilities dict
    return {
        "native_function_calling": config.get("native_function_calling", 
                                             endpoint == "bedrock"),  # Bedrock defaults to native
        "supports_vision": config.get("supports_vision", False),
        "supports_thinking": config.get("supports_thinking", False),
        "supports_streaming": True,  # All current models support streaming
        "supports_context_caching": config.get("supports_context_caching", False),
        "supports_multimodal": config.get("supports_multimodal", False),
        "endpoint": endpoint,
        "model_name": model_name
    }
