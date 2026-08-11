"""
Model configuration for Ziya.

This module contains model-specific configuration constants and settings.
It should be importable without triggering any side effects or initializations.
"""
import os
import json

# Model configuration
DEFAULT_ENDPOINT = "bedrock"
DEFAULT_MODELS = {
    "bedrock": "sonnet5",
    "google": "gemini-3.1-pro",
    "openai": "gpt-5.5",
    "anthropic": "claude-sonnet-5",
    "zai": "glm-5.2",
    "meta": "muse-spark-1.2"
}

# Model aliases — short names that resolve to canonical model keys.
# Checked per-endpoint so the same alias can point to different models
# on different providers.  Resolution order:
#   1. Exact match in MODEL_CONFIGS[endpoint] (canonical name)
#   2. Alias lookup in MODEL_ALIASES[endpoint]
#   3. Fallback to endpoint default (with warning)
MODEL_ALIASES: dict[str, dict[str, str]] = {
    "bedrock": {
        "fable": "fable5",
        "mythos": "mythos5",
        "sonnet": "sonnet4.6",
        "opus": "opus4.8",
        "haiku": "haiku-4.5",
        "nova": "nova-pro",
        "deepseek-v3": "deepseek-v3.1",
        # End-of-life on Bedrock (entries removed 2026-07) — keep the old
        # names resolving for users with saved settings:
        "sonnet3.7": "sonnet4.6",
        "opus4": "opus4.1",
    },
    "google": {
        "gemini": "gemini-3.1-pro",
        "flash": "gemini-flash",
    },
    "anthropic": {
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-8",
    },
    "zai": {
        "glm": "glm-5.2",
    },
    "meta": {
        "muse": "muse-spark-1.2",
        "spark": "muse-spark-1.2",
        # Data-sharing tier — see the muse-spark-1.2-contributor entry in
        # MODEL_CONFIGS before using this.
        "muse-contributor": "muse-spark-1.2-contributor",
    },
}

# Portable model "tiers" — the recommended way for a decomposed task
# (Task Card block, delegate, CLI task) to pick a model without
# hardcoding a provider- or version-specific name.  A tier is a
# relative cost/capability rung.
#
# Crucially, tiers are NOT a separate table.  Each concrete model entry
# in MODEL_CONFIGS carries its own ``"tier"`` tag, so the tier follows
# the model: when e.g. ``fable6`` lands, whoever adds it tags it in the
# same edit, the ``fable`` alias re-points, and the ``frontier`` tier
# auto-updates — no versioned name to maintain in two places.
#
# resolve_tier_model() scans MODEL_CONFIGS[endpoint] for the entry whose
# ``tier`` matches (first match in insertion order wins if two share a
# rung).  A requested tier with no tagged model rounds UP to the nearest
# defined rung at or above it (falling to the highest rung below only
# when nothing at/above is defined), then to the center rung ``medium``,
# then to DEFAULT_MODELS.  Never raises.
#
# The ladder has five rungs.  ``medium`` is the CENTER — it is the
# default/average model and the fallback target, i.e. the same model the
# top-level conversation uses (sonnet5 on Bedrock).  ``frontier`` is the
# rarely-warranted top: cutting-edge models that today run ~20x the cost
# of ``large`` with heavy throttling, so reserve it for work that truly
# needs it.  The rungs, cheapest → most capable:
#     xsmall  small  medium(=default)  large  frontier
_TIER_ORDER = ("xsmall", "small", "medium", "large", "frontier")
MODEL_TIER_NAMES = _TIER_ORDER
# The center rung: default model + resolution fallback target.
DEFAULT_TIER = "medium"


def resolve_tier_model(endpoint: str, tier: str) -> str:
    """Resolve a portable tier name to a concrete model NAME on *endpoint*.

    Scans per-model ``tier`` tags rather than a separate registry, so
    tiers stay correct as models are added/retired with no extra
    maintenance.  An unmapped rung rounds UP to the nearest defined rung
    at or above it (falling to the highest below only if nothing at/above
    exists), then the center rung ``medium``, then the endpoint default —
    never raises on an unknown/unmapped tier.  Rounding up means an
    unmapped rung never silently under-serves a task with a weaker model
    than requested.
    """
    endpoint_models = MODEL_CONFIGS.get(endpoint, {})
    # Build tier -> first-seen model name from per-model tags.
    by_tier: dict[str, str] = {}
    for name, cfg in endpoint_models.items():
        t = cfg.get("tier")
        if t and t not in by_tier:
            by_tier[t] = name

    if tier in by_tier:
        return by_tier[tier]

    # Requested tier has no tagged model on this endpoint: round UP —
    # prefer the nearest defined rung at or above the requested index;
    # only if none exists above, fall to the nearest (highest) below.
    # Sort key: (is-below flag, distance) so all at/above rungs are
    # considered before any below rung, nearest-first within each group.
    if tier in _TIER_ORDER and by_tier:
        idx = _TIER_ORDER.index(tier)
        best = min(
            by_tier.keys(),
            key=lambda t: (0 if _TIER_ORDER.index(t) >= idx else 1,
                           abs(_TIER_ORDER.index(t) - idx)),
        )
        return by_tier[best]

    if DEFAULT_TIER in by_tier:
        return by_tier[DEFAULT_TIER]
    return DEFAULT_MODELS.get(endpoint, DEFAULT_MODELS[DEFAULT_ENDPOINT])


# Lightweight models used for background tasks (memory extraction,
# summarization, classification).  These should be the cheapest
# available model per endpoint.  Override per-category via
# ZIYA_{CATEGORY}_MODEL env var.
# Default models for lightweight service tasks (extraction, classification).
# Each endpoint maps to its cheapest capable model.
DEFAULT_SERVICE_MODELS = {
    "bedrock": "us.amazon.nova-lite-v1:0",
    "google": "gemini-2.0-flash-lite",
    "openai": "gpt-5.5-mini",
    "anthropic": "claude-haiku-4-5-20251001",
    "zai": "glm-4.6",
}

