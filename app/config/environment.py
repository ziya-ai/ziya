"""
Shared environment setup for all Ziya entry points (server, CLI).

This is the single source of truth for translating parsed CLI arguments into
environment variables.  Entry-point-specific code (server's AST/MCP flags,
CLI's debug/logger reconfiguration) stays in the respective caller —
everything else lives here.
"""

import os
import sys
from typing import Any

from app.utils.logging_utils import logger
import app.config.models_config as config


# ---------------------------------------------------------------------------
# Helpers (moved from main.py so both entry points can use them)
# ---------------------------------------------------------------------------

def find_endpoint_for_model(model: str):
    """Find which endpoint contains the specified model."""
    for endpoint, models in config.MODEL_CONFIGS.items():
        if model in models:
            return endpoint
    return None


def validate_model_and_endpoint(endpoint, model, explicit_endpoint=False):
    """
    Validate that the specified endpoint and model are compatible.

    Returns:
        tuple: (is_valid, error_message, corrected_endpoint)
    """
    # Auto-detect endpoint for the model when user didn't explicitly pick one
    if (model and endpoint in config.MODEL_CONFIGS
            and model not in config.MODEL_CONFIGS[endpoint]
            and not explicit_endpoint):
        correct_endpoint = find_endpoint_for_model(model)
        if correct_endpoint:
            return True, None, correct_endpoint

    if endpoint not in config.MODEL_CONFIGS:
        valid_endpoints = ", ".join(config.MODEL_CONFIGS.keys())
        return False, (f"Invalid endpoint: '{endpoint}'. "
                       f"Valid endpoints are: {valid_endpoints}"), None

    if model is None:
        model = config.DEFAULT_MODELS.get(endpoint)

    if model not in config.MODEL_CONFIGS[endpoint]:
        valid_models = ", ".join(config.MODEL_CONFIGS[endpoint].keys())
        return False, (f"Invalid model: '{model}' for endpoint '{endpoint}'. "
                       f"Valid models are: {valid_models}"), None

    return True, None, endpoint


def _was_flag_explicit(flag_name: str) -> bool:
    """Return True if *--flag_name* appeared explicitly on the command line."""
    return any(
        arg == f'--{flag_name}' or arg.startswith(f'--{flag_name}=')
        for arg in sys.argv[1:]
    )


# ---------------------------------------------------------------------------
# Core shared setup
# ---------------------------------------------------------------------------

def setup_environment(args: Any) -> None:
    """
    Translate parsed CLI arguments into environment variables.

    Handles every setting that is common to *all* Ziya entry points:
    root directory, file inclusion/exclusion, AWS profile & region
    (with model-specific defaults), endpoint + model validation &
    auto-detection, model parameter flags, model ID override, and
    ZIYA_TEMPLATES_DIR.

    Callers add entry-point-specific extras *after* this returns
    (e.g. AST/MCP/ephemeral for the server; debug logging for the CLI).
    """

    # -- Root directory (always direct-assign; never setdefault) ------------
    root_dir = getattr(args, 'root', None) or os.getcwd()
    os.environ["ZIYA_USER_CODEBASE_DIR"] = root_dir

    # -- File inclusion / exclusion -----------------------------------------
    exclude = getattr(args, 'exclude', None)
    if exclude:
        os.environ["ZIYA_ADDITIONAL_EXCLUDE_DIRS"] = ','.join(exclude)

    include_only = getattr(args, 'include_only', None)
    if include_only:
        os.environ["ZIYA_INCLUDE_ONLY_DIRS"] = ','.join(include_only)
        logger.info(f"Only including specified directories/files: {','.join(include_only)}")

    include = getattr(args, 'include', None)
    if include:
        os.environ["ZIYA_INCLUDE_DIRS"] = ','.join(include)
        logger.info(f"Including external paths: {','.join(include)}")

    # -- AWS profile --------------------------------------------------------
    endpoint = getattr(args, 'endpoint', config.DEFAULT_ENDPOINT)
    profile = getattr(args, 'profile', None)

    if endpoint == "google" and profile:
        logger.error("--profile is for AWS Bedrock and cannot be used with --endpoint google.")
        logger.error("Please remove --profile or use --endpoint bedrock.")
        sys.exit(1)

    if profile:
        os.environ["ZIYA_AWS_PROFILE"] = profile
        os.environ["AWS_PROFILE"] = profile
        logger.info(f"Using AWS profile: {profile}")

    # -- AWS region ---------------------------------------------------------
    # args.region is None when the user didn't pass --region (default changed
    # to None in common_args).  In that case we fall through to model-specific
    # or global defaults so MODEL_DEFAULT_REGIONS actually takes effect.
    explicit_region = getattr(args, 'region', None)
    model = getattr(args, 'model', None)

    if explicit_region:
        os.environ["AWS_REGION"] = explicit_region
        logger.info(f"Using AWS region from command line: {explicit_region}")
    elif model and model in config.MODEL_DEFAULT_REGIONS:
        region = config.MODEL_DEFAULT_REGIONS[model]
        os.environ["AWS_REGION"] = region
        logger.info(f"Using model-specific default region for {model}: {region}")
    else:
        os.environ["AWS_REGION"] = config.DEFAULT_REGION
        logger.info(f"Using default region: {config.DEFAULT_REGION}")

    # -- Endpoint + model validation ----------------------------------------
    explicit_endpoint = _was_flag_explicit('endpoint')
    is_valid, error_message, corrected_endpoint = validate_model_and_endpoint(
        endpoint, model, explicit_endpoint=explicit_endpoint,
    )
    if not is_valid:
        logger.error(error_message)
        sys.exit(1)

    if corrected_endpoint and corrected_endpoint != endpoint:
        logger.info(f"Auto-detected endpoint '{corrected_endpoint}' for model '{model}'")
        endpoint = corrected_endpoint
        if hasattr(args, 'endpoint'):
            args.endpoint = corrected_endpoint

    os.environ["ZIYA_ENDPOINT"] = endpoint
    if model:
        os.environ["ZIYA_MODEL"] = model

    # -- Model parameter flags ----------------------------------------------
    if getattr(args, 'temperature', None) is not None:
        os.environ["ZIYA_TEMPERATURE"] = str(args.temperature)
    if getattr(args, 'top_p', None) is not None:
        os.environ["ZIYA_TOP_P"] = str(args.top_p)
    if getattr(args, 'top_k', None) is not None:
        os.environ["ZIYA_TOP_K"] = str(args.top_k)
    if getattr(args, 'max_output_tokens', None) is not None:
        os.environ["ZIYA_MAX_OUTPUT_TOKENS"] = str(args.max_output_tokens)
    if getattr(args, 'thinking_level', None) is not None:
        os.environ["ZIYA_THINKING_LEVEL"] = args.thinking_level

    # -- Model ID override --------------------------------------------------
    if getattr(args, 'model_id', None) is not None:
        os.environ["ZIYA_MODEL_ID_OVERRIDE"] = args.model_id
        logger.info(f"Overriding model ID with: {args.model_id}")

    # -- Templates directory ------------------------------------------------
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates_dir = os.path.join(current_dir, "templates")
    os.environ["ZIYA_TEMPLATES_DIR"] = templates_dir

    # -- Memory system (experimental, opt-in) --------------------------------
    if getattr(args, 'memory', False):
        os.environ["ZIYA_ENABLE_MEMORY"] = "true"
        logger.info("Persistent memory system enabled (experimental)")
