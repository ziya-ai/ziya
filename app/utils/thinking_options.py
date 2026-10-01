"""Reasoning display vocabulary shared by the model-config UI and the providers.

Ziya exposes one setting for *how much* of a model's reasoning to show,
``ZIYA_THINKING_DISPLAY``, with three canonical values:

    quiet    reason, but stream none of it back
    summary  stream the provider's summarised reasoning
    full     stream the complete chain of thought

Together with ``ZIYA_THINKING_MODE`` (on/off) this is what the modal's
"Reasoning" selector edits.  Not every backend can honour every tier, and
offering a tier a backend cannot honour misrepresents what the user is
paying for, so :func:`describe_thinking_options` computes the honest subset
per model.  The per-backend translations live here too (rather than in each
provider) so the table can be read and tested in one place:

    backend                       quiet                     summary            full
    ------------------------------------------------------------------------------------
    Claude adaptive (4.6+)        display: omitted          display: summarized  (= summary)
    Claude budget (3.7 / 4.x)     -- no API knob; on/off only --
    OpenAI Responses (GPT-5.6+)   reasoning.summary omitted summary: auto      summary: detailed
    Gemini                        include_thoughts: false   include_thoughts: true (= summary)
    OpenAI-compatible raw CoT     deltas dropped in Ziya    (= full)           deltas streamed
    Converse / Nova (no channel)  -- prompt-injected step-by-step; on/off only --

"quiet" never changes what is billed: the model still reasons, and the
tokens still land in ``thinking_tokens``.  It only changes what streams.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

DISPLAY_QUIET = "quiet"
DISPLAY_SUMMARY = "summary"
DISPLAY_FULL = "full"
DISPLAY_VALUES = (DISPLAY_QUIET, DISPLAY_SUMMARY, DISPLAY_FULL)
DEFAULT_DISPLAY = DISPLAY_SUMMARY

# Selector option ids.  ``off`` and ``on`` are the thinking_mode axis; the
# three display values are the thinking_display axis with thinking_mode on.
OPTION_OFF = "off"
OPTION_ON = "on"

# Group ids reported to the UI so tests and help text can key on them.
GROUP_CLAUDE_ADAPTIVE = "claude-adaptive"
GROUP_CLAUDE_BUDGET = "claude-budget"
GROUP_OPENAI_RESPONSES = "openai-responses"
GROUP_GEMINI = "gemini"
GROUP_RAW_REASONING = "raw-reasoning"
GROUP_PROMPTED = "prompted"

# Model-config key a model entry may set to override the computed option
# list, e.g. Meta Muse Spark bills for reasoning it never streams, so
# offering "full" there would promise text that cannot arrive.
OVERRIDE_KEY = "thinking_display_options"

# Endpoints whose provider has a server-side display knob or no reasoning
# channel at all.  Anything else is an OpenAI-compatible endpoint that
# streams raw reasoning_content (openai, zai, meta, local-*, ...).
_NON_RAW_ENDPOINTS = {"bedrock", "bedrock-mantle", "anthropic", "google"}

# The instruction Converse-API models without a native reasoning channel get
# appended to their system prompt when the user turns "reasoning" on.  Kept
# here so the provider and the UI help text describe the same behaviour.
STEP_BY_STEP_INSTRUCTION = (
    "\nThink through this step-by-step. First analyze the problem, then "
    "explore possible approaches, and finally provide your solution with "
    "clear reasoning."
)


def normalize_display(value: Optional[str]) -> str:
    """Coerce an env/user value to a canonical display tier."""
    if isinstance(value, str) and value.strip().lower() in DISPLAY_VALUES:
        return value.strip().lower()
    return DEFAULT_DISPLAY


# ---------------------------------------------------------------------------
# Per-backend translations (imported by the providers)
# ---------------------------------------------------------------------------

def anthropic_display(display: Optional[str]) -> str:
    """``thinking.display`` for Anthropic adaptive thinking.

    The API accepts exactly two values; there is no full-text tier, so
    "full" collapses to "summarized" rather than being rejected.
    """
    return "omitted" if normalize_display(display) == DISPLAY_QUIET else "summarized"


def openai_reasoning_summary(display: Optional[str]) -> Optional[str]:
    """``reasoning.summary`` for the OpenAI Responses API; None = omit key."""
    d = normalize_display(display)
    if d == DISPLAY_QUIET:
        return None
    if d == DISPLAY_FULL:
        return "detailed"
    return "auto"


def gemini_include_thoughts(display: Optional[str]) -> bool:
    """``thinking_config.include_thoughts`` for Gemini thought summaries."""
    return normalize_display(display) != DISPLAY_QUIET


def suppress_thinking_deltas(display: Optional[str]) -> bool:
    """Whether Ziya itself should drop ThinkingDelta events.

    Backends with a server knob already send nothing at "quiet"; for the raw
    chain-of-thought providers this client-side drop is the only way to
    honour it.  Applying it uniformly is harmless for the former.
    """
    return normalize_display(display) == DISPLAY_QUIET


# ---------------------------------------------------------------------------
# Group classification (used by /api/model-capabilities)
# ---------------------------------------------------------------------------

def classify_thinking_group(model_config: Dict[str, Any], endpoint: str) -> Optional[str]:
    """Return the reasoning-control group for a model, or None if it has none."""
    mc = model_config or {}
    if mc.get("supports_adaptive_thinking"):
        return GROUP_CLAUDE_ADAPTIVE
    if mc.get("mantle_api") == "openai-responses":
        return GROUP_OPENAI_RESPONSES
    family = str(mc.get("family", "") or "")
    if mc.get("supports_thinking_level") or family == "gemini-3":
        return GROUP_GEMINI
    if not mc.get("supports_thinking"):
        return None
    if endpoint == "google" or family.startswith("gemini"):
        return GROUP_GEMINI
    if family == "claude" and mc.get("wrapper_class") != "OpenAIBedrock":
        return GROUP_CLAUDE_BUDGET
    if (
        mc.get("wrapper_class") == "OpenAIBedrock"
        or mc.get("reasoning_request")
        or mc.get("supports_reasoning_effort")
        or endpoint not in _NON_RAW_ENDPOINTS
    ):
        return GROUP_RAW_REASONING
    return GROUP_PROMPTED


_GROUP_OPTIONS: Dict[str, List[str]] = {
    GROUP_CLAUDE_ADAPTIVE: [OPTION_OFF, DISPLAY_QUIET, DISPLAY_SUMMARY],
    GROUP_CLAUDE_BUDGET: [OPTION_OFF, OPTION_ON],
    GROUP_OPENAI_RESPONSES: [DISPLAY_QUIET, DISPLAY_SUMMARY, DISPLAY_FULL],
    GROUP_GEMINI: [DISPLAY_QUIET, DISPLAY_SUMMARY],
    GROUP_RAW_REASONING: [OPTION_OFF, DISPLAY_QUIET, DISPLAY_FULL],
    GROUP_PROMPTED: [OPTION_OFF, OPTION_ON],
}

_BASE_LABELS: Dict[str, str] = {
    OPTION_OFF: "Off",
    OPTION_ON: "On",
    DISPLAY_QUIET: "On · quiet",
    DISPLAY_SUMMARY: "On · summary",
    DISPLAY_FULL: "On · full",
}

_GROUP_LABEL_OVERRIDES: Dict[str, Dict[str, str]] = {
    GROUP_OPENAI_RESPONSES: {DISPLAY_FULL: "On · detailed"},
    GROUP_RAW_REASONING: {DISPLAY_QUIET: "On · hidden"},
}

_GROUP_HELP: Dict[str, str] = {
    GROUP_CLAUDE_ADAPTIVE: (
        "Summary streams Anthropic's summarized reasoning into the thinking "
        "panel (display: summarized). Quiet reasons identically and bills the "
        "same tokens but streams nothing back (display: omitted), for a faster "
        "first token. This API exposes no full-text tier. Off sends no "
        "thinking block, which also disables the effort setting."
    ),
    GROUP_CLAUDE_BUDGET: (
        "Extended thinking with a fixed token budget (type: enabled). This "
        "API has no display setting: Claude 4 returns a summary, Claude 3.7 "
        "the full text; the model decides."
    ),
    GROUP_OPENAI_RESPONSES: (
        "Maps to reasoning.summary: quiet omits it, summary requests auto, "
        "detailed requests detailed. Reasoning models on this API always "
        "reason; use Effort to control how much."
    ),
    GROUP_GEMINI: (
        "Gemini cannot disable thinking; pick the Low level to minimize it. "
        "Summary streams thought summaries (include_thoughts: true); quiet "
        "suppresses them."
    ),
    GROUP_RAW_REASONING: (
        "This provider streams the model's complete chain of thought "
        "(reasoning_content); there is no server-side summary. Hidden still "
        "requests reasoning but collapses it in Ziya; the tokens are still "
        "billed and still count toward output."
    ),
    GROUP_PROMPTED: (
        "This model has no native reasoning channel. On appends a "
        "think-step-by-step instruction to the system prompt; the reasoning "
        "appears as ordinary text in the answer, not in a thinking panel."
    ),
}

_GROUP_TITLES: Dict[str, str] = {
    GROUP_PROMPTED: "Step-by-step prompting",
}


def describe_thinking_options(model_config: Dict[str, Any], endpoint: str) -> Dict[str, Any]:
    """Compute what the Reasoning selector should offer for a model.

    Returns ``{"group", "options", "labels", "help", "title"}``; ``options``
    is empty when the model has no reasoning control at all.  A model entry
    may pin its own list via ``thinking_display_options`` (validated against
    the known option ids) when the computed group over-promises.
    """
    group = classify_thinking_group(model_config, endpoint)
    if group is None:
        return {"group": None, "options": [], "labels": {}, "help": "", "title": "Reasoning"}

    options = list(_GROUP_OPTIONS[group])
    override = (model_config or {}).get(OVERRIDE_KEY)
    if isinstance(override, (list, tuple)):
        valid = [o for o in override if o in _BASE_LABELS]
        if valid:
            options = valid

    labels = {o: _BASE_LABELS[o] for o in options}
    labels.update({k: v for k, v in _GROUP_LABEL_OVERRIDES.get(group, {}).items() if k in labels})
    return {
        "group": group,
        "options": options,
        "labels": labels,
        "help": _GROUP_HELP[group],
        "title": _GROUP_TITLES.get(group, "Reasoning"),
    }


def selector_value(thinking_mode: bool, display: Optional[str], options: List[str]) -> Optional[str]:
    """Which option id the selector should show for a (mode, display) pair.

    Mirrors the frontend derivation so the two cannot drift: off when mode is
    off and the group offers it; otherwise the display tier if offered, else
    the group's generic "on", else the first non-off option.
    """
    if not options:
        return None
    if not thinking_mode and OPTION_OFF in options:
        return OPTION_OFF
    d = normalize_display(display)
    if d in options:
        return d
    # An unoffered tier falls back to the default tier when the group has it
    # (e.g. "full" on adaptive Claude -> "summary"), never to the quietest
    # option, which would silently hide reasoning the user asked to see.
    if DEFAULT_DISPLAY in options:
        return DEFAULT_DISPLAY
    if OPTION_ON in options:
        return OPTION_ON
    non_off = [o for o in options if o != OPTION_OFF]
    return non_off[0] if non_off else options[0]