# Category-specific overrides.  Memory extraction needs a model that
# reliably follows nuanced prompt instructions (session-artifact vs
# durable knowledge).  Cheap models produce garbage that requires an
# ever-growing regex compensating layer.  Prefer instruction-following
# strength over cost here — the volume is low (once per conversation).
SERVICE_MODEL_OVERRIDES: dict[str, dict[str, str]] = {
    "memory_extraction": {
        "bedrock": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "google": "gemini-2.0-flash",       # flash (not lite) for extraction
        "openai": "gpt-5.5-mini",           # already strong enough
        "anthropic": "claude-haiku-4-5-20251001",
    },
    # Memory eval needs the strongest available judgment model — its
    # purpose is to grade the mid-tier extractor and salience heuristic.
    # Cost is bounded (~50-conversation samples, ~human-supervised cadence)
    # so the per-call expense is acceptable.  Routes to Opus on Bedrock /
    # Anthropic, top-tier on others.  Override per-user via
    # ZIYA_MEMORY_EVAL_MODEL.
    "memory_eval": {
        "bedrock": "us.anthropic.claude-opus-4-8",
        "google": "gemini-3.1-pro",
        "openai": "gpt-5.5",
        "anthropic": "claude-opus-4-8",
    },
    # Dangling-intent judge (app/services/intent_judge.py) needs reliable
    # yes/no instruction-following, not raw reasoning power. Measured live
    # against 9 real transcript cases (4 genuine dangling-intent endings, 5
    # correctly-resolved endings covering the quoted/conditional/negated/
    # past-tense exclusions the judge exists to handle): the endpoint
    # default (Nova Lite on Bedrock) scored 5/9 and specifically missed the
    # judge's OWN canonical positive example shape ("Let me gather the
    # exact text..." as the final sentence) — a lite-tier reliability gap,
    # not a prompt defect (Haiku and Sonnet both scored 9/9 on the same
    # prompt). Same fix pattern as memory_extraction above. Volume is low
    # (single-digit calls/session per the module docstring) so the cost
    # step from lite to Haiku-tier is negligible. Override per-user via
    # ZIYA_INTENT_JUDGE_MODEL.
    "intent_judge": {
        "bedrock": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "google": "gemini-2.0-flash",
        "openai": "gpt-5.5-mini",
        "anthropic": "claude-haiku-4-5-20251001",
    },
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
    "supports_assistant_prefill": True,  # Default: most models support prefill
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
    "deepseek": {
        "wrapper_class": "OpenAIBedrock",
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
    },
    "kimi": {
        "wrapper_class": "OpenAIBedrock",
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 0.7},
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.9},
            "max_tokens": {"min": 1, "max": 8192, "default": 4096}
        },
        "supports_thinking": True,
        "token_limit": 128000
    },
    "minimax": {
        "wrapper_class": "OpenAIBedrock",
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 0.7},
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.9},
            "max_tokens": {"min": 1, "max": 8192, "default": 4096}
        },
        "token_limit": 1000000
    },
    "glm": {
        "wrapper_class": "OpenAIBedrock",
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 0.7},
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.9},
            "max_tokens": {"min": 1, "max": 8192, "default": 4096}
        },
        "token_limit": 128000
    },
    "zai-glm": {
        # z.ai direct API (OpenAI-compatible). Distinct from the Bedrock
        # "glm" family above, which routes through the OpenAIBedrock wrapper
        # and caps output at 8K. The direct API supports much larger output.
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 0.6},
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.95},
            "max_tokens": {"min": 1, "max": 131072, "default": 4096}
        },
        "native_function_calling": True,
        "token_limit": 1000000,
        # Reasoning: z.ai's OpenAI-compatible API enables thinking via the
        # top-level {"thinking": {"type": "enabled"}} request envelope and
        # accepts reasoning_effort; reasoning streams back on
        # delta.reasoning_content (handled generically by OpenAIDirectProvider).
        # The effort value set matches the Claude effort UI, so the existing
        # ZIYA_THINKING_EFFORT plumbing applies. Activate with ZIYA_THINKING_MODE.
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        "reasoning_request": {"thinking": {"type": "enabled"}},
        "thinking_effort_default": "high",
        "supported_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        "token_limit": 1000000
    },
    "meta-muse": {
        # Meta Model API (Muse Spark), OpenAI Chat Completions compatible.
        # Served by OpenAIDirectProvider with base_url=api.meta.ai/v1.
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 0.3},
            "top_p": {"min": 0.0, "max": 1.0, "default": 1.0},
            # Live-verified 2026-08: 131072 accepted; 2000000 -> 400.
            "max_tokens": {"min": 1, "max": 131072, "default": 4096}
        },
        "native_function_calling": True,
        "supports_vision": True,
        # Reasoning: unlike zai-glm, Meta needs NO thinking-enable envelope —
        # it accepts the standard `reasoning_effort` key, so declaring
        # supports_reasoning_effort is sufficient and reasoning_request is
        # deliberately absent.
        #
        # Reasoning is NOT streamed: no reasoning_content / reasoning delta
        # attribute ever appears (live-verified on 1.1 and 1.2, all efforts).
        # It is only reported after the fact as
        # usage.completion_tokens_details.reasoning_tokens, and it dominates
        # short turns — "What is 31*47?" spent 267 of 279 completion tokens
        # on invisible reasoning. So supports_thinking here means "bills for
        # thinking", not "can display thinking".
        "supports_thinking": True,
        "supports_reasoning_effort": True,
        # Live-verified vocabulary (2026-08): none|minimal|low|medium|high|
        # xhigh, but `none` is rejected per-model ("does not support 'none'
        # with this model") and `max` does not exist ("unknown variant
        # `max`") — both are in Ziya's canonical set and reachable via
        # ZIYA_THINKING_EFFORT, so declaring the real set makes the provider
        # clamp instead of sending a 400. `minimal` has no Ziya equivalent.
        "supported_efforts": ["low", "medium", "high", "xhigh"],
        "thinking_effort_default": "medium",
        "token_limit": 1048576
    },
    "openai-gpt": {
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 0.3},
            "top_p": {"min": 0.0, "max": 1.0, "default": 1.0},
            "max_tokens": {"min": 1, "max": 128000, "default": 4096}
        },
        "native_function_calling": True,
        "supports_vision": True,
        "token_limit": 272000
    },
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
    },
    "openai": {
        "token_limit": 272000,
        "max_output_tokens": 128000,
        "default_max_output_tokens": 32768,
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 0.3},
            "top_p": {"min": 0.0, "max": 1.0, "default": 1.0},
            "max_tokens": {"min": 1, "max": 128000, "default": 4096}
        }
    },
    "zai": {
        "token_limit": 1000000,
        "max_output_tokens": 131072,
        "default_max_output_tokens": 32768,
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 1.0, "default": 0.6},
            "top_p": {"min": 0.0, "max": 1.0, "default": 0.95},
            "max_tokens": {"min": 1, "max": 131072, "default": 4096}
        }
    },
    "meta": {
        "token_limit": 1048576,
        "max_output_tokens": 131072,
        "default_max_output_tokens": 16384,
        "supported_parameters": ["temperature", "top_p", "max_tokens"],
        "parameter_ranges": {
            "temperature": {"min": 0.0, "max": 2.0, "default": 0.3},
            "top_p": {"min": 0.0, "max": 1.0, "default": 1.0},
            "max_tokens": {"min": 1, "max": 131072, "default": 4096}
        }
    },
}

