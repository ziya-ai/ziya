"""summarize_description — one-line headline from a registry service description.

Fixtures under tests/fixtures/mcp_descriptions/ are real payloads. The Slack
MCP registry entry is a 20 KB README whose first line is a markdown heading
and whose second is a byline of links; when such a service is installed the
registry provider copies that whole README into mcp_config.json as the
server's ``description``, and the MCP status panel then used it as the
server's *name*. These tests pin the summariser and the two seams where the
blob used to leak: the config write and the installed-services listing.

The TypeScript mirror (frontend/src/utils/mcpDescriptionSummary.ts) is tested
against the same fixtures and expected strings in
frontend/src/utils/__tests__/mcpDescriptionSummary.test.ts.
"""
import json
from pathlib import Path

import pytest

from app.mcp.description_summary import summarize_description

FIXTURES = Path(__file__).parent / "fixtures" / "mcp_descriptions"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestSummarizeDescription:
    def test_slack_readme_reduces_to_first_prose_sentence_capped(self):
        raw = fixture("slack_mcp_readme.md")
        assert len(raw) > 15000  # the fixture is the real blob
        s = summarize_description(raw)
        assert s.startswith("A Slack MCP server for Amazon Enterprise Slack")
        assert not s.startswith("#")          # heading skipped
        assert "Source Code" not in s         # byline skipped
        assert "](" not in s                  # no link syntax
        assert "\n" not in s
        assert len(s) <= 160
        assert s.endswith("…")

    def test_quip_keeps_only_lead_sentence_before_tool_list(self):
        assert summarize_description(fixture("quip_mcp_toollist.md")) == (
            "Comprehensive MCP server for Amazon Quip providing 37 tools "
            "covering the full Automation API surface."
        )

    def test_awesome_list_badge_residue_recovers_prose(self):
        assert summarize_description(fixture("awesome_list_badge_residue.md")) == (
            "Token-budgeted web fetch for AI agents."
        )

    def test_prose_with_pipes_is_not_treated_as_byline(self):
        raw = (
            "Read-only, self-scoped ABR review session lookup over the Midway-gated endpoint. "
            "Tools: get_my_sessions (next | upcoming | recent | past-all), get_my_profile."
        )
        assert summarize_description(raw) == (
            "Read-only, self-scoped ABR review session lookup over the Midway-gated endpoint."
        )

    def test_short_lead_sentence_is_extended_with_next(self):
        raw = "Cradle Edit. Edits Cradle jobs and profiles from your assistant. More text here."
        assert summarize_description(raw) == (
            "Cradle Edit. Edits Cradle jobs and profiles from your assistant."
        )

    def test_fallbacks_heading_then_first_line(self):
        assert summarize_description("# Only Heading") == "Only Heading"
        assert summarize_description("- first item\n- second item") == "first item"

    @pytest.mark.parametrize("raw", [
        "Shell command execution server",
        "MCP server for Pippin (https://pippin.sara.amazon.dev/) tools",
    ])
    def test_short_plain_descriptions_pass_through(self, raw):
        assert summarize_description(raw) == raw

    @pytest.mark.parametrize("raw", ["", "   ", None, 42])
    def test_empty_or_non_string_returns_empty(self, raw):
        assert summarize_description(raw) == ""

    def test_unpunctuated_runon_capped_on_word_boundary(self):
        s = summarize_description(("word " * 80).strip(), max_len=50)
        assert len(s) <= 50
        assert s.endswith("…")
        assert not s.endswith("wor…")


def _bare_manager(tmp_path):
    """A RegistryIntegrationManager with only the config path wired.

    __init__ boots every registry provider and the MCP manager; the seams
    under test need neither.
    """
    from app.mcp.registry_manager import RegistryIntegrationManager
    mgr = RegistryIntegrationManager.__new__(RegistryIntegrationManager)
    mgr.config_path = str(tmp_path / "mcp_config.json")
    return mgr


class TestRegistryManagerSeams:
    def test_install_writes_one_line_description_to_config(self, tmp_path):
        mgr = _bare_manager(tmp_path)
        entries = {
            "enabled": True,
            "command": "slack-mcp",
            "description": fixture("slack_mcp_readme.md"),
            "registry_provider": "amazon-internal",
            "service_id": "slack-mcp",
        }
        mgr._add_to_config("slack-mcp", entries)

        written = json.loads(Path(mgr.config_path).read_text())["mcpServers"]["slack-mcp"]
        assert written["description"].startswith("A Slack MCP server for Amazon Enterprise Slack")
        assert len(written["description"]) <= 160
        # The other fields are untouched.
        assert written["command"] == "slack-mcp"
        assert written["service_id"] == "slack-mcp"

    def test_install_without_description_is_unchanged(self, tmp_path):
        mgr = _bare_manager(tmp_path)
        mgr._add_to_config("plain", {"enabled": True, "command": "plain-mcp"})
        written = json.loads(Path(mgr.config_path).read_text())["mcpServers"]["plain"]
        assert "description" not in written

    def test_installed_listing_summarises_legacy_blob_descriptions(self, tmp_path):
        """Configs written before the summariser existed still carry the blob."""
        mgr = _bare_manager(tmp_path)
        config = {"mcpServers": {
            "quip_mcp": {
                "registry_provider": "amazon-internal",
                "service_id": "quip-mcp",
                "description": fixture("quip_mcp_toollist.md"),
            },
            "no_desc": {"registry_provider": "open-mcp", "service_id": "x"},
        }}
        installed = {s["server_name"]: s for s in mgr._get_installed_from_config(config)}
        assert installed["quip_mcp"]["service_name"] == (
            "Comprehensive MCP server for Amazon Quip providing 37 tools "
            "covering the full Automation API surface."
        )
        assert installed["no_desc"]["service_name"] == "no_desc"
