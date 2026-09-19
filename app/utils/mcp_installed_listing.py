"""Shape helpers for GET /api/mcp/registry/services/installed.

The route merges three sources into one list the frontend's `InstalledService`
type (camelCase: `serverName`, `serviceId`, ...) consumes:

1. entries the registry manager wrote to mcp_config.json (snake_case),
2. the builtin time/shell servers,
3. hand-configured servers correlated against the live registry.

Two defects lived at that merge. The snake_case entries were passed through
unconverted, so `ServiceCard` (which returns null without `serviceId`) dropped
every registry install from the Installed tab and `isServiceInstalled` never
matched them — they reappeared only through (3), labelled "Manually
Configured". And (3) matched by substring against ~10k registry entries in
whatever order the aggregator returned them, so `slack-mcp` correlated to a
different service on consecutive calls.
"""
from typing import Any, Dict, Iterable, Optional


def _norm(name: Optional[str]) -> str:
    return (name or "").strip().lower().replace("-", "_").replace(" ", "_")


def installed_entry_to_api(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a RegistryIntegrationManager installed record to the API shape."""
    provider_id = entry.get("provider")
    return {
        "serverName": entry.get("server_name"),
        "serviceId": entry.get("service_id") or entry.get("server_name"),
        "serviceName": entry.get("service_name") or entry.get("server_name"),
        "version": entry.get("version"),
        "supportLevel": entry.get("support_level"),
        "installedAt": entry.get("installed_at"),
        "enabled": entry.get("enabled", True),
        "installationPath": entry.get("installation_path"),
        "provider": {"id": provider_id, "name": provider_id} if provider_id else None,
        "_manually_configured": False,
    }


def _service_id_leaf(service_id: str) -> str:
    """`modelcontextprotocol.servers.tree.main.src.brave-search` -> `brave_search`."""
    leaf = service_id
    for sep in (".", "/"):
        leaf = leaf.rsplit(sep, 1)[-1]
    return _norm(leaf)


def correlate_server_to_registry(
    server_name: str,
    server_config: Dict[str, Any],
    registry_services: Iterable[Any],
):
    """Pick the registry entry a hand-configured server corresponds to, or None.

    Only exact correspondences count — a normalised server name equal to the
    service id, the service id's last path segment, or the service name, or an
    identical repository URL. Substring matches are refused: `fetch` is not
    `secure-mcp-fetch`, and `slack_mcp` is not `jtalk22/slack-mcp-server`.
    Ties are broken by the tier of the match and then by service id, so the
    result does not depend on the order the aggregator returned entries in.
    """
    target = _norm(server_name)
    if not target:
        return None
    repo = (server_config or {}).get("repository_url")

    best = None  # (tier, service_id, service)
    for svc in registry_services:
        sid = getattr(svc, "service_id", "") or ""
        tier = None
        if _norm(sid) == target:
            tier = 0
        elif _service_id_leaf(sid) == target:
            tier = 1
        elif _norm(getattr(svc, "service_name", "")) == target:
            tier = 2
        elif repo and getattr(svc, "repository_url", None) == repo:
            tier = 3
        if tier is None:
            continue
        key = (tier, sid)
        if best is None or key < best[:2]:
            best = (tier, sid, svc)
    return best[2] if best else None
