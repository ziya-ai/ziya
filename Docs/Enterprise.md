# Ziya Enterprise & Internal Deployment Guide

This document describes the plugin system that allows enterprise and internal deployments to extend Ziya without modifying the core codebase, and the user-level customization options available to individuals on personal AWS accounts.

---

## Plugin System Overview

Ziya's plugin system loads environment-specific extensions at startup. Enterprise deployments ship a plugin package that registers providers into the core plugin registries. Community users see none of this — the defaults work out of the box.

### Activation

Enterprise plugins are loaded when `ZIYA_LOAD_INTERNAL_PLUGINS=1` is set. Ziya looks for a Python module named `plugins` or `internal.plugins` on the path and calls its `register()` function.

```bash
export ZIYA_LOAD_INTERNAL_PLUGINS=1
ziya
```

The `register()` function calls the core registration helpers:

```python
from app.plugins import (
    register_auth_provider,
    register_config_provider,
    register_registry_provider,
    register_data_retention_provider,
    register_shell_config_provider,
    register_service_model_provider,
)
```

---

## Provider Types

### AuthProvider

Controls how credentials are validated and how users are told to refresh them.

```python
from app.plugins.interfaces import AuthProvider

class MyAuthProvider(AuthProvider):
    provider_id = "my-org"
    priority = 10  # Higher = checked first

    def detect_environment(self) -> bool:
        """Return True if this provider should handle auth in the current environment."""
        return os.path.exists("/etc/my-org-marker")

    def check_credentials(self, profile_name=None, region=None) -> tuple[bool, str]:
        """Return (is_valid, message)."""
        ...

    def get_credential_help_message(self, error_context=None) -> str:
        """Human-readable instructions for refreshing credentials."""
        return "Run: my-org-login --profile default"
```

Multiple auth providers can be registered. They are checked in priority order; the first whose `detect_environment()` returns `True` becomes active.

---

### ConfigProvider

Provides environment-specific defaults and policy constraints.

```python
from app.plugins.interfaces import ConfigProvider

class MyConfigProvider(ConfigProvider):
    provider_id = "my-org"
    priority = 50

    def get_defaults(self) -> dict:
        return {
            "branding": {"edition": "MyOrg Internal"},
            "aws": {"region": "us-east-1"},
            "models": {"endpoint": "bedrock", "default_model": "sonnet4.0"},
        }

    def should_apply(self) -> bool:
        """Only apply when running under MyOrg auth."""
        from app.plugins import get_active_auth_provider
        return get_active_auth_provider().provider_id == "my-org"

    def get_allowed_endpoints(self) -> list[str] | None:
        """
        Restrict the model picker to specific endpoints.
        Return None to allow all endpoints (community default).
        Return ['bedrock'] to hide Google/Gemini from the UI.
        """
        return ["bedrock"]
```

#### `get_allowed_endpoints()`

When one or more active `ConfigProvider` implementations return a non-None list from `get_allowed_endpoints()`, the intersection of all such lists becomes the effective allowed set. Models from disallowed endpoints disappear from the model picker and cannot be selected via the API.

This is how enterprise deployments that want to restrict users to Bedrock hide the Google/Gemini and other endpoints.

#### Startup Enforcement

The endpoint policy is enforced at three levels:

1. **`--help`** — only allowed endpoints appear in the help text.
2. **Startup** — if `--endpoint` or `ZIYA_ENDPOINT` specifies a restricted endpoint, Ziya exits with a clear error before any model initialization.
3. **Runtime** — the model picker API and set-model API only return/accept models from allowed endpoints.

#### Developer Override

Set `ZIYA_ALLOW_ALL_ENDPOINTS=1` to bypass all endpoint restrictions. This is intended for plugin developers and internal testing only — it is not advertised to end users.

```bash
export ZIYA_ALLOW_ALL_ENDPOINTS=1
ziya --endpoint openai
```

---

### ServiceModelProvider

Enables built-in tool categories and configures the small specialized models that back them (e.g. Nova Web Grounding for web search).

```python
from app.plugins.interfaces import ServiceModelProvider

class MyServiceModelProvider(ServiceModelProvider):
    provider_id = "my-org-services"
    priority = 10

    def get_enabled_service_tools(self) -> set[str]:
        """
        Return builtin tool category names to force-enable.
        These are enabled even if the user has set ZIYA_ENABLE_<CATEGORY>=false.
        """
        return {"nova_grounding"}

    def get_service_model_config(self) -> dict:
        """
        Override service model configuration per category.
        Keys are category names, values are config dicts.
        """
        return {
            "nova_grounding": {
                "model": "nova-premier",   # Use the larger model
                "region": "us-east-1",
            }
        }
```

See the **Nova Web Grounding** section below for the concrete built-in implementation.

---

### DataRetentionProvider

Enforces organisation-specific data retention policies. When multiple providers register, the most restrictive (shortest) TTL per category wins.

```python
from app.plugins.interfaces import DataRetentionProvider, DataRetentionPolicy
from datetime import timedelta

class MyRetentionProvider(DataRetentionProvider):
    provider_id = "my-org"

    def get_retention_policy(self) -> DataRetentionPolicy:
        return DataRetentionPolicy(
            conversation_data_ttl=timedelta(hours=8),
            default_ttl=timedelta(days=30),
            policy_reason="MyOrg security policy v2.1",
        )
```

Available TTL categories: `conversation_data`, `context_cache`, `prompt_cache`, `tool_result`, `file_state`, `session_max`. A `default_ttl` applies to all categories that don't have an explicit override.

---

### FormatterProvider

Injects JavaScript into the frontend to provide custom rendering of tool results.

