"""
Bedrock Mantle LLM Provider — streams via the bedrock-mantle endpoint.

Bedrock Mantle is the OpenAI-/Anthropic-compatible gateway for Amazon Bedrock.
Anthropic models use the /anthropic/v1/messages path with SigV4 auth.
It is not accessible via boto3's bedrock-runtime client.

Authentication uses standard AWS IAM credentials (SigV4) via a custom httpx
transport, so no separate API key is needed.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from app.providers.anthropic_direct import AnthropicDirectProvider
from app.utils.logging_utils import get_mode_aware_logger

logger = get_mode_aware_logger(__name__)

# Base-URL template. The Anthropic SDK appends /v1/messages automatically.
_MANTLE_BASE_URL = "https://bedrock-mantle.{region}.api.aws/anthropic"


def resolve_mantle_region(model_config: Optional[Dict[str, Any]], region: Optional[str] = None) -> str:
    """Clamp a requested region to the model's mantle-available regions.

    Mantle model availability differs per region (e.g. anthropic.claude-fable-5
    exists on bedrock-mantle.us-east-1 but NOT us-west-2 — verified via
    GET /v1/models on both). The session's AWS_REGION frequently resolves to
    a region where the model doesn't exist, yielding a 404 "model does not
    exist". The model config's available_regions/preferred_region is the
    source of truth for mantle models; honor it here since the generic
    region-clamping in ModelManager only fires for dict-form model_ids.
    """
    region = region or os.environ.get(
        "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    cfg = model_config or {}
    avail = cfg.get("available_regions") or []
    if avail and region not in avail:
        clamped = cfg.get("preferred_region") or avail[0]
        logger.info(
            f"Mantle region {region} not in model's available_regions {avail}; "
            f"clamping to {clamped}"
        )
        return clamped
    return region


class _AsyncSigV4Transport(httpx.AsyncBaseTransport):
    """httpx async transport that signs every outgoing request with SigV4."""

    def __init__(self, region: str = "us-east-1", profile: str | None = None):
        self._region = region
        session = boto3.Session(profile_name=profile)
        creds = session.get_credentials()
        if creds is None:
            raise ValueError("No AWS credentials available for Bedrock Mantle")
        self._creds = creds.get_frozen_credentials()
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Remove the x-api-key header the SDK injects; it conflicts with SigV4.
        if "x-api-key" in request.headers:
            del request.headers["x-api-key"]
        aws_req = AWSRequest(
            method=request.method,
            url=str(request.url),
            headers=dict(request.headers),
            data=request.content,
        )
        SigV4Auth(self._creds, "bedrock", self._region).add_auth(aws_req)
        request.headers.update(dict(aws_req.headers))
        return await self._inner.handle_async_request(request)


class BedrockMantleProvider(AnthropicDirectProvider):
    """Anthropic Messages API over Bedrock Mantle with SigV4 auth.

    Uses AsyncAnthropic with a custom SigV4 transport pointed at the mantle
    base URL. No separate API key required — uses standard AWS credentials.
    """

    def __init__(
        self,
        model_id: str,
        model_config: Dict[str, Any],
        region: str = "us-east-1",
        aws_profile: Optional[str] = None,
    ):
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "The 'anthropic' package is required. Install with: pip install anthropic"
            )

        self.model_id = model_id
        self.model_config = model_config

        base_url = _MANTLE_BASE_URL.format(region=region)
        transport = _AsyncSigV4Transport(region=region, profile=aws_profile)
        self.client = anthropic.AsyncAnthropic(
            api_key="unused",  # Required by SDK init but SigV4 transport replaces auth.
            base_url=base_url,
            http_client=httpx.AsyncClient(transport=transport),
        )
        logger.info(f"BedrockMantleProvider: model={model_id} base_url={base_url}")

    # ------------------------------------------------------------------
    # Override: skip the count_tokens pre-flight (not available on mantle)
    # ------------------------------------------------------------------

    def _estimate_request_tokens(self, request_kwargs: Dict[str, Any]) -> int:
        """Character-based heuristic; mantle has no count_tokens endpoint."""
        import json
        total_chars = 0
        # Image content blocks carry no text but cost tokens (Claude vision
        # caps a single image at ~1568px ≈ 1600 tokens). The pre-flight is a
        # safety net that should err high, so count each image at that cap
        # rather than the zero it contributed before — a request full of
        # images could otherwise sail past _check_context_limit unbounded.
        image_tokens = 0
        system = request_kwargs.get("system")
        if isinstance(system, str):
            total_chars += len(system)
        elif isinstance(system, list):
            for block in system:
                if isinstance(block, dict):
                    total_chars += len(block.get("text", ""))
        for msg in request_kwargs.get("messages", []):
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(block.get("text", ""))
                        if "input" in block:
                            total_chars += len(json.dumps(block["input"]))
                        if block.get("type") == "image":
                            image_tokens += 1600
        tools = request_kwargs.get("tools", [])
        if tools:
            total_chars += int(len(json.dumps(tools)) * 2.5)
        return int(total_chars / 3.5) + image_tokens

    @property
    def provider_name(self) -> str:
        return "bedrock-mantle"
