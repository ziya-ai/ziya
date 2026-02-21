"""
Nova Web Grounding service.

Calls Amazon Nova models via the Bedrock Converse API with the
nova_grounding systemTool to perform web searches and return
cited results.  Used as the backend for the nova_web_search
builtin tool — the primary model (Claude) calls this tool when
it needs current web information.

The converse API is used (not invoke_model) because nova_grounding
is a systemTool that requires the toolConfig parameter.

Regional availability: US regions only (us-east-1, us-west-2).
IAM: Requires bedrock:InvokeTool on
      arn:aws:bedrock::*:system-tool/amazon.nova_grounding
"""

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

import boto3
from botocore.config import Config as BotoConfig

from app.utils.logging_utils import logger

# Supported grounding models, cheapest first
GROUNDING_MODELS = {
    "nova-2-lite": "us.amazon.nova-2-lite-v1:0",
    "nova-premier": "us.amazon.nova-premier-v1:0",
}

DEFAULT_GROUNDING_MODEL = "nova-2-lite"
DEFAULT_GROUNDING_REGION = "us-east-1"


@dataclass
class Citation:
    """A single web citation returned by Nova grounding."""
    url: str
    domain: str


@dataclass
class GroundingResult:
    """Parsed result from a Nova grounding call."""
    text: str
    citations: List[Citation] = field(default_factory=list)
    model_id: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: Optional[str] = None

    def format_for_tool_result(self) -> str:
        """
        Format the grounding result as a tool response string.

        Produces a readable block of grounded text followed by a
        numbered reference list that the primary model can cite.
        """
        if self.error:
            return f"Web search failed: {self.error}"

        if not self.text:
            return "Web search returned no results."

        parts = [self.text.strip()]

        if self.citations:
            parts.append("")
            parts.append("Sources:")
            seen = set()
            idx = 1
            for c in self.citations:
                if c.url not in seen:
                    seen.add(c.url)
                    parts.append(f"[{idx}] {c.url} ({c.domain})")
                    idx += 1

        return "\n".join(parts)


class GroundingService:
    """
    Client for Nova Web Grounding via the Bedrock Converse API.

    Creates its own lightweight boto3 client so it doesn't interfere
    with the primary model's streaming client or auth wrappers.
    """

    def __init__(
        self,
        model_key: str = DEFAULT_GROUNDING_MODEL,
        region: str = DEFAULT_GROUNDING_REGION,
        profile_name: Optional[str] = None,
    ):
        self.model_id = GROUNDING_MODELS.get(model_key, GROUNDING_MODELS[DEFAULT_GROUNDING_MODEL])
        self.region = region

        # Determine AWS profile from env or parameter
        profile = profile_name or os.environ.get("AWS_PROFILE") or os.environ.get("ZIYA_AWS_PROFILE", "ziya")

        try:
            session = boto3.Session(profile_name=profile)
            self._client = session.client(
                "bedrock-runtime",
                region_name=self.region,
                config=BotoConfig(
                    read_timeout=60,
                    retries={"max_attempts": 2, "mode": "adaptive"},
                ),
            )
            logger.info(f"🌐 GroundingService: Initialized with model={self.model_id}, region={self.region}")
        except Exception as e:
            logger.error(f"🌐 GroundingService: Failed to create client: {e}")
            self._client = None

    def query(self, user_query: str, system_prompt: Optional[str] = None) -> GroundingResult:
        """
        Send a query to Nova with web grounding enabled.

        Args:
            user_query: The search query or question.
            system_prompt: Optional system instruction to guide the search.

        Returns:
            GroundingResult with text, citations, and usage metadata.
        """
        if not self._client:
            return GroundingResult(text="", error="Grounding service client not available")

        start = time.time()

        messages = [
            {
                "role": "user",
                "content": [{"text": user_query}],
            }
        ]

        tool_config = {
            "tools": [
                {"systemTool": {"name": "nova_grounding"}}
            ]
        }

        kwargs: Dict[str, Any] = {
            "modelId": self.model_id,
            "messages": messages,
            "toolConfig": tool_config,
        }

        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]

        try:
            response = self._client.converse(**kwargs)
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            logger.error(f"🌐 GroundingService: converse call failed ({latency}ms): {e}")
            return GroundingResult(text="", latency_ms=latency, error=str(e))

        latency = int((time.time() - start) * 1000)

        # Parse usage
        usage = response.get("usage", {})
        input_tokens = usage.get("inputTokens", 0)
        output_tokens = usage.get("outputTokens", 0)

        # Parse content blocks — interleaved text and citationsContent
        content_blocks = (
            response.get("output", {}).get("message", {}).get("content", [])
        )

        return self._parse_content_blocks(
            content_blocks,
            latency_ms=latency,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _parse_content_blocks(
        self,
        blocks: List[Dict[str, Any]],
        latency_ms: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> GroundingResult:
        """Parse the interleaved text + citationsContent blocks from Nova."""
        text_parts: List[str] = []
        citations: List[Citation] = []

        for block in blocks:
            if "text" in block:
                text_parts.append(block["text"])

            elif "citationsContent" in block:
                cites = block["citationsContent"].get("citations", [])
                for cite in cites:
                    loc = cite.get("location", {}).get("web", {})
                    url = loc.get("url", "")
                    domain = loc.get("domain", "")
                    if url:
                        citations.append(Citation(url=url, domain=domain))

        return GroundingResult(
            text="".join(text_parts),
            citations=citations,
            model_id=self.model_id,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


# Singleton with lazy initialization
_grounding_service: Optional[GroundingService] = None


def get_grounding_service() -> GroundingService:
    """Get or create the singleton GroundingService."""
    global _grounding_service
    if _grounding_service is None:
        model_key = os.environ.get("ZIYA_GROUNDING_MODEL", DEFAULT_GROUNDING_MODEL)
        region = os.environ.get("ZIYA_GROUNDING_REGION", DEFAULT_GROUNDING_REGION)
        _grounding_service = GroundingService(model_key=model_key, region=region)
    return _grounding_service
