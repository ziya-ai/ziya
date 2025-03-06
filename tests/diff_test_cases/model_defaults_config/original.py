class ModelManager:
    _state = {
        'model': None,
        'auth_checked': False,
        'auth_success': False,
        'process_id': None
    }

    MODEL_CONFIGS = {
        "bedrock": {
            "sonnet3.7": {
                "model_id": "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                "token_limit": 200000,
                "max_output_tokens": 128000,
                "temperature": 0.3,
                "top_k": 15,
                "is_default": True
            },
            "sonnet3.5": {
                "model_id": "us.anthropic.claude-3-5-sonnet-20240620-v1:0",
                "token_limit": 200000,
                "max_output_tokens": 4096,
                "temperature": 0.3,
                "top_k": 15
            }
        },
        "google": {
            "gemini-pro": {
                "model_id": "gemini-pro",
                "token_limit": 30720,
                "max_output_tokens": 2048,
                "temperature": 0.3, 
                "convert_system_message_to_human": True,
                "is_default": True
            }
        }
    }

    DEFAULT_MODELS = {
        "bedrock": "sonnet3.5-v2",
        "google": "gemini-1.5-pro"
    }

    @classmethod
    def get_model_config(cls, endpoint: str, model_name: str = None) -> dict:
        endpoint_configs = cls.MODEL_CONFIGS.get(endpoint)
        if not endpoint_configs:
            raise ValueError(f"Invalid endpoint: {endpoint}")

        if model_name is None:
            # Find the default model for this endpoint
            for name, config in endpoint_configs.items():
                if config.get("is_default"):
                    return {**config, "name": name}
            # If no default specified, use first model
            first_model = next(iter(endpoint_configs.items()))
            return {**first_model[1], "name": first_model[0]}
        
