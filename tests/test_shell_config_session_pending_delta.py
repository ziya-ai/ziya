"""
The staged temporary-grant banner must list WHAT is requested, not just that
something is (2026-09-19 report: the persistent "Unsigned privilege
escalation" banner itemises its delta; "Temporary grant staged" showed none,
so when both were visible there was no way to tell the two requests apart).

``_session_pending_status`` (app/routes/mcp_routes.py) reads the transient
pending file written by /shell-config/request-session-grant and reports its
beyond-floor delta with the SAME canonical computation the persistent banner
uses (``_compute_signature_status``), so the two banners list like for like.

Seams pinned here:
  - no pending file            -> pending False, delta {}
  - floor-only pending file    -> pending True,  delta {}   (banner says "nothing beyond floor")
  - escalated pending file     -> pending True,  delta lists the extra commands
  - unreadable pending file    -> pending True,  delta {}   (Apply/Discard stay reachable)
  - GET /shell-config          -> exposes sessionPendingDelta alongside sessionPending
  - request-session-grant      -> response carries pendingDelta (no re-fetch needed)
"""

import json

import pytest

from app.routes import mcp_routes
from app.routes.mcp_routes import _session_pending_status, _compute_signature_status


FLOOR_ENV = {"ALLOW_COMMANDS": "ls,cat,grep"}
ESCALATED_ENV = {"ALLOW_COMMANDS": "ls,cat,grep,/usr/bin/danger,git push"}


def test_no_pending_file(tmp_path):
    assert _session_pending_status(tmp_path) == {"pending": False, "delta": {}}


def test_floor_only_pending_reports_empty_delta(tmp_path):
    (tmp_path / "pending_session_shell.json").write_text(json.dumps(FLOOR_ENV))
    status = _session_pending_status(tmp_path)
    assert status["pending"] is True
    assert status["delta"] == {}


def test_escalated_pending_lists_delta(tmp_path):
    (tmp_path / "pending_session_shell.json").write_text(json.dumps(ESCALATED_ENV))
    status = _session_pending_status(tmp_path)
    assert status["pending"] is True
    listed = " ".join(" ".join(v) for v in status["delta"].values())
    assert "/usr/bin/danger" in listed
    assert "git push" in listed
    # like-for-like with the persistent banner's computation
    assert status["delta"] == _compute_signature_status(ESCALATED_ENV)["pendingDelta"]


def test_unreadable_pending_still_reports_pending(tmp_path):
    (tmp_path / "pending_session_shell.json").write_text("{not json")
    status = _session_pending_status(tmp_path)
    assert status["pending"] is True
    assert status["delta"] == {}


def test_non_dict_pending_still_reports_pending(tmp_path):
    (tmp_path / "pending_session_shell.json").write_text(json.dumps(["ls"]))
    assert _session_pending_status(tmp_path) == {"pending": True, "delta": {}}


# ── seams: the endpoints actually carry the delta to the UI ───────────────────

class _FakeClient:
    is_connected = True


class _FakeManager:
    is_initialized = True
    clients = {"shell": _FakeClient()}
    server_configs = {"shell": {"env": {}}}
    _session_grants = {}
    _session_nonce = "nonce"
    _ephemeral_pubkey_b64 = None


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ziya").mkdir(parents=True)
    monkeypatch.setattr(mcp_routes.Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("ZIYA_ENABLE_MCP", "true")
    monkeypatch.setattr(mcp_routes, "get_mcp_manager", lambda: _FakeManager())
    return home


@pytest.mark.asyncio
async def test_get_shell_config_exposes_session_pending_delta(fake_home, monkeypatch):
    import app.config.shell_config as shell_config
    monkeypatch.setattr(shell_config, "_read_mcp_config", lambda: {})
    (fake_home / ".ziya" / "pending_session_shell.json").write_text(json.dumps(ESCALATED_ENV))

    data = await mcp_routes.get_shell_config()

    assert data["sessionPending"] is True
    assert "sessionPendingDelta" in data
    listed = " ".join(" ".join(v) for v in data["sessionPendingDelta"].values())
    assert "/usr/bin/danger" in listed


@pytest.mark.asyncio
async def test_request_session_grant_returns_delta(fake_home):
    cfg = mcp_routes.ShellConfig(
        allowedCommands=["ls", "cat", "grep", "/usr/bin/danger"],
    )
    result = await mcp_routes.request_session_grant(cfg)

    assert result["success"] is True
    assert "pendingDelta" in result
    listed = " ".join(" ".join(v) for v in result["pendingDelta"].values())
    assert "/usr/bin/danger" in listed
    # and the file the signer reads agrees with what the UI was told
    on_disk = _session_pending_status(fake_home / ".ziya")
    assert on_disk["delta"] == result["pendingDelta"]
