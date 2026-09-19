"""GET /api/mcp/registry/services/installed: registry installs are listed in the
frontend's camelCase shape, and hand-configured servers correlate to a registry
entry deterministically (exact matches only)."""
from types import SimpleNamespace

import pytest

from app.utils.mcp_installed_listing import (
    installed_entry_to_api,
    correlate_server_to_registry,
)


def _svc(service_id, name, repo=None):
    return SimpleNamespace(
        service_id=service_id, service_name=name, repository_url=repo,
        version=1, support_level="Community", installation_type="npm",
        service_description=f"{name} desc", security_review_url=None,
        provider_metadata={"provider_id": "test"},
    )


class TestShape:
    def test_snake_case_record_becomes_frontend_shape(self):
        out = installed_entry_to_api({
            "server_name": "quip_mcp", "service_id": "quip-mcp",
            "service_name": "Comprehensive MCP server for Amazon Quip.",
            "version": 10, "support_level": "In development",
            "installed_at": "2026-09-05T01:11:04", "enabled": True,
            "provider": "amazon-internal", "installation_path": "/x",
        })
        assert out["serverName"] == "quip_mcp"
        assert out["serviceId"] == "quip-mcp"
        assert out["serviceName"].startswith("Comprehensive")
        assert out["supportLevel"] == "In development"
        assert out["installedAt"] == "2026-09-05T01:11:04"
        assert out["provider"] == {"id": "amazon-internal", "name": "amazon-internal"}
        assert out["_manually_configured"] is False
        assert "server_name" not in out

    def test_missing_ids_fall_back_to_server_name(self):
        out = installed_entry_to_api({"server_name": "x", "enabled": False})
        assert out["serviceId"] == "x" and out["serviceName"] == "x"
        assert out["enabled"] is False and out["provider"] is None


class TestCorrelation:
    REGISTRY = [
        _svc("jtalk22.slack-mcp-server", "jtalk22/slack-mcp-server"),
        _svc("slack-mcp", "Slack MCP Server"),
        _svc("korotovsky.slack-mcp-server", "korotovsky/slack-mcp-server"),
        _svc("appsec-innovation-labs.secure-mcp-fetch", "Secure Fetch"),
        _svc("modelcontextprotocol.servers.tree.main.src.brave-search", "Brave Search"),
        _svc("modelcontextprotocol.servers.tree.main.src.sequentialthinking", "Sequential Thinking"),
        _svc("bug-breeder.quip-mcp", "bug-breeder/quip-mcp"),
    ]

    def test_exact_service_id_beats_substring_neighbours_regardless_of_order(self):
        for order in (self.REGISTRY, list(reversed(self.REGISTRY))):
            m = correlate_server_to_registry("slack-mcp", {}, order)
            assert m is not None and m.service_id == "slack-mcp"

    def test_service_id_leaf_matches_official_registry_ids(self):
        m = correlate_server_to_registry("brave-search", {}, self.REGISTRY)
        assert m.service_id.endswith("brave-search")

    def test_service_name_match(self):
        m = correlate_server_to_registry("sequential-thinking", {}, self.REGISTRY)
        assert m.service_name == "Sequential Thinking"

    def test_substring_is_not_a_match(self):
        # `fetch` is not Secure Fetch; `slack` is not any of the three slack servers.
        assert correlate_server_to_registry("fetch", {}, self.REGISTRY) is None
        assert correlate_server_to_registry("slack", {}, self.REGISTRY) is None

    def test_id_leaf_is_a_match_by_design(self):
        # The same rule that resolves official-registry path ids also resolves
        # owner.repo ids; an exact id (tier 0) still wins over a leaf (tier 1).
        m = correlate_server_to_registry("quip_mcp", {}, self.REGISTRY)
        assert m.service_id == "bug-breeder.quip-mcp"
        reg = self.REGISTRY + [_svc("quip-mcp", "Quip MCP")]
        assert correlate_server_to_registry("quip_mcp", {}, reg).service_id == "quip-mcp"

    def test_repository_url_match(self):
        reg = [_svc("some.id", "Other Name", repo="https://g/x")]
        m = correlate_server_to_registry("mine", {"repository_url": "https://g/x"}, reg)
        assert m is reg[0]

    def test_empty_name_matches_nothing(self):
        assert correlate_server_to_registry("", {}, self.REGISTRY) is None


class TestRouteSeam:
    """Drive the real route with a fake registry manager and MCP manager."""

    @pytest.fixture
    def route(self, monkeypatch):
        import app.routes.mcp_routes as r

        installed = [{
            "server_name": "quip_mcp", "service_id": "quip-mcp",
            "service_name": "Comprehensive MCP server for Amazon Quip.",
            "version": 10, "support_level": "In development",
            "installed_at": "t", "enabled": True, "provider": "amazon-internal",
            "installation_path": "/x",
        }]
        registry = TestCorrelation.REGISTRY

        class FakeRegistryManager:
            def get_installed_services(self):
                return installed

            async def get_available_services(self, max_results=100):
                return registry

        client = SimpleNamespace(is_connected=True)
        fake_mcp = SimpleNamespace(
            is_initialized=True,
            clients={"quip_mcp": client, "slack-mcp": client, "fetch": client},
            server_configs={
                "quip_mcp": {"registry_provider": "amazon-internal", "enabled": True},
                "slack-mcp": {"command": "slack-mcp"},
                "fetch": {"command": "uvx"},
            },
        )
        monkeypatch.setattr(r, "get_registry_manager", lambda: FakeRegistryManager())
        monkeypatch.setattr(r, "get_mcp_manager", lambda: fake_mcp)
        return r

    @pytest.mark.asyncio
    async def test_registry_install_listed_once_in_camel_case_and_not_manual(self, route):
        out = await route.get_installed_registry_services()
        by_server = {}
        for s in out["services"]:
            by_server.setdefault(s["serverName"], []).append(s)
        assert len(by_server["quip_mcp"]) == 1, by_server["quip_mcp"]
        q = by_server["quip_mcp"][0]
        assert q["serviceId"] == "quip-mcp"
        assert q["_manually_configured"] is False
        # It must not have been re-derived as bug-breeder/quip-mcp.
        assert "bug-breeder" not in q["serviceId"]

    @pytest.mark.asyncio
    async def test_manual_servers_correlate_exactly_or_not_at_all(self, route):
        out = await route.get_installed_registry_services()
        by_server = {s["serverName"]: s for s in out["services"]}
        assert by_server["slack-mcp"]["serviceId"] == "slack-mcp"
        assert by_server["slack-mcp"]["_manually_configured"] is True
        assert "fetch" not in by_server