```python
from app.plugins.interfaces import FormatterProvider

class MyFormatterProvider(FormatterProvider):
    formatter_id = "my-org-formatter"
    priority = 10

    def get_formatter_code(self) -> str:
        """Return an ES module exporting a ToolFormatter object."""
        return """
        export default {
            formatterId: 'my-org-formatter',
            canFormat: (toolName, result) => toolName.startsWith('my_tool'),
            format: (toolName, result, args) => ({
                type: 'structured',
                content: result,
            }),
        };
        """
```

---

### ToolEnhancementProvider

Supplies supplemental description text for MCP tools to reduce parameter errors from models. Enhancements are appended to tool descriptions before they reach the LLM.

```python
class MyToolEnhancementProvider:
    provider_id = "my-org"

    def get_tool_enhancements(self) -> dict:
        """Return a dict mapping tool names to enhancement config."""
        return {
            "WorkspaceSearch": {
                "description_suffix": "\n<Rule>Use 'searchQuery' not 'query'</Rule>"
            },
            "RunShellCommand": {
                "description_suffix": "\nPrefer read-only commands."
            }
        }
```

Registration in `register()`:

```python
from app.plugins import register_tool_enhancement_provider
from .my_tool_enhancements import MyToolEnhancementProvider
register_tool_enhancement_provider(MyToolEnhancementProvider())
```

Enhancement priority (later overrides earlier for the same tool):

1. **Plugin providers** — enterprise-wide defaults via `register_tool_enhancement_provider()`
2. **MCP server config** — per-server `"tool_enhancements"` key in `mcp_config.json`
3. **User overrides** — `~/.ziya/tool_enhancements.json`

---

## Built-in Service Models

### Nova Web Grounding (`nova_grounding`)

Exposes Amazon Nova Web Grounding as a tool that the primary model (Claude, in most deployments) can call when it needs current web information. No external MCP server or API key required — uses the AWS Bedrock Converse API with the `nova_grounding` system tool.

**Default state**: enabled for all users (`enabled_by_default: True`).

**IAM requirement**: the AWS role must have `bedrock:InvokeTool` on  
`arn:aws:bedrock::*:system-tool/amazon.nova_grounding`.

**Environment variables**:

| Variable | Default | Description |
|---|---|---|
| `ZIYA_ENABLE_NOVA_GROUNDING` | `true` | Set to `false` to disable |
| `ZIYA_GROUNDING_MODEL` | `nova-2-lite` | `nova-2-lite` or `nova-premier` |
| `ZIYA_GROUNDING_REGION` | `us-east-1` | US regions only |

**Enterprise plugin registration** (one line in `register()`):

```python
from app.plugins.service_models import NovaGroundingProvider
register_service_model_provider(NovaGroundingProvider(region=region))
```

`NovaGroundingProvider` is a ready-to-use implementation of `ServiceModelProvider` shipped with the core package in `app/plugins/service_models.py`. Enterprise plugins instantiate and register it; they don't need to re-implement it.

**What the model sees**: When enabled, Claude sees a `nova_web_search` tool alongside its other tools and calls it autonomously when it determines current web information would improve its response.

---

## Registering the Complete Internal Plugin Set

Here is the canonical `register()` function pattern for an internal enterprise plugin:

```python
def register():
    from app.plugins import (
        register_auth_provider,
        register_config_provider,
        register_registry_provider,
        register_data_retention_provider,
        register_service_model_provider,
    )
    import os

    region = os.environ.get("AWS_REGION", "us-west-2")

    from .amazon_auth import EnterpriseAuthProvider
    register_auth_provider(EnterpriseAuthProvider())

    from .amazon_config import EnterpriseConfigProvider
    register_config_provider(EnterpriseConfigProvider())

    from .amazon_registry import EnterpriseRegistryProvider
    register_registry_provider(EnterpriseRegistryProvider(region=region))

    from .amazon_data_retention import EnterpriseDataRetentionProvider
    register_data_retention_provider(EnterpriseDataRetentionProvider())

    from app.plugins.service_models import NovaGroundingProvider
    register_service_model_provider(NovaGroundingProvider(region=region))
```

---

## User-Level Model Configuration

Individual users on personal AWS accounts can customize the model list without modifying any package files.

### `~/.ziya/models.json`

Create this file to restrict or extend the available models:

**Allowlist** (most common): show only the models you have enabled or budgeted for.

```json
{
  "allowed_models": ["sonnet4.0", "haiku-4.5", "nova-lite"]
}
```

The global model definitions (IDs, families, capabilities, token limits) are unchanged — you're only filtering which models appear in the UI and can be selected. Models not in the list simply don't appear.

**Custom inference profiles** (advanced): add models not in the global config, e.g. a provisioned throughput endpoint or a custom inference profile ARN.

```json
{
  "allowed_models": ["sonnet4.0", "my-throughput"],
  "bedrock": {
    "my-throughput": {
      "model_id": "arn:aws:bedrock:us-east-1:123456789012:inference-profile/my-profile",
      "family": "claude",
      "max_output_tokens": 64000,
      "supports_vision": true,
      "supports_context_caching": true
    }
  }
}
```

Both sections are optional. The file is loaded at server startup; restart Ziya after changes.

---

## Environment Variable Reference

| Variable | Default | Description |
|---|---|---|
| `ZIYA_LOAD_INTERNAL_PLUGINS` | unset | Set to `1` to load enterprise plugins |
| `ZIYA_ENABLE_NOVA_GROUNDING` | `true` | Enable/disable Nova web search tool |
| `ZIYA_GROUNDING_MODEL` | `nova-2-lite` | Nova model for web grounding |
| `ZIYA_GROUNDING_REGION` | `us-east-1` | AWS region for grounding calls |
| `ZIYA_ENABLE_<CATEGORY>` | varies | Override enabled state for any builtin tool category |
