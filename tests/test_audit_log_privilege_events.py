"""
ASR LOG-01 — privilege-relevant config changes must reach the audit trail.

Two classes of change survive a restart and alter what code can run, and
neither was audited:

  * an **MCP install** writes ``config_entries['command']`` into
    ``mcp_config.json``, which is executed on every subsequent server start
  * a **shell-config change** (command allowlist, YOLO) rewrites the shell
    server's privilege surface

The ``log_security_event`` infrastructure already existed (used by the response
validator, the encoding scanner and memory provenance); these are the two
call sites that were missing. Forensic hardening -- so a later compromise can
be traced back to the change that introduced it.
"""

import json
from datetime import datetime, timezone

import pytest

import app.utils.tool_audit_log as audit


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    """Fresh audit directory under the sandboxed ZIYA_HOME.

    ``_LOG_DIR`` is a module-level cache resolved on first use, so it must be
    dropped or a directory computed against the developer's real home leaks
    into the test (and the assertions read an empty sandbox forever).
    """
    monkeypatch.delenv("ZIYA_DISABLE_AUDIT_LOG", raising=False)
    monkeypatch.setattr(audit, "_DISABLED", False)
    monkeypatch.setattr(audit, "_LOG_DIR", None)
    yield
    monkeypatch.setattr(audit, "_LOG_DIR", None)


def _events(event_name=None):
    """Read back today's security events."""
    log_dir = audit._ensure_log_dir()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = log_dir / f"security_{today}.jsonl"
    if not path.exists():
        return []
    entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if event_name is not None:
        entries = [e for e in entries if e["eventName"] == event_name]
    return entries


@pytest.fixture
def shell_config(tmp_path, monkeypatch):
    """Point the persisted shell config at a tmp file.

    ``_mcp_config_path()`` resolves ``Path.home()/.ziya/mcp_config.json``
    directly rather than through ZIYA_HOME, so without this the test would
    rewrite the developer's real allowlist.
    """
    import app.config.shell_config as sc

    cfg_path = tmp_path / "mcp_config.json"
    monkeypatch.setattr(sc, "_mcp_config_path", lambda: cfg_path)
    return sc, cfg_path


# ---------------------------------------------------------------------------
# MCP install
# ---------------------------------------------------------------------------

class TestMcpInstallAudited:
    @staticmethod
    def _add_to_config(entries, server_name="acme-tool"):
        """Drive ``_add_to_config`` without constructing the real manager.

        The constructor initializes every registry provider and the MCP
        manager; none of that participates in the property under test.
        """
        from app.mcp.registry_manager import RegistryIntegrationManager

        saved = {}

        class _Stub:
            def _load_current_config(self):
                return {}

            def _save_config(self, config):
                saved.update(config)

        RegistryIntegrationManager._add_to_config(_Stub(), server_name, entries)
        return saved

    def test_install_writes_an_audit_event(self, audit_dir):
        self._add_to_config({
            "command": ["npx", "-y", "acme-mcp"],
            "service_id": "acme/tool",
            "registry_provider": "official-mcp",
        })
        events = _events("mcp_server_installed")
        assert len(events) == 1

    def test_event_records_what_will_be_executed(self, audit_dir):
        """The command is the forensically interesting field -- it is what runs
        on every future server start."""
        self._add_to_config({
            "command": ["npx", "-y", "acme-mcp"],
            "service_id": "acme/tool",
            "registry_provider": "official-mcp",
        })
        details = _events("mcp_server_installed")[0]["details"]
        assert details["server_name"] == "acme-tool"
        assert details["service_id"] == "acme/tool"
        assert details["provider"] == "official-mcp"
        assert "acme-mcp" in details["command"]

    def test_remote_install_records_the_url(self, audit_dir):
        self._add_to_config({
            "remote_url": "https://mcp.example.com/mcp",
            "transport": "streamable-http",
            "service_id": "acme/remote",
        })
        details = _events("mcp_server_installed")[0]["details"]
        assert details["remote_url"] == "https://mcp.example.com/mcp"

    def test_config_is_still_written(self, audit_dir):
        """Positive control: auditing was added alongside the write, not in
        place of it."""
        saved = self._add_to_config({"command": ["npx", "acme"]})
        assert saved["mcpServers"]["acme-tool"] == {"command": ["npx", "acme"]}

    def test_audit_failure_does_not_break_the_install(self, audit_dir, monkeypatch):
        """The audit call is wrapped precisely so a logging problem cannot fail
        an install. Assert that, or the wrapping is untested and someone will
        eventually 'tidy' it away."""
        def _boom(*a, **k):
            raise OSError("audit volume full")

        monkeypatch.setattr(audit, "log_security_event", _boom)
        saved = self._add_to_config({"command": ["npx", "acme"]})
        assert "mcpServers" in saved


