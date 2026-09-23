"""
The shell-config routes rebuild the in-memory shell server config that
``restart_server`` spawns from. Two defects lived in that rebuild:

1. ``workspace_scoped`` was omitted, so ``MCPManager._is_workspace_scoped``
   fell back to keyword auto-detection over the script path. On a checkout
   under ``.../workspace/...`` that matched by accident; on a pip install
   (``site-packages/app/mcp_servers/shell_server.py``) it did not, and after
   any shell-config save the shell server became global-scoped and ran
   commands in the server's cwd instead of the project.

2. ``POST /shell-config`` assembled ``env`` from the request only, so even
   when the file still held a ZIYA_SCOPE_SIG covering exactly this escalation
   (no privileged field changed) the spawned subprocess had no signature and
   clamped to the floor, while GET /shell-config -- which reads the file --
   reported "authorized".

Both are pinned at the helper level so the tests need no running manager.
"""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import scope_canonical as sc
from app.mcp.manager import MCPManager
from app.routes.mcp_routes import _build_shell_server_config, _carry_file_signature


@pytest.fixture
def keyed(tmp_path, monkeypatch):
    priv = tmp_path / "approve_ed25519"
    pub = tmp_path / "approve_ed25519.pub"
    key = Ed25519PrivateKey.generate()
    priv.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    pub.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
    ))
    monkeypatch.setenv("ZIYA_APPROVE_PRIVKEY", str(priv))
    monkeypatch.setenv("ZIYA_APPROVE_PUBKEY", str(pub))
    return key


def _sign_env(env: dict) -> str:
    return sc.sign_delta(sc.compute_delta(sc.parse_env_scope(env)))


# ── workspace_scoped ────────────────────────────────────────────────────────────

def test_built_config_is_workspace_scoped_regardless_of_install_path():
    cfg = _build_shell_server_config({"ALLOW_COMMANDS": "ls"})
    assert cfg["workspace_scoped"] is True
    assert cfg["builtin"] is True
    assert cfg["env"] == {"ALLOW_COMMANDS": "ls"}

    # Seam: the manager must classify a shell entry built by the route as
    # workspace-scoped even when the script path carries no detection keyword
    # (the pip-install shape).
    m = MCPManager()
    m.server_configs = {
        "shell": {**cfg, "args": ["-u", "/opt/venv/lib/python3.12/site-packages/app/mcp_servers/shell_server.py"]}
    }
    assert m._is_workspace_scoped("shell") is True


def test_manager_autodetect_misses_pip_install_path_without_flag():
    """Documents WHY the flag is required: the same entry minus the flag is not
    recognised on a site-packages path. If auto-detection ever starts matching
    this shape the explicit flag becomes belt-and-braces, not load-bearing."""
    m = MCPManager()
    cfg = _build_shell_server_config({})
    cfg.pop("workspace_scoped")
    cfg["args"] = ["-u", "/opt/venv/lib/python3.12/site-packages/app/mcp_servers/shell_server.py"]
    m.server_configs = {"shell": cfg}
    assert m._is_workspace_scoped("shell") is False


def test_built_config_honours_enabled_flag():
    assert _build_shell_server_config({}, enabled=False)["enabled"] is False
    assert _build_shell_server_config({})["enabled"] is True


# ── signature carry-forward ─────────────────────────────────────────────────────

def test_sig_carried_when_delta_unchanged(keyed):
    file_env = {"ALLOW_COMMANDS": "ls,ada,aws", "COMMAND_TIMEOUT": "30"}
    file_env["ZIYA_SCOPE_SIG"] = _sign_env(file_env)
    # A non-privileged edit (timeout) with the same escalation.
    new_env = {"ALLOW_COMMANDS": "ls,ada,aws", "COMMAND_TIMEOUT": "120"}

    out = _carry_file_signature(new_env, file_env)

    assert out["ZIYA_SCOPE_SIG"] == file_env["ZIYA_SCOPE_SIG"]
    assert sc.is_env_scope_authorized(out), "carried sig must verify for the new env"


def test_sig_carried_when_only_order_differs(keyed):
    file_env = {"ALLOW_COMMANDS": "ls,ada,aws"}
    file_env["ZIYA_SCOPE_SIG"] = _sign_env(file_env)
    out = _carry_file_signature({"ALLOW_COMMANDS": "aws,ls,ada"}, file_env)
    assert out.get("ZIYA_SCOPE_SIG") == file_env["ZIYA_SCOPE_SIG"]
    assert sc.is_env_scope_authorized(out)


def test_sig_dropped_when_escalation_widens(keyed):
    file_env = {"ALLOW_COMMANDS": "ls,ada"}
    file_env["ZIYA_SCOPE_SIG"] = _sign_env(file_env)
    out = _carry_file_signature({"ALLOW_COMMANDS": "ls,ada,aws"}, file_env)
    assert "ZIYA_SCOPE_SIG" not in out
    assert sc.is_env_scope_authorized(out) is False


def test_sig_dropped_when_escalation_narrows_to_floor(keyed):
    file_env = {"ALLOW_COMMANDS": "ls,ada"}
    file_env["ZIYA_SCOPE_SIG"] = _sign_env(file_env)
    out = _carry_file_signature({"ALLOW_COMMANDS": "ls"}, file_env)
    # No escalation -> nothing to sign; a stale sig must not be attached.
    assert "ZIYA_SCOPE_SIG" not in out
    assert sc.is_env_scope_authorized(out) is True


def test_no_file_sig_means_no_sig(keyed):
    out = _carry_file_signature({"ALLOW_COMMANDS": "ls,ada"}, {"ALLOW_COMMANDS": "ls,ada"})
    assert "ZIYA_SCOPE_SIG" not in out


def test_incoming_sig_is_never_trusted(keyed):
    """The request/in-memory env can't smuggle its own signature; only the
    file's is considered."""
    out = _carry_file_signature(
        {"ALLOW_COMMANDS": "ls,ada", "ZIYA_SCOPE_SIG": "forged"},
        {"ALLOW_COMMANDS": "ls"},
    )
    assert "ZIYA_SCOPE_SIG" not in out


def test_carry_does_not_mutate_inputs(keyed):
    file_env = {"ALLOW_COMMANDS": "ls,ada"}
    file_env["ZIYA_SCOPE_SIG"] = _sign_env(file_env)
    new_env = {"ALLOW_COMMANDS": "ls,ada"}
    _carry_file_signature(new_env, file_env)
    assert "ZIYA_SCOPE_SIG" not in new_env
