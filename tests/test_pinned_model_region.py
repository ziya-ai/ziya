"""A per-conversation pinned model must not inherit an unsupported session region.

2026-10-07: session model opus5.5 ran in eu-west-1; a conversation pinned to
fable5.1 (no ``eu`` profile, available only in us-east-1/us-west-2) was sent
to eu-west-1 too and rejected by that region's never-set retention switch.
"""
import inspect
import re

from app.config.models_config import MODEL_CONFIGS
from app.utils.model_region import region_for_model

BR = MODEL_CONFIGS["bedrock"]


def test_pinned_fable51_leaves_eu_session_region():
    assert region_for_model("eu-west-1", BR["fable5.1"]) == "us-east-1"


def test_supported_region_is_kept():
    assert region_for_model("us-west-2", BR["fable5.1"]) == "us-west-2"


def test_model_with_eu_profile_keeps_eu_region():
    # opus5.5 has an "eu" profile; EU users must not be moved to the US.
    assert region_for_model("eu-west-1", BR["opus5.5"]) == "eu-west-1"


def test_no_config_or_region_is_passthrough():
    assert region_for_model(None, BR["fable5.1"]) is None
    assert region_for_model("eu-west-1", None) == "eu-west-1"


def test_executor_clamps_before_resolving_and_building_clients():
    """Seam: StreamingToolExecutor must apply the clamp, and do so before the
    model id is resolved and the client/provider are created from `region`."""
    import app.streaming_tool_executor as ste
    src = inspect.getsource(ste.StreamingToolExecutor.__init__)
    clamp = re.search(r"region\s*=\s*region_for_model\(region,\s*self\.model_config\)", src)
    assert clamp, "StreamingToolExecutor.__init__ does not clamp the region"
    resolve = src.index("_get_region_specific_model_id_with_region_update")
    provider = src.index("create_provider(")
    assert clamp.start() < resolve < provider
