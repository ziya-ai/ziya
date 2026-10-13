"""The shell seam: a script run by the shell server can record ledger entries
with the calling block's provenance (design/evidence-ledger.md, Tests).

Hops, each asserted:
  MCPManager.call_tool  --_ledger_scope-->  ShellServer  --env-->  child process
  child (ziya ledger / ziya_ledger_client)  --X-Ziya-Ledger-Scope-->  /api/ledger
  /api/ledger  -->  .ziya/ledger/*.jsonl with provenance from the token.
Fails on unpatched code because ZIYA_LEDGER_SCOPE is absent from the child.
"""
import asyncio
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils.evidence import scope, store as st


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIYA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ZIYA_LEDGER_URL", raising=False)
    monkeypatch.delenv("ZIYA_LEDGER_SCOPE", raising=False)
    st._key_cache.clear()
    st._ledgers.clear()


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    (p / "evidence").mkdir(parents=True)
    (p / "evidence" / "s1.csv").write_text("errors,requests\n22464,120000\n")
    return p


def _api():
    from app.api.ledger import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# -- token -------------------------------------------------------------------

def test_token_round_trips_and_rejects_tampering_and_expiry(proj):
    tok = scope.issue(str(proj), {"conversation_id": "c1", "run_id": "r1", "block_id": "b1"})
    p = scope.verify(tok)
    assert p["project_root"] == str(proj) and p["block_id"] == "b1"
    assert scope.provenance_from_scope(p) == {"via": "shell", "conversation_id": "c1",
                                              "run_id": "r1", "block_id": "b1"}
    b64, _, sig = tok.rpartition(".")
    assert scope.verify(b64 + "." + ("0" * len(sig))) is None          # bad signature
    assert scope.verify(tok[:-1] + ("a" if tok[-1] != "a" else "b")) is None
    assert scope.verify(scope.issue(str(proj), ttl_s=-1)) is None       # expired
    assert scope.verify(None) is None and scope.verify("junk") is None


# -- shell server -> child env ----------------------------------------------

def _shell_env_seen(proj, ledger_scope):
    """Run `env` through the real ShellServer request handler; return the child's env."""
    from app.mcp_servers.shell_server import ShellServer
    srv = ShellServer()
    srv.yolo_mode = True
    args = {"command": "env", "_project_root": str(proj)}
    if ledger_scope is not None:
        args["_ledger_scope"] = ledger_scope
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "run_shell_command", "arguments": args}}

    async def go():
        return await srv.handle_request(req)
    resp = asyncio.run(go())
    text = resp["result"]["content"][0]["text"]
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("$"))


def test_shell_child_sees_scope_variables_only_when_given(proj):
    env = _shell_env_seen(proj, {"url": "http://127.0.0.1:6969/api/ledger", "token": "T"})
    assert env["ZIYA_LEDGER_URL"] == "http://127.0.0.1:6969/api/ledger"
    assert env["ZIYA_LEDGER_SCOPE"] == "T"
    env2 = _shell_env_seen(proj, None)
    assert "ZIYA_LEDGER_SCOPE" not in env2 and "ZIYA_LEDGER_URL" not in env2
    assert "PATH" in env2                                            # positive control


# -- manager attaches the envelope -------------------------------------------

@pytest.mark.asyncio
async def test_manager_attaches_ledger_scope_for_shell_only(proj, monkeypatch):
    from app.mcp.manager import MCPManager
    from app.context import set_usage_attribution

    captured = {}

    class _Client:
        is_connected = True
        server_name = "fake"
        server_config = {"name": "fake"}

        def __init__(self):
            self.tools = [MagicMock()]
            self.tools[0].name = "run_shell_command"

        async def call_tool(self, name, arguments):
            captured[name] = arguments
            return {"content": [{"type": "text", "text": "ok"}]}

    m = MCPManager()
    m.clients = {"shell": _Client(), "other": _Client()}
    m.clients["other"].tools[0].name = "other_tool"
    m.server_configs = {}
    monkeypatch.setattr(MCPManager, "_is_workspace_scoped", lambda self, n: False)
    fake_perms = MagicMock()
    fake_perms.get_permissions.return_value = {"defaults": {"tool": "enabled"}, "servers": {}}
    tok = set_usage_attribution({"source": "task", "run_id": "run-9", "block_id": "blk-2"})
    try:
        with patch("app.mcp.permissions.get_permissions_manager", return_value=fake_perms):
            await m.call_tool("run_shell_command",
                              {"command": "true", "_workspace_path": str(proj),
                               "conversation_id": "conv-7"}, server_name="shell")
            await m.call_tool("other_tool", {"x": 1, "_workspace_path": str(proj)},
                              server_name="other")
    finally:
        from app.context import reset_usage_attribution
        reset_usage_attribution(tok)
    ls = captured["run_shell_command"].get("_ledger_scope")
    assert ls and ls["url"].endswith("/api/ledger")
    p = scope.verify(ls["token"])
    assert p["project_root"] == str(proj)
    assert (p["conversation_id"], p["run_id"], p["block_id"]) == ("conv-7", "run-9", "blk-2")
    assert "_ledger_scope" not in captured["other_tool"]           # shell only


# -- end to end: child -> API -> ledger --------------------------------------

