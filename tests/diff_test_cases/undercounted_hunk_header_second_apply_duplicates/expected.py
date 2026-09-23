"""Trimmed shape of app/config/models_config.py around the kimi-k3 entry."""

MODEL_CONFIGS = {
    "bedrock": {
        "kimi-k2-thinking": {
            "model_id": {
                "us": "moonshot.kimi-k2-thinking"
            },
            "family": "kimi",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 256000,
            "context_window": 256000,
            "max_output_tokens": 16384,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "kimi-k3": {
            # Live-verified 2026-09-23: moonshotai.kimi-k3 "Kimi K3" invocable
            # via Converse on bedrock-runtime (us./global. CRIS, us-east-1 +
            # us-west-2); TEXT+IMAGE in. No wrapper_class -> NovaBedrockProvider
            # (Converse), unlike kimi-k2.5/k2-thinking which use the
            # OpenAIBedrock invoke path. Emits reasoningContent by default.
            # Output ceiling 128000 (probed); temperature/topP rejected (400).
            # Context window LIVE-PROBED 2026-09-23: a ~1.08M-token input was
            # rejected with "maximum (1048576)"; 900k succeeded — so the
            # window is 1048576, NOT the 256k first inferred.
            "model_id": {
                "us": "us.moonshotai.kimi-k3",
                "global": "global.moonshotai.kimi-k3"
            },
            "available_regions": ["us-east-1", "us-west-2"],
            "preferred_region": "us-east-1",
            "family": "oss_openai_gpt",
            "token_limit": 1048576,
            "max_input_tokens": 1048576,
            "context_window": 1048576,
            "max_output_tokens": 120000,
            "default_max_output_tokens": 32000,
            "supports_vision": True,
            "supports_thinking": True,
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
        },
        "minimax-m2.1": {
            "model_id": {
                "us": "minimax.minimax-m2.1"
            },
            "family": "minimax",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 1000000,
            "context_window": 1000000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
    },
}
