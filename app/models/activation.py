"""
Activation model for the capabilities hub (design/capabilities-hub.md, slice 2).

Vocabulary only.  The API speaks ``always|ondemand|off``; the lane names
(on hand / within reach / shelved) are frontend strings and never appear here.

Keys are ``kind:name`` (``skill:kuiper-conventions``, ``mcp:kuiper-jira-mcp``),
never ``Skill.id``: ``skill_discovery._stable_id`` hashes the install path, so
a reinstall to another tier or a registry update changes the id and would
orphan every placement.  Name is already the tier-shadowing key, so a placement
follows the winning copy.  The kind prefix keeps ``skill:shell`` and
``mcp:shell`` apart.
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

PlacementState = Literal["always", "ondemand", "off"]
PLACEMENT_STATES = ("always", "ondemand", "off")

# Layers a placement may be WRITTEN to, in first-set-wins precedence.  ``default``
# is a computed origin, never a writable layer.
WRITABLE_LAYERS = ("conversation", "project", "user")
Layer = Literal["conversation", "project", "user"]
Origin = Literal["conversation", "project", "user", "default", "environment"]

CapabilityKind = Literal["skill", "mcp"]
Tier = Literal["placeable", "environment"]


def make_key(kind: str, name: str) -> str:
    return f"{kind}:{name}"


def parse_key(key: str) -> Optional[tuple]:
    """``"skill:foo"`` -> ``("skill", "foo")``; None when malformed."""
    if not isinstance(key, str) or ":" not in key:
        return None
    kind, _, name = key.partition(":")
    if kind not in ("skill", "mcp") or not name:
        return None
    return kind, name


class ItemSpec(BaseModel):
    """What the resolver needs to know about one installed capability.

    Built by the caller from the skill catalog and the MCP manager; the
    resolver never touches either.
    """
    key: str
    kind: CapabilityKind
    tier: Tier = "placeable"
    # Skills only: ``model_discoverable`` skills default to ``ondemand`` (the
    # model can load them via get_skill_details); ``user_selectable`` ones
    # default to ``off``.  Ignored for MCP, which defaults to ``always``.
    discoverable: bool = False


class ResolvedItem(BaseModel):
    key: str
    kind: CapabilityKind
    tier: Tier
    # The placement stored at each writable layer (None = inherit).  Returned
    # for every layer because the lens switch is client-only: the UI renders a
    # ghost or a pin at the viewed layer without another GET.
    placements: Dict[str, Optional[PlacementState]]
    effective: PlacementState
    origin: Origin
    default: PlacementState


class DroppedEntry(BaseModel):
    """A stored layer entry the resolver refused to honour.

    Reported, not silently discarded: a made-global chat carrying a skill
    the current project does not have must show as *not installed here*.
    """
    key: str
    layer: str
    reason: str


class ResolveResult(BaseModel):
    items: List[ResolvedItem]
    dropped: List[DroppedEntry]

    def always_set(self) -> List[str]:
        """Keys whose prompt text / tools go in every turn."""
        return [i.key for i in self.items if i.effective == "always"]

    def catalog_set(self) -> List[str]:
        """Keys offered to the model for on-demand loading."""
        return [i.key for i in self.items if i.effective == "ondemand"]


# Rejection reasons.  The API layer (slice 3) maps every one of these to 409;
# they are strings rather than an enum so the shared fixture file can name
# them and the jest mirror can assert the same values.
REASON_UNKNOWN_KEY = "unknown_key"
REASON_INVALID_STATE = "invalid_state"
REASON_INVALID_LAYER = "invalid_layer"
REASON_ENVIRONMENT = "environment_not_placeable"
REASON_MCP_ONDEMAND = "mcp_ondemand_unsupported"


class PlacementRejected(ValueError):
    """A placement write the model does not accept.

    Carries the machine-readable ``reason`` so the API can return it in the
    409 body and the UI can phrase the refusal (e.g. the MCP on-demand lane's
    "coming soon" drop, which exists until slice 9 lifts the restriction).
    """

    def __init__(self, reason: str, key: str, detail: str = ""):
        self.reason = reason
        self.key = key
        super().__init__(detail or f"{reason}: {key}")
