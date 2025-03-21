"""
Configuration module for Ziya.

This module contains all configuration constants and settings.
It should be importable without triggering any side effects or initializations.
"""

# Server configuration
DEFAULT_PORT = 8000

# Model configuration
DEFAULT_ENDPOINT = "bedrock"
DEFAULT_MODELS = {
    "bedrock": "sonnet3.5-v2",
    "google": "gemini-1.5-pro"
}

# Global model defaults that apply to all models unless overridden
GLOBAL_MODEL_DEFAULTS = {
    "enforce_size_limit": False,
    "max_request_size_mb": None,
    "temperature": 0.3,
    "supports_thinking": False,
    "supports_max_input_tokens": False,
    "parameter_mappings": {
        "max_output_tokens": ["max_tokens"],  # Some APIs use max_tokens instead
        "temperature": ["temperature"],
        "top_k": ["top_k"],
        "max_tokens": ["max_tokens", "max_output_tokens"]
    }
}

# Endpoint-specific defaults that override globals
ENDPOINT_DEFAULTS = {
    "bedrock": {
        "token_limit": 200000,
        "max_output_tokens": 4096,
        "top_k": 15,
        "supported_params": ["temperature", "top_k", "max_tokens"],
        "parameter_mappings": {
            "max_output_tokens": ["max_tokens"]  # Bedrock uses max_tokens
        },
        "region": "us-west-2",  # Default region for Bedrock
        "service_name": "bedrock-runtime"
    },
    "google": {
        "token_limit": 30720,
        "max_output_tokens": 2048,
        "convert_system_message_to_human": True,
        "enforce_size_limit": True,
        "max_request_size_mb": 10,
        "supported_params": ["temperature", "max_tokens"]
    }
}

# Model-specific configs that override endpoint defaults
MODEL_CONFIGS = {
    "bedrock": {
        "sonnet3.7": {
            "model_id": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            "max_output_tokens": 128000,  # Override endpoint default
            "supports_max_input_tokens": True,  # Override global default
            "supports_thinking": True,  # Override global default
        },
        "sonnet3.5-v2": {
            "model_id": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
        },
        "sonnet3.5": {
            "model_id": "us.anthropic.claude-3-5-sonnet-20240620-v1:0",
        },
        "opus": {
            "model_id": "us.anthropic.claude-3-opus-20240229-v1:0",
        }, 
        "sonnet": {
            "model_id": "us.anthropic.claude-3-sonnet-20240229-v1:0",
        },
        "haiku": {
            "model_id": "us.anthropic.claude-3-haiku-20240307-v1:0",
        },
        "nova-pro": {
            "model_id": "us.amazon.nova-pro-v1:0",
            "max_output_tokens": 4096,
            "supports_max_input_tokens": True,
            "supports_thinking": True,
        },
        "nova-lite": {
            "model_id": "us.amazon.nova-lite-v1:0",
            "max_output_tokens": 4096,
            "supports_max_input_tokens": True,
        },
        "nova-micro": {
            "model_id": "us.amazon.nova-micro-v1:0",
            "max_output_tokens": 4096,
            "supports_max_input_tokens": True,
        },
    },
    "google": {
        "gemini-pro": {
            "model_id": "gemini-pro",
            "token_limit": 30720,
            "max_output_tokens": 2048,
            "convert_system_message_to_human": True,
        },
        "gemini-2.0-pro": {
            "model_id": "gemini-2.0-pro-exp-02-05",
            "token_limit": 2097152,
            "max_output_tokens": 8192,
            "convert_system_message_to_human": False,
        },
        "gemini-2.0-flash": {
            "model_id": "gemini-2.0-flash",
            "token_limit": 1048576,
            "max_output_tokens": 8192,
            "convert_system_message_to_human": False,
        },
        "gemini-2.0-flash-lite": {
            "model_id": "gemini-2.0-flash-lite",
            "token_limit": 1048576,
            "max_output_tokens": 8192,
            "convert_system_message_to_human": False,
        },
        "gemini-1.5-flash": {
            "model_id": "gemini-1.5-flash",
            "token_limit": 1048576,
            "max_output_tokens": 8192,
            "convert_system_message_to_human": False,
        },
        "gemini-1.5-flash-8b": {
            "model_id": "gemini-1.5-flash-8b",
            "token_limit": 1048576,
            "max_output_tokens": 8192,
            "convert_system_message_to_human": False,
        },
        "gemini-1.5-pro": {
            "model_id": "gemini-1.5-pro",
            "token_limit": 1000000,
            "max_output_tokens": 2048,
            "convert_system_message_to_human": False,
        }
    }
}

# Environment variable mapping to config keys
ENV_VAR_MAPPING = {
    "ZIYA_TEMPERATURE": "temperature",
    "ZIYA_TOP_K": "top_k",
    "ZIYA_MAX_OUTPUT_TOKENS": "max_output_tokens",
    "ZIYA_THINKING_MODE": "thinking_mode",
    "ZIYA_MAX_TOKENS": "max_tokens",
    "AWS_REGION": "region"
}

# Default request size limits
DEFAULT_MAX_REQUEST_SIZE_MB = 10
