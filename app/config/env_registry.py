"""
Centralized registry of all ZIYA_* environment variables.

Every ZIYA_* env var used anywhere in the codebase must be declared here.
This is the single source of truth for names, types, defaults, and
documentation.  New vars added without a registry entry will be caught
by the lint script (scripts/lint_env_vars.py).

Usage from application code::

    from app.config.env_registry import ziya_env

    root = ziya_env("ZIYA_USER_CODEBASE_DIR")       # str, with default
    mcp  = ziya_env("ZIYA_ENABLE_MCP")               # auto-parsed bool
    temp = ziya_env("ZIYA_TEMPERATURE")               # auto-parsed float
    depth = ziya_env("ZIYA_MAX_DEPTH")                # auto-parsed int

The ``ziya_env`` helper validates the key exists in the registry and
coerces the raw string to the declared type.  Direct ``os.environ.get``
calls for ZIYA_* keys should be migrated over time — but this is NOT
enforced at runtime to keep the migration non-breaking.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional


class EnvCategory(str, Enum):
    """Grouping for env vars — used in docs and --info output."""
    CORE = "core"
    PATHS = "paths"
    MODEL = "model"
    MODEL_PARAMS = "model_params"
    AWS = "aws"
    MCP = "mcp"
    FEATURES = "features"
    DIFF = "diff"
    GROUNDING = "grounding"
    SECURITY = "security"
    LOGGING = "logging"
    INTERNAL = "internal"


@dataclass(frozen=True)
class EnvVar:
    """Declaration of a single ZIYA_* environment variable."""
    name: str
    type: type                        # str, int, float, bool
    default: Any                      # None means "required at point of use"
    category: EnvCategory
    description: str
    cli_flag: Optional[str] = None    # corresponding --flag if any
    user_facing: bool = True          # False for internal startup flags
    deprecated_by: Optional[str] = None  # set if this var is superseded


# ---------------------------------------------------------------------------
# THE REGISTRY — alphabetical within each category.
# ---------------------------------------------------------------------------

_VARS: List[EnvVar] = [
    # ── Core ──────────────────────────────────────────────────────────────
    EnvVar("ZIYA_MODE", str, "server", EnvCategory.CORE,
           "Execution mode: 'server', 'chat', or 'debug'."),
    EnvVar("ZIYA_EDITION", str, "Community Edition", EnvCategory.CORE,
           "Edition label shown in UI and --version output."),
    EnvVar("ZIYA_HOME", str, "~/.ziya", EnvCategory.CORE,
           "Root directory for Ziya user data (sessions, caches, projects)."),
    EnvVar("ZIYA_PORT", int, 6969, EnvCategory.CORE,
           "Port for the Ziya web server.", cli_flag="--port"),
    EnvVar("ZIYA_THEME", str, "light", EnvCategory.CORE,
           "UI theme: 'light' or 'dark'."),

    # ── Paths ─────────────────────────────────────────────────────────────
    EnvVar("ZIYA_USER_CODEBASE_DIR", str, None, EnvCategory.PATHS,
           "Absolute path to the project root directory.", cli_flag="--root"),
    EnvVar("ZIYA_TEMPLATES_DIR", str, None, EnvCategory.PATHS,
           "Path to the HTML templates directory (set automatically)."),
    EnvVar("ZIYA_INCLUDE_DIRS", str, "", EnvCategory.PATHS,
           "Comma-separated external paths to include in the file tree.",
           cli_flag="--include"),
    EnvVar("ZIYA_INCLUDE_ONLY_DIRS", str, "", EnvCategory.PATHS,
           "Comma-separated paths; only these directories are shown.",
           cli_flag="--include-only"),
    EnvVar("ZIYA_ADDITIONAL_EXCLUDE_DIRS", str, "", EnvCategory.PATHS,
           "Comma-separated directories to exclude from scanning.",
           cli_flag="--exclude"),

    # ── Model / Endpoint ──────────────────────────────────────────────────
    EnvVar("ZIYA_ENDPOINT", str, "bedrock", EnvCategory.MODEL,
           "Model provider: 'bedrock', 'google', 'openai', or 'anthropic'.",
           cli_flag="--endpoint"),
    EnvVar("ZIYA_MODEL", str, None, EnvCategory.MODEL,
           "Model alias (e.g. 'sonnet4.0', 'gemini-3.1-pro').",
           cli_flag="--model"),
    EnvVar("ZIYA_MODEL_ID_OVERRIDE", str, None, EnvCategory.MODEL,
           "Override the resolved model ID directly (advanced).",
           cli_flag="--model-id"),

    # ── Model Parameters ──────────────────────────────────────────────────
    EnvVar("ZIYA_TEMPERATURE", float, None, EnvCategory.MODEL_PARAMS,
           "Sampling temperature (0.0-1.0 for most models).",
           cli_flag="--temperature"),
    EnvVar("ZIYA_TOP_K", int, None, EnvCategory.MODEL_PARAMS,
           "Top-k sampling (0-500, Claude only).", cli_flag="--top-k"),
    EnvVar("ZIYA_TOP_P", float, None, EnvCategory.MODEL_PARAMS,
           "Top-p / nucleus sampling.", cli_flag="--top-p"),
    EnvVar("ZIYA_MAX_OUTPUT_TOKENS", int, None, EnvCategory.MODEL_PARAMS,
           "Maximum tokens the model may generate per response.",
           cli_flag="--max-output-tokens"),
    EnvVar("ZIYA_MAX_TOKENS", int, None, EnvCategory.MODEL_PARAMS,
           "Legacy alias for ZIYA_MAX_OUTPUT_TOKENS.",
           deprecated_by="ZIYA_MAX_OUTPUT_TOKENS"),
    EnvVar("ZIYA_MAX_INPUT_TOKENS", int, None, EnvCategory.MODEL_PARAMS,
           "Maximum input tokens (set via frontend settings panel)."),
    EnvVar("ZIYA_THINKING_MODE", bool, False, EnvCategory.MODEL_PARAMS,
           "Enable thinking/chain-of-thought mode when the model supports it."),
    EnvVar("ZIYA_THINKING_LEVEL", str, None, EnvCategory.MODEL_PARAMS,
           "Thinking level for Gemini 3 models: 'low', 'medium', 'high'.",
           cli_flag="--thinking-level"),
    EnvVar("ZIYA_THINKING_EFFORT", str, None, EnvCategory.MODEL_PARAMS,
           "Thinking effort for Claude 4.6+ adaptive thinking: "
           "'low', 'medium', 'high', 'max'."),
    EnvVar("ZIYA_THINKING_BUDGET", int, 16000, EnvCategory.MODEL_PARAMS,
           "Token budget for extended thinking (Bedrock streaming)."),

    # ── AWS ───────────────────────────────────────────────────────────────
    EnvVar("ZIYA_AWS_PROFILE", str, None, EnvCategory.AWS,
           "AWS credential profile name for Bedrock.", cli_flag="--profile"),

    # ── MCP ───────────────────────────────────────────────────────────────
    EnvVar("ZIYA_ENABLE_MCP", bool, True, EnvCategory.MCP,
           "Enable MCP (Model Context Protocol) server integration.",
           cli_flag="--mcp / --no-mcp"),
    EnvVar("ZIYA_TOOL_TIMEOUT", int, 120, EnvCategory.MCP,
           "Default timeout (seconds) for individual MCP tool executions."),
    EnvVar("ZIYA_TOOL_SENTINEL", str, "TOOL_SENTINEL", EnvCategory.MCP,
           "XML tag name used for tool call boundaries in streaming."),
    EnvVar("ZIYA_MAX_TOOLS_PER_ROUND", int, 5, EnvCategory.MCP,
           "Maximum tool calls the model may make in a single round."),
    EnvVar("ZIYA_SECURE_MCP", bool, False, EnvCategory.MCP,
           "Enforce strict MCP result signing and verification."),

    # ── Features ──────────────────────────────────────────────────────────
    EnvVar("ZIYA_ENABLE_AST", bool, False, EnvCategory.FEATURES,
           "Enable AST-based code understanding.", cli_flag="--ast"),
    EnvVar("ZIYA_AST_RESOLUTION", str, "medium", EnvCategory.FEATURES,
           "AST context resolution level: 'disabled', 'minimal', 'medium', "
           "'detailed', 'comprehensive'.", cli_flag="--ast-resolution"),
    EnvVar("ZIYA_EPHEMERAL_MODE", bool, False, EnvCategory.FEATURES,
           "Don't persist conversations or data beyond the current session.",
           cli_flag="--ephemeral"),
    EnvVar("ZIYA_USE_DIRECT_STREAMING", bool, False, EnvCategory.FEATURES,
           "Use direct Bedrock streaming (legacy toggle, largely superseded)."),
    EnvVar("ZIYA_ENABLE_NOVA_GROUNDING", bool, False, EnvCategory.FEATURES,
           "Enable the Nova Web Grounding tool for web search."),

    # ── Diff Application ──────────────────────────────────────────────────
    EnvVar("ZIYA_ENABLE_DIFF_VALIDATION", bool, True, EnvCategory.DIFF,
           "Validate diffs before streaming completes."),
    EnvVar("ZIYA_AUTO_REGENERATE_INVALID_DIFFS", bool, True, EnvCategory.DIFF,
           "Automatically ask the model to regenerate failed diffs."),
    EnvVar("ZIYA_AUTO_ENHANCE_CONTEXT_ON_VALIDATION_FAILURE", bool, True,
           EnvCategory.DIFF,
           "Automatically add missing files to context when a diff fails."),
    EnvVar("ZIYA_DIFF_VIEW_TYPE", str, "unified", EnvCategory.DIFF,
           "Default diff view in the UI: 'unified' or 'split'."),
    EnvVar("ZIYA_DIFF_CONTEXT_SIZE", int, None, EnvCategory.DIFF,
           "Override the number of context lines used in diff matching."),
    EnvVar("ZIYA_DIFF_SEARCH_RADIUS", int, None, EnvCategory.DIFF,
           "Override the hunk search radius for fuzzy matching."),
    EnvVar("ZIYA_FORCE_DIFFLIB", bool, False, EnvCategory.DIFF,
           "Bypass system patch and always use Python difflib."),
    EnvVar("ZIYA_FORCE_DRY_RUN", bool, False, EnvCategory.DIFF,
           "Never write changes — dry-run all diff applications."),

    # ── Grounding ─────────────────────────────────────────────────────────
    EnvVar("ZIYA_GROUNDING_MODEL", str, "nova-2-lite", EnvCategory.GROUNDING,
           "Model key for Nova web grounding calls."),
    EnvVar("ZIYA_GROUNDING_REGION", str, "us-east-1", EnvCategory.GROUNDING,
           "AWS region for the grounding service."),

    # ── Security ──────────────────────────────────────────────────────────
    EnvVar("ZIYA_ENCRYPTION_KEY", str, None, EnvCategory.SECURITY,
           "Passphrase for at-rest encryption of stored conversations."),
    EnvVar("ZIYA_DISABLE_AUDIT_LOG", bool, False, EnvCategory.SECURITY,
           "Disable the MCP tool audit log."),
    EnvVar("ZIYA_ALLOW_ALL_ENDPOINTS", bool, False, EnvCategory.SECURITY,
           "Bypass enterprise endpoint restrictions (dev/testing only)."),

    # ── Logging / Debug ───────────────────────────────────────────────────
    EnvVar("ZIYA_LOG_LEVEL", str, "INFO", EnvCategory.LOGGING,
           "Python log level: DEBUG, INFO, WARNING, ERROR."),
    EnvVar("ZIYA_DEBUG_PROMPTS", bool, False, EnvCategory.LOGGING,
           "Log full prompt assembly details to the console."),

    # ── Internal (not user-facing) ────────────────────────────────────────
    EnvVar("ZIYA_AUTH_CHECKED", str, None, EnvCategory.INTERNAL,
           "Flag: auth was attempted during startup.", user_facing=False),
    EnvVar("ZIYA_PARENT_AUTH_COMPLETE", str, None, EnvCategory.INTERNAL,
           "Flag: parent process completed auth successfully.", user_facing=False),
    EnvVar("ZIYA_SKIP_INIT", str, None, EnvCategory.INTERNAL,
           "Flag: skip model init (set after startup auth).", user_facing=False),
    EnvVar("ZIYA_LOAD_INTERNAL_PLUGINS", str, None, EnvCategory.INTERNAL,
           "Load internal/enterprise plugins.", user_facing=False),
    EnvVar("ZIYA_DISABLE_AUTO_UPDATE", bool, False, EnvCategory.INTERNAL,
           "Disable automatic pip/pipx upgrade check.", user_facing=False),
    EnvVar("ZIYA_PROJECT_ID", str, None, EnvCategory.INTERNAL,
           "Current project ID (set by middleware/context).", user_facing=False),
    EnvVar("ZIYA_SCAN_TIMEOUT", int, 45, EnvCategory.INTERNAL,
           "Maximum seconds for folder scanning.", user_facing=True),
    EnvVar("ZIYA_MAX_DEPTH", int, 15, EnvCategory.INTERNAL,
           "Maximum depth for folder tree traversal.",
           cli_flag="--max-depth", user_facing=True),
    EnvVar("ZIYA_INCLUDE_INTERNAL_REGISTRIES", bool, False, EnvCategory.INTERNAL,
           "Include internal MCP registry sources.", user_facing=False),
    EnvVar("ZIYA_CANARY", str, None, EnvCategory.INTERNAL,
           "Canary token for deployment verification.", user_facing=False),
    EnvVar("ZIYA_ENCRYPTION_KEY_RAW", str, None, EnvCategory.INTERNAL,
           "Raw encryption key before derivation (internal).", user_facing=False),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

REGISTRY: Dict[str, EnvVar] = {v.name: v for v in _VARS}


def ziya_env(name: str, default: Any = ...) -> Any:
    """Read a ZIYA_* env var with type coercion from the registry.

    Args:
        name: The environment variable name (must start with ``ZIYA_``).
        default: Override the registry default.  Pass ``...`` (the sentinel)
                 to use the registry's declared default.

    Returns:
        The coerced value, or *default* when unset.

    Raises:
        KeyError: If *name* is not declared in the registry.
    """
    spec = REGISTRY.get(name)
    if spec is None:
        raise KeyError(
            f"Environment variable '{name}' is not declared in "
            f"app.config.env_registry.  Add it to _VARS before use."
        )

    raw = os.environ.get(name)
    fallback = spec.default if default is ... else default

    if raw is None:
        return fallback

    if spec.type is bool:
        return raw.strip().lower() in ("true", "1", "yes")
    if spec.type is int:
        try:
            return int(raw)
        except (ValueError, TypeError):
            return fallback
    if spec.type is float:
        try:
            return float(raw)
        except (ValueError, TypeError):
            return fallback
    return raw


def get_user_facing_vars() -> List[EnvVar]:
    """Return only user-facing env vars (for docs and --info)."""
    return [v for v in _VARS if v.user_facing]


def get_vars_by_category(category: EnvCategory) -> List[EnvVar]:
    """Return all vars in a given category."""
    return [v for v in _VARS if v.category == category]
