"""
Bedrock client cache — creates and caches boto3 bedrock-runtime clients.

Extracted from ModelManager to break the circular dependency:
    providers/bedrock → agents/models → providers/

This module is a leaf: it imports only from app/utils/ and stdlib.
Both app/providers/bedrock.py and app/agents/models.py import from here.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Dict, Optional

from app.utils.logging_utils import logger

# Process-global client cache. Keyed by config hash → ThrottleSafeBedrock.
_client_cache: Dict[str, Any] = {}
_current_config_hash: Optional[str] = None


def get_client_config_hash(aws_profile: str, region: str, model_id: str) -> str:
    """Generate a stable hash for client configuration to enable reuse."""
    config_string = f"{aws_profile}_{region}_{model_id}"
    return hashlib.md5(config_string.encode()).hexdigest()[:8]


def get_persistent_bedrock_client(
    aws_profile: str,
    region: str,
    model_id: str,
    model_config: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    Get or create a persistent Bedrock client for the given configuration.

    Reuses existing clients when the (profile, region, model_id) triple
    matches a previously created client.  New clients are wrapped with
    CustomBedrockClient (for model-config-aware request building) and
    ThrottleSafeBedrock (for transparent retry on 429s).
    """
    global _current_config_hash

    from app.utils.aws_utils import ThrottleSafeBedrock, create_fresh_boto3_session
    from app.utils.custom_bedrock import CustomBedrockClient

    config_hash = get_client_config_hash(aws_profile, region, model_id)

    if config_hash in _client_cache:
        logger.debug(f"Reusing persistent Bedrock client for {aws_profile}/{region}/{model_id}")
        return _client_cache[config_hash]

    logger.info(f"Creating new persistent Bedrock client for {aws_profile}/{region}/{model_id}")

    session = create_fresh_boto3_session(profile_name=aws_profile)

    # Credential check with retry for transient failures
    from botocore.config import Config as BotoConfig
    sts = session.client(
        "sts", region_name=region,
        config=BotoConfig(connect_timeout=5, read_timeout=5),
    )
    max_retries, retry_delays = 2, [0.5, 1.0]
    for attempt in range(max_retries):
        try:
            identity = sts.get_caller_identity()
            logger.info(f"Authenticated as: {identity.get('Arn', 'Unknown')}")
            break
        except Exception as cred_error:
            err = str(cred_error)
            is_transient = any(s in err for s in [
                "Unable to locate credentials", "Could not connect",
                "Temporary failure", "timed out", "ConnectTimeoutError",
            ])
            is_permanent = any(s in err for s in [
                "InvalidClientTokenId", "ExpiredToken", "InvalidToken",
            ])
            if is_transient and not is_permanent and attempt < max_retries - 1:
                time.sleep(retry_delays[attempt])
            else:
                from app.utils.custom_exceptions import KnownCredentialException
                raise KnownCredentialException(
                    f"AWS credentials check failed: {cred_error}",
                    is_server_startup=False,
                )

    bedrock_client = session.client(
        "bedrock-runtime", region_name=region,
        config=BotoConfig(
            read_timeout=300, max_pool_connections=25,
            retries={"max_attempts": 2, "mode": "adaptive"},
        ),
    )

    custom_client = CustomBedrockClient(bedrock_client, model_config=model_config)
    throttle_safe_client = ThrottleSafeBedrock(custom_client)

    _client_cache[config_hash] = throttle_safe_client
    _current_config_hash = config_hash
    return throttle_safe_client


def clear_cache() -> None:
    """Reset the client cache (used by ModelManager._reset_state)."""
    global _current_config_hash
    _client_cache.clear()
    _current_config_hash = None