def test_claim_recorded_from_a_shell_child_carries_block_provenance(proj, monkeypatch):
    """The spec's shell seam test: the token the child received is accepted by
    the API, and the claim it records names the calling block."""
    envelope = scope.build_envelope(str(proj), "conv-1")
    env = _shell_env_seen(proj, envelope)
    monkeypatch.setenv("ZIYA_LEDGER_SCOPE", env["ZIYA_LEDGER_SCOPE"])
    monkeypatch.setenv("ZIYA_LEDGER_URL", "http://testserver/api/ledger")

    # Route the stdlib client's urllib POST into the in-process TestClient.
    from app.utils import ziya_ledger_client as lc
    client = _api()

    def fake_post(path, body, timeout=30.0):
        r = client.post(f"/api/ledger/{path}", json=body,
                        headers={lc.HEADER: os.environ[lc.ENV_SCOPE]})
        out = r.json()
        if r.status_code >= 400:
            raise lc.LedgerUnavailable(f"ledger {path}: HTTP {r.status_code}: {out.get('message')}")
        return out
    monkeypatch.setattr(lc, "_post", fake_post)

    obs = lc.observe("sql", query_text="SELECT 1", result_path="evidence/s1.csv")
    assert obs["attested"] is True
    c = lc.claim("s1.errors", 22464, namespace="audit", unit="requests", from_obs=obs["id"],
                 population={"description": "requests", "n": 120000},
                 derivation={"column": "cut"})
    assert c["id"] == "audit.s1.errors" and c["status"] == "recorded"

    led = st.ledger_for(str(proj))
    rec = led.claim("audit.s1.errors")
    assert rec["provenance"] == {"via": "shell", "conversation_id": "conv-1"}
    assert led.observation(obs["id"])["provenance"]["via"] == "shell"

    v = lc.verify(prefix="audit")
    assert v["counts"] == {"agrees": 1}
    assert lc.query("audit.s1.errors")["claim"]["status"] == "verified"
    # Outside-project path refused at the API too.
    with pytest.raises(lc.LedgerUnavailable, match="outside"):
        lc.observe("file", result_path="/etc/passwd")


def test_api_refuses_missing_bad_and_foreign_tokens(proj):
    client = _api()
    body = {"observation": {"source": "tool", "inline": 1}}
    assert client.post("/api/ledger/record", json=body).status_code == 401
    assert client.post("/api/ledger/record", json=body,
                       headers={scope.HEADER: "not.a.token"}).status_code == 401
    expired = scope.issue(str(proj), ttl_s=-5)
    assert client.post("/api/ledger/record", json=body,
                       headers={scope.HEADER: expired}).status_code == 401
    assert st.ledger_for(str(proj)).read("observation") == []
    # Positive control: a live token records.
    ok = client.post("/api/ledger/record", json=body,
                     headers={scope.HEADER: scope.issue(str(proj))})
    assert ok.status_code == 200 and ok.json()["observation"]["attested"] is False


def test_client_without_env_explains_itself():
    from app.utils import ziya_ledger_client as lc
    assert not lc.available()
    with pytest.raises(lc.LedgerUnavailable, match="ZIYA_LEDGER_URL"):
        lc.observe("tool", inline=1)


# -- CLI ---------------------------------------------------------------------

def test_cli_observe_claim_query_verify(proj, monkeypatch, capsys):
    from app.cli import create_parser
    from app.utils import ziya_ledger_client as lc
    client = _api()
    monkeypatch.setenv("ZIYA_LEDGER_SCOPE", scope.issue(str(proj), {"block_id": "b7"}))
    monkeypatch.setenv("ZIYA_LEDGER_URL", "http://testserver/api/ledger")

    def fake_post(path, body, timeout=30.0):
        r = client.post(f"/api/ledger/{path}", json=body, headers={lc.HEADER: os.environ[lc.ENV_SCOPE]})
        if r.status_code >= 400:
            raise lc.LedgerUnavailable(r.json().get("message", r.text))
        return r.json()
    monkeypatch.setattr(lc, "_post", fake_post)

    def run(argv):
        a = create_parser().parse_args(argv)
        a.func(a)
        return capsys.readouterr().out.strip()

    sql = proj / "q.sql"
    sql.write_text("SELECT sum(cut) FROM t")
    oid = run(["ledger", "observe", "--source", "sql", "--sql-file", str(sql),
               "--connector", "duckdb", "--result", "evidence/s1.csv"])
    assert oid.startswith("obs_")
    pin = run(["ledger", "claim", "audit.s1.error_rate", "0.1872", "--unit", "ratio",
               "--from", oid, "--population", "retried requests", "--n", "120000",
               "--derivation", "sum(cut)/sum(paths)", "--tolerance", "rel=0.02"])
    assert pin == "audit.s1.error_rate@v1"
    led = st.ledger_for(str(proj))
    c = led.claim("audit.s1.error_rate")
    assert c["value"] == 0.1872 and c["tolerance"] == {"rel": 0.02}
    assert c["population"] == {"description": "retried requests", "n": 120000}
    assert c["derivation"]["expression"] == "sum(cut)/sum(paths)"
    assert c["provenance"] == {"via": "shell", "block_id": "b7"}
    assert led.observation(oid)["query_text"] == "SELECT sum(cut) FROM t"

    out = json.loads(run(["ledger", "query", "s1.error_rate", "--namespace", "audit"]))
    assert out["value"] == 0.1872
    out = json.loads(run(["ledger", "verify", "--prefix", "audit"]))
    assert out["counts"] == {"agrees": 1}
    # A drifted verify exits 1 so a script can notice.
    (proj / "evidence" / "s1.csv").write_text("changed\n")
    with pytest.raises(SystemExit) as e:
        run(["ledger", "verify", "audit.s1.error_rate"])
    assert e.value.code == 1


def test_cli_without_scope_exits_2_with_explanation(capsys):
    from app.cli import create_parser
    a = create_parser().parse_args(["ledger", "observe", "--source", "tool", "--inline", "1"])
    with pytest.raises(SystemExit) as e:
        a.func(a)
    assert e.value.code == 2
    assert "ZIYA_LEDGER_URL" in capsys.readouterr().err