# Model-specific configs that override endpoint defaults
MODEL_CONFIGS = {
    "bedrock": {
        "sonnet4.0": {
            "model_id": {
                "us": "us.anthropic.claude-sonnet-4-20250514-v1:0",
                "eu": "eu.anthropic.claude-sonnet-4-20250514-v1:0",
                "global": "global.anthropic.claude-sonnet-4-20250514-v1:0"
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
                "eu": "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
                "global": "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
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
        "sonnet4.6": {
            "model_id": {
                "us": "us.anthropic.claude-sonnet-4-6",
                "eu": "eu.anthropic.claude-sonnet-4-6",
                "global": "global.anthropic.claude-sonnet-4-6"
            },
            "available_regions": [
                "us-east-1", "us-east-2", "us-west-2",
                "ap-northeast-1", "ap-northeast-3"
            ],
            "preferred_region": "us-east-1",
            "token_limit": 200000,
            "max_output_tokens": 64000,
            "default_max_output_tokens": 36000,
            "supports_max_input_tokens": True,
            "supports_thinking": True,
            "supports_vision": True,
            "family": "claude",
            "supports_adaptive_thinking": True,
            "thinking_effort_default": "medium",
            "supported_efforts": ["low", "medium", "high", "max"],
            "supports_context_caching": True,
            "supports_assistant_prefill": False,
            "supports_extended_context": True,
            "extended_context_limit": 1000000,
            "extended_context_header": "context-1m-2025-08-07"
        },
        "sonnet5": {
            "tier": "medium",
            "model_id": {
                "us": "us.anthropic.claude-sonnet-5",
                "global": "global.anthropic.claude-sonnet-5"
            },
            "available_regions": [
                "us-east-1", "us-east-2", "us-west-2"
            ],
            "preferred_region": "us-east-1",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 36000,
            "supports_max_input_tokens": True,
            "supports_thinking": True,
            "supports_vision": True,
            "family": "claude",
            "supports_adaptive_thinking": True,
            "thinking_effort_default": "medium",
            "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
            "supports_context_caching": True,
            "supports_assistant_prefill": False,
            # Live-verified 2026-07: Bedrock rejects temperature for Sonnet 5
            # ("`temperature` is deprecated for this model"), same as Opus 4.8.
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
        },
        "opus4.1": {
            "model_id": {
                "us": "us.anthropic.claude-opus-4-1-20250805-v1:0",
                "global": "global.anthropic.claude-opus-4-1-20250805-v1:0"
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
            "supports_assistant_prefill": False,
        },
        "opus4.6": {
            "model_id": {
                "us": "us.anthropic.claude-opus-4-6-v1",
                "eu": "eu.anthropic.claude-opus-4-6-v1",
                "global": "global.anthropic.claude-opus-4-6-v1"
            },
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
            "supports_adaptive_thinking": True,
            "thinking_effort_default": "high",
            "supported_efforts": ["low", "medium", "high", "max"],
            "supports_vision": True,
            "supports_assistant_prefill": False,
            "supports_extended_context": True,  # Supports 1M token context window
            "extended_context_limit": 1000000,  # Extended context window size
            "extended_context_header": "context-1m-2025-08-07",  # Beta header for extended context
        },
        "opus4.7": {
            "model_id": {
                "us": "us.anthropic.claude-opus-4-7",
                "eu": "eu.anthropic.claude-opus-4-7",
                "global": "global.anthropic.claude-opus-4-7"
            },
            "available_regions": ["us-east-1", "us-east-2", "us-west-2"],
            "preferred_region": "us-east-1",
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
            "supports_adaptive_thinking": True,
            "thinking_effort_default": "medium",
            "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
            "supports_vision": True,
            "supports_assistant_prefill": False,
            "supports_extended_context": True,
            "extended_context_limit": 1000000,
            "effort_beta_required": False,
            # Opus 4.7 rejects sampling parameters (temperature/top_p/top_k)
            # with a 400 error per Anthropic's migration guide. Steer via
            # prompting + the `effort` parameter instead. These are stripped
            # from outgoing requests and hidden in the frontend modal.
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
        },
        "opus4.8": {
            "tier": "large",
            "model_id": {
                "us": "us.anthropic.claude-opus-4-8",
                "eu": "eu.anthropic.claude-opus-4-8",
                "global": "global.anthropic.claude-opus-4-8"
            },
            "available_regions": ["us-east-1", "us-east-2", "us-west-2"],
            "preferred_region": "us-east-1",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32000,
            "max_iterations": 8,
            "timeout_multiplier": 6,
            "is_advanced_model": True,
            "supports_max_input_tokens": True,
            "supports_thinking": True,
            "family": "claude",
            "supports_context_caching": True,
            "supports_adaptive_thinking": True,
            "thinking_effort_default": "medium",
            "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
            "supports_vision": True,
            "supports_assistant_prefill": False,
            "effort_beta_required": False,
            # Opus 4.8 inherits 4.7's sampling-parameter restrictions
            # (temperature/top_p/top_k rejected with 400). Steer via
            # prompting + the `effort` parameter instead.
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
        },
        "opus5": {
            "tier": "large",
            "model_id": {
                "us": "us.anthropic.claude-opus-5",
                "eu": "eu.anthropic.claude-opus-5",
                "global": "global.anthropic.claude-opus-5"
            },
            "available_regions": ["us-east-1", "us-east-2", "us-west-2"],
            "preferred_region": "us-east-1",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32000,
            "max_iterations": 8,
            "timeout_multiplier": 6,
            "is_advanced_model": True,
            "supports_max_input_tokens": True,
            "supports_thinking": True,
            "family": "claude",
            "supports_context_caching": True,
            "supports_adaptive_thinking": True,
            "thinking_effort_default": "medium",
            "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
            "supports_vision": True,
            "supports_assistant_prefill": False,
            "effort_beta_required": False,
            # Opus 5 inherits 4.7/4.8's sampling-parameter restrictions
            # (temperature/top_p/top_k rejected with 400). Steer via
            # prompting + the `effort` parameter instead.
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
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
                "us": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                "global": "global.anthropic.claude-haiku-4-5-20251001-v1:0"
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
            "tier": "small",
            "model_id": {
                "us": "us.amazon.nova-lite-v1:0"
            },
            "family": "nova",  # Use nova family which doesn't include top_k
            "supported_parameters": ["temperature", "top_p", "max_tokens"]  # Adding temperature back as supported
        },
        "nova-micro": {
            "tier": "xsmall",
            "model_id": {
                "us": "us.amazon.nova-micro-v1:0"
            },
            "family": "nova",  # Use nova family which doesn't include top_k
            "supports_multimodal": False,  # Override the family default
            "supports_vision": False,  # Nova Micro is text-only
            "context_window": 128000,  # Override the family default
            "parameter_mappings": {
                "max_tokens": "maxTokens"  # Nova uses maxTokens instead of max_tokens
            }
        },
        "deepseek-r1": {
            "model_id": {
                "us": "us.deepseek.r1-v1:0"
            },
            "family": "deepseek",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "native_function_calling": False
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
            "wrapper_class": "OpenAIBedrock",
            "family": "oss_openai_gpt",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096
        },
        "deepseek-v3.2": {
            "model_id": {
                "us": "deepseek.v3.2"
            },
            "family": "deepseek",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "deepseek-v3.1": {
            "model_id": {
                "us": "deepseek.v3-v1:0"
            },
            "family": "deepseek",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "kimi-k2.5": {
            "model_id": {
                "us": "moonshotai.kimi-k2.5"
            },
            "family": "kimi",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 256000,
            "context_window": 256000,
            "max_output_tokens": 16384,
            "default_max_output_tokens": 4096,
            "supports_vision": True,
            "region": "us-west-2"
        },
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
        "glm-4.7": {
            "model_id": {
                "us": "zai.glm-4.7"
            },
            "family": "glm",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "glm-4.7-flash": {
            "model_id": {
                "us": "zai.glm-4.7-flash"
            },
            "family": "glm",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "qwen3-next": {
            "model_id": {
                "us": "qwen.qwen3-next-80b-a3b"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "qwen3-coder-next": {
            "model_id": {
                "us": "qwen.qwen3-coder-next"
            },
            "available_regions": ["us-east-1"],
            "preferred_region": "us-east-1",
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
            "region": "us-east-1"
        },
        "nova-2-lite": {
            "model_id": {
                "us": "us.amazon.nova-2-lite-v1:0"
            },
            "family": "nova",
            "context_window": 1000000,
            "token_limit": 1000000,
            "supported_parameters": ["temperature", "top_p", "max_tokens"],
        },
        "glm-5": {
            "model_id": {
                "us": "zai.glm-5"
            },
            "family": "glm",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 200000,
            "context_window": 200000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "llama4-scout": {
            "model_id": {
                "us": "us.meta.llama4-scout-17b-instruct-v1:0"
            },
            "family": "oss_openai_gpt",
            # No wrapper_class: Llama 4 rejects the OpenAI Chat Completions
            # invoke body ("required key [prompt] not found") — use Converse.
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
        },
        "llama4-maverick": {
            "model_id": {
                "us": "us.meta.llama4-maverick-17b-instruct-v1:0"
            },
            "family": "oss_openai_gpt",
            # No wrapper_class — same Converse routing as llama4-scout above.
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
        },
        "mistral-large-3": {
            "model_id": {
                "us": "mistral.mistral-large-3-675b-instruct"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 256000,
            "context_window": 256000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "devstral-2": {
            "model_id": {
                "us": "mistral.devstral-2-123b"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 256000,
            "context_window": 256000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "minimax-m2.5": {
            "model_id": {
                "us": "minimax.minimax-m2.5"
            },
            "family": "minimax",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 1000000,
            "context_window": 1000000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "qwen3-vl-235b": {
            "model_id": {
                "us": "qwen.qwen3-vl-235b-a22b"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 256000,
            "context_window": 256000,
            "supports_vision": True,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "qwen3-235b": {
            "model_id": {
                "us": "qwen.qwen3-235b-a22b-2507-v1:0"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 256000,
            "context_window": 256000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "qwen3-32b": {
            "model_id": {
                "us": "qwen.qwen3-32b-v1:0"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "qwen3-coder-30b": {
            "model_id": {
                "us": "qwen.qwen3-coder-30b-a3b-v1:0"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 256000,
            "context_window": 256000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "minimax-m2": {
            "model_id": {
                "us": "minimax.minimax-m2"
            },
            "family": "minimax",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 1000000,
            "context_window": 1000000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "nemotron-nano-9b": {
            "model_id": {
                "us": "nvidia.nemotron-nano-9b-v2"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "nemotron-nano-12b-vl": {
            "model_id": {
                "us": "nvidia.nemotron-nano-12b-v2"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "supports_vision": True,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "nemotron-nano-3-30b": {
            "model_id": {
                "us": "nvidia.nemotron-nano-3-30b"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "nemotron-super-3-120b": {
            "model_id": {
                "us": "nvidia.nemotron-super-3-120b"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "gemma3-27b": {
            "model_id": {
                "us": "google.gemma-3-27b-it"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "supports_vision": True,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "gemma3-12b": {
            "model_id": {
                "us": "google.gemma-3-12b-it"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "supports_vision": True,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "gemma3-4b": {
            "model_id": {
                "us": "google.gemma-3-4b-it"
            },
            "family": "oss_openai_gpt",
            "wrapper_class": "OpenAIBedrock",
            "max_input_tokens": 128000,
            "context_window": 128000,
            "supports_vision": True,
            "default_max_output_tokens": 4096,
            "region": "us-west-2"
        },
        "fable5": {
            "tier": "frontier",
            # Fable 5 is now routed via the Bedrock Mantle endpoint (not
            # bedrock-runtime), so its data-retention opt-in is scoped to
            # the mantle-only account/region switch (ensure_mantle_data_retention_mode)
            # instead of the classic account-wide bedrock:PutAccountDataRetention
            # switch every other model (Sonnet/Opus/etc.) shares. This is the
            # deconfliction fix: opting Fable 5 into provider_data_share no
            # longer touches the classic switch other users' sessions rely on.
            # Mantle has no geo/global inference profiles for Fable 5 yet —
            # a plain model_id string, single region, matching mythos5's shape.
            "model_id": "anthropic.claude-fable-5",
            "available_regions": ["us-east-1"],
            "preferred_region": "us-east-1",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32000,
            "max_iterations": 10,
            "timeout_multiplier": 8,
            "is_advanced_model": True,
            "supports_max_input_tokens": True,
            "supports_thinking": True,
            "family": "claude",
            "supports_context_caching": True,
            "supports_adaptive_thinking": True,
            "thinking_effort_default": "medium",
            "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
            "supports_vision": True,
            "supports_assistant_prefill": False,
            "endpoint_override": "bedrock-mantle",
            # Fable 5 (Mythos-class) requires temperature=1.0 (or unset) and
            # top_p >= 0.99 (or unset); top_k is not supported. Steer via
            # the effort parameter instead.
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
            # Bedrock Mantle requires the mantle-scoped data retention mode
            # set to 'provider_data_share' before invocations succeed. Ziya
            # applies this automatically at startup via ensure_mantle_data_retention_mode
            # (app/main.py), which is independent of the classic bedrock-runtime switch.
            "requires_provider_data_share": True,
        },
        "mythos5": {
            # Mythos 5 is in limited preview for cybersecurity/life sciences.
            # It uses the bedrock-mantle endpoint exclusively (not bedrock-runtime)
            # and has no geo/global inference profiles.
            "model_id": "anthropic.claude-mythos-5",
            "available_regions": ["us-east-1"],
            "preferred_region": "us-east-1",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32000,
            "max_iterations": 10,
            "timeout_multiplier": 8,
            "is_advanced_model": True,
            "supports_max_input_tokens": True,
            "supports_thinking": True,
            "family": "claude",
            "supports_context_caching": True,
            "supports_adaptive_thinking": True,
            "thinking_effort_default": "high",
            "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
            "supports_vision": True,
            "supports_assistant_prefill": False,
            "preview": True,
            "endpoint_override": "bedrock-mantle",
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
        },
        # GPT-5.6 family (Sol/Terra/Luna) — served exclusively through the
        # bedrock-mantle gateway on its OpenAI Responses API path
        # (/openai/v1/responses).  This is neither bedrock-runtime
        # invoke_model (the OpenAIBedrock wrapper path) nor the Anthropic
        # Messages path fable5/mythos5 use; "mantle_api" routes these to
        # OpenAIResponsesMantleProvider in the factory/ModelManager.
        "gpt-5.6-sol": {
            "model_id": "openai.gpt-5.6-sol",
            "available_regions": ["us-east-1", "us-east-2"],
            "preferred_region": "us-east-1",
            "family": "openai-gpt",
            "token_limit": 272000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_thinking": True,
            "thinking_effort_default": "medium",
            "supported_efforts": ["low", "medium", "high"],
            "supports_vision": True,
            "native_function_calling": True,
            "supports_assistant_prefill": False,
            "endpoint_override": "bedrock-mantle",
            "mantle_api": "openai-responses",
            # Mantle is an account/region-wide retention switch shared with
            # fable5/mythos5.  Declaring the same 'provider_data_share' mode
            # keeps all mantle models on one mode so selecting gpt-5.6 never
            # resets the switch out from under fable5 (main.py startup hook).
            "requires_provider_data_share": True,
            "unsupported_parameters": ["temperature"],
        },
        "gpt-5.6-terra": {
            "model_id": "openai.gpt-5.6-terra",
            "available_regions": ["us-east-1", "us-east-2", "us-west-2"],
            "preferred_region": "us-east-1",
            "family": "openai-gpt",
            "token_limit": 272000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_thinking": True,
            "thinking_effort_default": "medium",
            "supported_efforts": ["low", "medium", "high"],
            "supports_vision": True,
            "native_function_calling": True,
            "supports_assistant_prefill": False,
            "endpoint_override": "bedrock-mantle",
            "mantle_api": "openai-responses",
            "requires_provider_data_share": True,
            "unsupported_parameters": ["temperature"],
        },
        "gpt-5.6-luna": {
            "model_id": "openai.gpt-5.6-luna",
            "available_regions": ["us-east-1", "us-east-2", "us-west-2"],
            "preferred_region": "us-east-1",
            "family": "openai-gpt",
            "token_limit": 272000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_thinking": True,
            "thinking_effort_default": "medium",
            "supported_efforts": ["low", "medium", "high"],
            "supports_vision": True,
            "native_function_calling": True,
            "supports_assistant_prefill": False,
            "endpoint_override": "bedrock-mantle",
            "mantle_api": "openai-responses",
            "requires_provider_data_share": True,
            "unsupported_parameters": ["temperature"],
        },
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
            "tier": "small",
            "model_id": "gemini-2.5-flash",
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
            "native_function_calling": True,
        },
        "gemini-2.0-flash-lite": {
            "tier": "xsmall",
            "model_id": "gemini-2.0-flash-lite",
            "token_limit": 1048576,
            "family": "gemini-flash",
            "max_output_tokens": 8192,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": True,
        },
        "gemini-3.1-pro": {
            "tier": "medium",
            "model_id": "gemini-3.1-pro-preview",
            "token_limit": 1048576,
            "family": "gemini-3",
            "max_output_tokens": 65536,
            "default_max_output_tokens": 32768,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": True,
            "thinking_level": "medium"
        },
        "gemini-3.1-pro-customtools": {
            "model_id": "gemini-3.1-pro-preview-customtools",
            "token_limit": 1048576,
            "family": "gemini-3",
            "max_output_tokens": 65536,
            "default_max_output_tokens": 32768,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": True,
            "thinking_level": "medium"
        },
        "gemini-latest": {
            "model_id": "gemini-pro-latest",
            "token_limit": 1048576,
            "family": "gemini-3",
            "max_output_tokens": 65536,
            "default_max_output_tokens": 32768,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": True,
            "thinking_level": "medium"
        },
        "gemini-3-flash": {
            "model_id": "gemini-3-flash-preview",
            "token_limit": 1048576,
            "family": "gemini-3",
            "max_output_tokens": 65536,
            "default_max_output_tokens": 32768,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": True,
            "thinking_level": "medium"
        },
        "gemini-3.5-flash": {
            "model_id": "gemini-3.5-flash",
            "token_limit": 1048576,
            "family": "gemini-flash",
            "max_output_tokens": 65536,
            "default_max_output_tokens": 32768,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": True,
            "thinking_level": "medium"
        },
        "gemini-2.5-flash-lite": {
            "model_id": "gemini-2.5-flash-lite",
            "token_limit": 1048576,
            "family": "gemini-flash",
            "max_output_tokens": 65536,
            "convert_system_message_to_human": False,
            "supports_vision": True,
            "supports_function_calling": True,
            "native_function_calling": True,
            "supports_thinking": True,
        },
    },
    "openai": {
        "gpt-5.5": {
            "tier": "medium",
            "model_id": "gpt-5.5",
            "family": "openai-gpt",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "gpt-5.5-pro": {
            "tier": "large",
            "model_id": "gpt-5.5-pro",
            "family": "openai-gpt",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_thinking": True,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "gpt-5.5-mini": {
            "tier": "small",
            "model_id": "gpt-5.5-mini",
            "family": "openai-gpt",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "gpt-5.5-nano": {
            "tier": "xsmall",
            "model_id": "gpt-5.5-nano",
            "family": "openai-gpt",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "gpt-5.4": {
            "model_id": "gpt-5.4",
            "family": "openai-gpt",
            "token_limit": 272000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "gpt-5.4-pro": {
            "model_id": "gpt-5.4-pro",
            "family": "openai-gpt",
            "token_limit": 1050000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_thinking": True,
            "native_function_calling": True,
        },
        "gpt-5.4-mini": {
            "model_id": "gpt-5.4-mini",
            "family": "openai-gpt",
            "token_limit": 400000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "gpt-5.4-nano": {
            "model_id": "gpt-5.4-nano",
            "family": "openai-gpt",
            "token_limit": 400000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "gpt-4.1": {
            "model_id": "gpt-4.1",
            "family": "openai-gpt",
            "token_limit": 200000,
            "max_output_tokens": 32768,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "gpt-4.1-mini": {
            "model_id": "gpt-4.1-mini",
            "family": "openai-gpt",
            "token_limit": 200000,
            "max_output_tokens": 32768,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "gpt-4o": {
            "model_id": "gpt-4o",
            "family": "openai-gpt",
            "token_limit": 128000,
            "max_output_tokens": 16384,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "gpt-4o-mini": {
            "model_id": "gpt-4o-mini",
            "family": "openai-gpt",
            "token_limit": 128000,
            "max_output_tokens": 16384,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "native_function_calling": True,
        },
        "o3": {
            "model_id": "o3",
            "family": "openai-gpt",
            "token_limit": 200000,
            "max_output_tokens": 100000,
            "default_max_output_tokens": 32768,
            "supports_thinking": True,
            "native_function_calling": True,
        },
        "o3-mini": {
            "model_id": "o3-mini",
            "family": "openai-gpt",
            "token_limit": 200000,
            "max_output_tokens": 65536,
            "default_max_output_tokens": 16384,
            "supports_thinking": True,
            "native_function_calling": True,
        },
        "o4-mini": {
            "model_id": "o4-mini",
            "family": "openai-gpt",
            "token_limit": 200000,
            "max_output_tokens": 100000,
            "default_max_output_tokens": 32768,
            "supports_thinking": True,
            "native_function_calling": True,
        },
    },
    "anthropic": {
        "claude-sonnet-4-6": {
            "model_id": "claude-sonnet-4-6",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 64000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "supports_adaptive_thinking": True,
            "native_function_calling": True,
        },
        "claude-sonnet-5": {
            "tier": "medium",
            "model_id": "claude-sonnet-5",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 64000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "supports_adaptive_thinking": True,
            "native_function_calling": True,
        },
        "claude-sonnet-4-5": {
            "model_id": "claude-sonnet-4-5-20250929",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 64000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "native_function_calling": True,
        },
        "claude-opus-4-6": {
            "model_id": "claude-opus-4-6",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "supports_adaptive_thinking": True,
            "native_function_calling": True,
        },
        "claude-opus-4-7": {
            "model_id": "claude-opus-4-7",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "supports_adaptive_thinking": True,
            "native_function_calling": True,
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
        },
        "claude-opus-4-8": {
            "tier": "large",
            "model_id": "claude-opus-4-8",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "supports_adaptive_thinking": True,
            "native_function_calling": True,
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
        },
        "claude-opus-5": {
            "tier": "large",
            "model_id": "claude-opus-5",
            "family": "claude",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "supports_adaptive_thinking": True,
            "native_function_calling": True,
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
        },
        "claude-sonnet-4": {
            "model_id": "claude-sonnet-4-20250514",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 64000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "native_function_calling": True,
        },
        "claude-opus-4": {
            "model_id": "claude-opus-4-20250514",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "native_function_calling": True,
        },
        "claude-opus-4-1": {
            "model_id": "claude-opus-4-1-20250805",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "native_function_calling": True,
        },
        "claude-opus-4-5": {
            "model_id": "claude-opus-4-5-20251101",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "native_function_calling": True,
        },
        "claude-haiku-4-5": {
            "tier": "xsmall",
            "model_id": "claude-haiku-4-5-20251001",
            "family": "claude",
            "token_limit": 200000,
            "max_output_tokens": 64000,
            "default_max_output_tokens": 16384,
            "supports_vision": True,
            "supports_thinking": True,
            "native_function_calling": True,
        },
        "claude-fable-5": {
            "tier": "frontier",
            "model_id": "claude-fable-5",
            "family": "claude",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32000,
            "supports_vision": True,
            "supports_thinking": True,
            "supports_adaptive_thinking": True,
            "native_function_calling": True,
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
        },
        "claude-mythos-5": {
            "model_id": "claude-mythos-5",
            "family": "claude",
            "token_limit": 1000000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32000,
            "supports_vision": True,
            "supports_thinking": True,
            "supports_adaptive_thinking": True,
            "native_function_calling": True,
            "preview": True,
            "unsupported_parameters": ["temperature", "top_k", "top_p"],
        },
    },
    "zai": {
        "glm-5.2": {
            "tier": "medium",
            "model_id": "glm-5.2",
            "family": "zai-glm",
            "token_limit": 1000000,
            "max_output_tokens": 131072,
            "default_max_output_tokens": 32768,
            "supports_thinking": True,
            "native_function_calling": True,
        },
        "glm-4.6": {
            "tier": "small",
            "model_id": "glm-4.6",
            "family": "zai-glm",
            "token_limit": 200000,
            "max_output_tokens": 128000,
            "default_max_output_tokens": 32768,
            "supports_thinking": True,
            "native_function_calling": True,
        },
    },
    "meta": {
        # Meta's API rejects any tool schema containing $ref with
        # "400 Recursive JSON schemas are not currently supported". Tool
        # definitions are sent as one array, so a single recursive schema
        # (MCP canvas block trees, or anything from Pydantic's
        # model_json_schema()) fails every request. inline_schema_refs makes
        # the OpenAI-compatible provider expand refs before sending. This is
        # an endpoint-wide API constraint, hence set on every meta model.
        "muse-spark-1.2": {
            "tier": "medium",
            "model_id": "muse-spark-1.2",
            "family": "meta-muse",
            "token_limit": 1048576,
            "max_output_tokens": 131072,
            "default_max_output_tokens": 16384,
            "supports_thinking": True,
            "native_function_calling": True,
            "supports_vision": True,
            "inline_schema_refs": True,
        },
        "muse-spark-1.1": {
            "tier": "small",
            "model_id": "muse-spark-1.1",
            "family": "meta-muse",
            "token_limit": 1048576,
            "max_output_tokens": 131072,
            "default_max_output_tokens": 16384,
            "supports_thinking": True,
            "native_function_calling": True,
            "supports_vision": True,
            "inline_schema_refs": True,
        },
        # Contributor tier: same model as muse-spark-1.2 at roughly a tenth
        # of the price (~$0.10/$0.20 per M vs ~$1.25/$4.25) in exchange for
        # Meta using your prompts and completions to train future models.
        #
        # Deliberately NOT aliased to a short name and NOT tier-tagged, so no
        # tier request, delegate, or Task Card block can route here
        # implicitly. Ziya sends source code, design docs and shell output to
        # the model, so opting that corpus into a vendor's training set must
        # be an explicit, typed-out choice — never a cost optimisation the
        # harness makes on the user's behalf.
        "muse-spark-1.2-contributor": {
            "model_id": "muse-spark-1.2-contributor",
            "family": "meta-muse",
            "token_limit": 1048576,
            "max_output_tokens": 131072,
            "default_max_output_tokens": 16384,
            "supports_thinking": True,
            "native_function_calling": True,
            "supports_vision": True,
            "shares_data_for_training": True,
            "inline_schema_refs": True,
        },
    },
}

# Environment variable mapping to config keys
ENV_VAR_MAPPING = {
    "ZIYA_TEMPERATURE": "temperature",
    "ZIYA_TOP_K": "top_k",
    "ZIYA_TOP_P": "top_p",
    "ZIYA_MAX_OUTPUT_TOKENS": "max_output_tokens",
    # Read back so get_model_settings() surfaces the stored input ceiling.
    # Without this entry the value written to ZIYA_MAX_INPUT_TOKENS was
    # never loaded, and /api/model-capabilities always fell through to the
    # model's static token_limit — the setting had no observable effect.
    "ZIYA_MAX_INPUT_TOKENS": "max_input_tokens",
    "ZIYA_THINKING_MODE": "thinking_mode",
    "ZIYA_MAX_TOKENS": "max_tokens",
    "ZIYA_MODEL_ID_OVERRIDE": "model_id",
    "AWS_REGION": "region",
    "ZIYA_THINKING_LEVEL": "thinking_level",
    "ZIYA_THINKING_EFFORT": "thinking_effort"
}

# Default request size limits
DEFAULT_MAX_REQUEST_SIZE_MB = 10

# MCP Tool sentinel configuration - single env var for tag name
TOOL_SENTINEL_TAG = os.environ.get("ZIYA_TOOL_SENTINEL", "TOOL_SENTINEL")
TOOL_SENTINEL_OPEN = f"<{TOOL_SENTINEL_TAG}>"
TOOL_SENTINEL_CLOSE = f"</{TOOL_SENTINEL_TAG}>"

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
    
    # Subtract any parameters explicitly marked unsupported. This lets a
    # specific model opt out of family-level defaults (e.g. Opus 4.7 rejects
    # temperature/top_p/top_k even though the claude family supports them).
    unsupported = set()
    unsupported.update(family_config.get("unsupported_parameters", []))
    if parent_family_config:
        unsupported.update(parent_family_config.get("unsupported_parameters", []))
    unsupported.update(model_specific_config.get("unsupported_parameters", []))
    supported_params -= unsupported

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

# Canonical fallback for max_output_tokens when no model-specific or
# endpoint-specific override is configured.  Every call site should
# reference this constant instead of hardcoding a number.
DEFAULT_MAX_OUTPUT_TOKENS = 32768

# User-defined model allowlist — None means no restriction
_user_allowed_models = None


def get_user_allowed_models():
    """Return the user's personal model allowlist as a set, or None if unrestricted."""
    return _user_allowed_models


def _load_user_model_config() -> None:
    """
    Load user-local model configuration from ~/.ziya/models.json.

    Two independent capabilities:

    1. ALLOWLIST — restrict the model picker to a named subset.
       Useful for personal AWS accounts where only certain models are
       enabled or budgeted.  Global model definitions are unchanged;
       only the visible list is filtered.

           { "allowed_models": ["sonnet4.0", "haiku-4.5", "nova-lite"] }

    2. CUSTOM ENTRIES — add model definitions not in the global config,
       e.g. custom inference profile ARNs.  Merged on top of global
       config; existing entries are updated, new ones are added.

           {
             "bedrock": {
               "my-profile": {
                 "model_id": "arn:aws:bedrock:us-east-1:123:inference-profile/...",
                 "family": "claude",
                 "max_output_tokens": 64000
               }
             }
           }

    Both sections are optional and independent.
    """
    global _user_allowed_models

    config_path = os.path.join(os.path.expanduser("~"), ".ziya", "models.json")
    if not os.path.exists(config_path):
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)

        if "allowed_models" in user_config:
            _user_allowed_models = set(user_config["allowed_models"])

        for endpoint, models in user_config.items():
            if endpoint == "allowed_models" or not isinstance(models, dict):
                continue
            if endpoint not in MODEL_CONFIGS:
                MODEL_CONFIGS[endpoint] = {}
            for model_name, model_cfg in models.items():
                if model_name in MODEL_CONFIGS[endpoint]:
                    MODEL_CONFIGS[endpoint][model_name].update(model_cfg)
                else:
                    MODEL_CONFIGS[endpoint][model_name] = model_cfg

        import logging
        logging.getLogger(__name__).info(
            f"Loaded ~/.ziya/models.json"
            + (f": allowlist={sorted(_user_allowed_models)}" if _user_allowed_models else "")
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to load ~/.ziya/models.json: {e}")


_load_user_model_config()


# ─── Schema Validation ────────────────────────────────────────────────────────
# Known valid keys for model configuration entries.  Adding a key here
# is what makes it "declared" — typos in MODEL_CONFIGS will be caught.
_VALID_MODEL_CONFIG_KEYS = frozenset({
    "available_regions", "context_window", "convert_system_message_to_human",
    "default_max_output_tokens", "effort_beta_required", "endpoint_override",
    "enforce_size_limit", "extended_context_header", "extended_context_limit",
    "family", "inference_parameters", "internal_parameters",
    "is_advanced_model", "mantle_api", "max_input_tokens", "max_iterations",
    "max_output_tokens", "max_request_size_mb", "max_thinking_tokens", "max_tokens",
    "message_format", "model_id", "model_name", "native_function_calling",
    "parameter_mappings", "parameter_ranges", "parent", "preferred_region",
    "preferred_regions", "preview", "region", "region_restricted",
    "region_router_class", "requires_provider_data_share",
    "reasoning_request", "supports_reasoning_effort",
    "service_name", "stop_sequences", "supports_cache", "supported_efforts",
    "shares_data_for_training", "supported_parameters", "supports_adaptive_thinking",
    "supports_assistant_prefill", "supports_context_caching",
    "supports_extended_context", "supports_function_calling",
    "supports_max_input_tokens", "supports_multimodal", "supports_streaming",
    "supports_thinking", "supports_vision", "temperature",
    "thinking_budget", "thinking_effort_default", "thinking_level",
    "tier", "timeout_multiplier", "token_limit", "top_k", "top_p",
    "unsupported_parameters", "wrapper_class",
})

_VALID_FAMILY_KEYS = frozenset({
    "available_regions", "context_window", "default_max_output_tokens",
    "family", "inference_parameters", "max_output_tokens", "model_id",
    "internal_parameters", "message_format", "native_function_calling",
    "parameter_mappings", "parameter_ranges", "parent", "preferred_region",
    "region", "stop_sequences", "supported_efforts",
    "reasoning_request", "supports_reasoning_effort",
    "supported_parameters", "supports_adaptive_thinking",
    "supports_assistant_prefill", "supports_context_caching",
    "supports_extended_context", "supports_function_calling",
    "supports_max_input_tokens", "supports_multimodal", "supports_streaming",
    "supports_thinking", "supports_vision", "thinking_effort_default",
    "thinking_level", "token_limit", "unsupported_parameters",
    "wrapper_class",
})


def validate_model_configs() -> list[str]:
    """Validate MODEL_CONFIGS, MODEL_FAMILIES, and MODEL_ALIASES for common errors.

    Returns a list of human-readable issue descriptions (empty = all good).
    Called at startup so misconfigurations surface immediately.
    """
    issues: list[str] = []

    # 1. Check for unknown keys in model configs (catches typos)
    for endpoint, models in MODEL_CONFIGS.items():
        for model_name, cfg in models.items():
            unknown = set(cfg.keys()) - _VALID_MODEL_CONFIG_KEYS
            if unknown:
                issues.append(
                    f"[{endpoint}/{model_name}] unknown config keys: {sorted(unknown)}"
                )

    # 2. Check for unknown keys in family definitions
    for family_name, cfg in MODEL_FAMILIES.items():
        unknown = set(cfg.keys()) - _VALID_FAMILY_KEYS
        if unknown:
            issues.append(
                f"[family/{family_name}] unknown keys: {sorted(unknown)}"
            )

    # 3. Validate family references exist
    for endpoint, models in MODEL_CONFIGS.items():
        for model_name, cfg in models.items():
            family_ref = cfg.get("family")
            if family_ref and family_ref not in MODEL_FAMILIES:
                issues.append(
                    f"[{endpoint}/{model_name}] references non-existent family '{family_ref}'"
                )

    # 4. Validate parent references in families
    for family_name, cfg in MODEL_FAMILIES.items():
        parent_ref = cfg.get("parent")
        if parent_ref and parent_ref not in MODEL_FAMILIES:
            issues.append(
                f"[family/{family_name}] references non-existent parent '{parent_ref}'"
            )

    # 5. Validate aliases point to real models
    for endpoint, aliases in MODEL_ALIASES.items():
        endpoint_models = MODEL_CONFIGS.get(endpoint, {})
        for alias, target in aliases.items():
            if target not in endpoint_models:
                issues.append(
                    f"[alias/{endpoint}] '{alias}' → '{target}' but '{target}' not in MODEL_CONFIGS['{endpoint}']"
                )

    # 6. Validate per-model tier tags: known rung names, and every
    #    endpoint defines the center rung 'medium' (the default model and
    #    the resolve_tier_model fallback target).  Missing OTHER rungs are
    #    fine — resolve rounds up to the nearest defined one — so only
    #    'medium' is required.
    for endpoint, models in MODEL_CONFIGS.items():
        seen_tiers = set()
        for model_name, cfg in models.items():
            t = cfg.get("tier")
            if t is None:
                continue
            if t not in _TIER_ORDER:
                issues.append(
                    f"[tier/{endpoint}/{model_name}] unknown tier '{t}' "
                    f"(valid: {', '.join(_TIER_ORDER)})"
                )
            seen_tiers.add(t)
        if seen_tiers and DEFAULT_TIER not in seen_tiers:
            issues.append(
                f"[tier/{endpoint}] no model tagged '{DEFAULT_TIER}' (the center/"
                f"default rung) — resolve_tier_model falls back to DEFAULT_MODELS"
            )

    return issues