# ---------------------------------------------------------------------------
# Shell config
# ---------------------------------------------------------------------------

class TestShellAllowlistChangeAudited:
    def test_change_writes_an_event(self, audit_dir, shell_config):
        sc, _ = shell_config
        sc.set_persisted_allowed_commands(["ls", "cat"])
        assert len(_events("shell_allowlist_changed")) == 1

    def test_event_records_the_delta_not_just_the_new_value(
        self, audit_dir, shell_config
    ):
        """A snapshot of the new list makes a reviewer diff two log lines to
        learn what changed; the delta is the whole point of the record."""
        sc, _ = shell_config
        sc.set_persisted_allowed_commands(["ls", "cat", "grep"])
        sc.set_persisted_allowed_commands(["ls", "curl"])

        latest = _events("shell_allowlist_changed")[-1]["details"]
        assert "curl" in latest["added"]
        assert "cat" in latest["removed"]
        assert "grep" in latest["removed"]
        assert "ls" not in latest["added"]
        assert "ls" not in latest["removed"]

    def test_allowlist_is_still_persisted(self, audit_dir, shell_config):
        sc, cfg_path = shell_config
        sc.set_persisted_allowed_commands(["ls", "cat"])
        env = json.loads(cfg_path.read_text())["mcpServers"]["shell"]["env"]
        assert env["ALLOW_COMMANDS"] == "ls,cat"


class TestYoloModeChangeAudited:
    def test_enabling_with_operator_optin_is_audited(
        self, audit_dir, shell_config, monkeypatch
    ):
        """YOLO bypasses the command allowlist entirely, so a successful
        persist is the single highest-signal shell-config event."""
        monkeypatch.setenv("ZIYA_ALLOW_PERSISTENT_YOLO", "1")
        sc, cfg_path = shell_config
        sc.set_yolo_mode(True)

        events = _events("shell_yolo_mode_changed")
        assert len(events) == 1
        assert events[0]["details"]["enabled"] == "true"
        env = json.loads(cfg_path.read_text())["mcpServers"]["shell"]["env"]
        assert env["YOLO_MODE"] == "true"

    def test_disabling_is_audited(self, audit_dir, shell_config):
        sc, _ = shell_config
        sc.set_yolo_mode(False)
        events = _events("shell_yolo_mode_changed")
        assert len(events) == 1
        assert events[0]["details"]["enabled"] == "false"


class TestEventShape:
    """All three events must be queryable the same way as the pre-existing
    security events, or the new records are invisible to whatever a defender
    already greps for."""

    def test_entries_carry_the_common_envelope(self, audit_dir, shell_config):
        sc, _ = shell_config
        sc.set_persisted_allowed_commands(["ls"])
        entry = _events("shell_allowlist_changed")[0]
        for key in (
            "eventTime", "eventCategory", "eventName",
            "userIdentity", "principalType", "sourceHostname",
            "sourceTool", "details",
        ):
            assert key in entry, f"missing envelope field {key}"
        assert entry["eventCategory"] == "security"
        assert entry["sourceTool"] == "shell_config"

    def test_events_land_in_the_security_stream(self, audit_dir, shell_config):
        """Not the general tool-execution stream -- the split is what lets a
        defender query defensive/privilege events in isolation."""
        sc, _ = shell_config
        sc.set_persisted_allowed_commands(["ls"])
        log_dir = audit._ensure_log_dir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert (log_dir / f"security_{today}.jsonl").exists()
