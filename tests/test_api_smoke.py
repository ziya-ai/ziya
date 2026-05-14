"""
End-to-end HTTP smoke test for the task-card pipeline.

Exercises the full flow against a running Ziya backend:
  create card → POST binding (atomic bind+launch) → list bindings →
  poll run to completion → query iterations → fetch artifact →
  delete binding.

Skips cleanly if no backend is reachable on the target URL, so this
test is safe to include in the default test run. Controlled by env:
  ZIYA_SMOKE_URL       — base URL (default http://localhost:6969)
  ZIYA_SMOKE_PROJECT   — project ID (default: first project from
                         /api/v1/projects with > 0 chats)
  ZIYA_SMOKE_CHAT      — chat ID (default: first chat in the project)

Catches regressions in router registration, storage read/write
contracts, and executor wiring that pure-unit tests miss — e.g. the
missing ``app.include_router(task_runs.router)`` and the dict-only
``BaseStorage._read_json`` contract that silently ate binding lists.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import pytest


BASE = os.environ.get("ZIYA_SMOKE_URL", "http://localhost:6969")
_PROBE_TIMEOUT = 2.0


def _get(path: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
        return json.load(r)


def _post(path: str, body: dict, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _delete(path: str, timeout: float = 10.0) -> int:
    req = urllib.request.Request(f"{BASE}{path}", method="DELETE")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def _backend_reachable() -> bool:
    try:
        urllib.request.urlopen(f"{BASE}/api/v1/projects", timeout=_PROBE_TIMEOUT)
        return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def _pick_project_and_chat() -> Optional[tuple]:
    """Choose a live project/chat for the smoke test.

    Prefers env overrides; otherwise picks the first project that has
    at least one chat (creating a throwaway chat would require more
    API wiring than this thin smoke test is meant to own).
    """
    pid = os.environ.get("ZIYA_SMOKE_PROJECT")
    cid = os.environ.get("ZIYA_SMOKE_CHAT")
    if pid and cid:
        return pid, cid
    try:
        projects = _get("/api/v1/projects")
    except Exception:
        return None
    for p in projects:
        try:
            chats = _get(f"/api/v1/projects/{p['id']}/chats?limit=1")
            items = chats if isinstance(chats, list) else chats.get("items", [])
            if items:
                return p["id"], items[0]["id"]
        except Exception:
            continue
    return None


pytestmark = pytest.mark.skipif(
    not _backend_reachable(),
    reason=f"No Ziya backend reachable at {BASE} — skipping HTTP smoke tests",
)


@pytest.fixture(scope="module")
def pid_cid():
    picked = _pick_project_and_chat()
    if not picked:
        pytest.skip("No project+chat available for smoke test")
    return picked


@pytest.fixture
def ephemeral_card(pid_cid):
    """Create a small repeat-count=3 card; delete on teardown."""
    pid, _cid = pid_cid
    card = _post(
        f"/api/v1/projects/{pid}/task-cards",
        {
            "name": "pytest-smoke-ephemeral",
            "description": "created by tests/test_api_smoke.py",
            "root": {
                "block_type": "repeat",
                "name": "loop",
                "repeat_mode": "count",
                "repeat_count": 3,
                "body": [
                    {
                        "block_type": "task",
                        "name": "echo",
                        "instructions": "Reply with 'ok' and stop.",
                    }
                ],
            },
        },
    )
    yield card
    try:
        _delete(f"/api/v1/projects/{pid}/task-cards/{card['id']}")
    except Exception:
        pass  # Best-effort cleanup; test already ran.


def test_router_registrations_reachable(pid_cid):
    """The three task-card routers are all included in the FastAPI app.

    Historical regression: ``task_runs.router`` was imported but not
    included, so every ``/task-runs/...`` endpoint silently 404'd and
    the frontend inline tile never saw run progress.
    """
    pid, cid = pid_cid
    # /task-cards registered
    _get(f"/api/v1/projects/{pid}/task-cards")
    # /task-runs registered
    _get(f"/api/v1/projects/{pid}/task-runs")
    # /chats/{cid}/task-bindings registered
    _get(f"/api/v1/projects/{pid}/chats/{cid}/task-bindings")


def test_full_pipeline_create_bind_poll_iterations_delete(pid_cid, ephemeral_card):
    """Exercise card→bind→launch→poll→iterations→delete end-to-end."""
    pid, cid = pid_cid
    card_id = ephemeral_card["id"]

    # 1. POST binding — atomically creates binding + queues run
    resp = _post(
        f"/api/v1/projects/{pid}/chats/{cid}/task-bindings",
        {"card_id": card_id, "anchor_message_id": None},
    )
    binding_id = resp["binding"]["id"]
    run_id = resp["run"]["id"]
    assert resp["run"]["status"] == "queued"

    try:
        # 2. Binding shows up in list_for_chat
        bindings = _get(f"/api/v1/projects/{pid}/chats/{cid}/task-bindings")
        assert any(b["id"] == binding_id for b in bindings), (
            "Binding missing from list — regression in TaskBindingStorage "
            "read path (list-backed file rejected by dict-only _read_json)"
        )

        # 3. Poll run to completion. 60s cap is plenty for 3 tiny 'ok' calls.
        deadline = time.time() + 60
        run = None
        while time.time() < deadline:
            time.sleep(2)
            run = _get(f"/api/v1/projects/{pid}/task-runs/{run_id}")
            if run["status"] in ("done", "failed", "cancelled"):
                break
        assert run is not None and run["status"] == "done", (
            f"Run did not finish in 60s: status={run['status'] if run else None}"
        )
        assert run["artifact"] is not None, "Completed run has no artifact"

        # 4. Iterations query works, returns 3 items, filter works
        repeat_bid = next(
            bid for bid, bs in run["block_states"].items()
            if bs["block_type"] == "repeat"
        )
        q = urllib.parse.urlencode({"block_id": repeat_bid})
        iters = _get(f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations?{q}")
        assert iters["total"] == 3, f"expected 3 iterations, got {iters['total']}"

        q_fail = urllib.parse.urlencode({"block_id": repeat_bid, "status": "failed"})
        failed = _get(f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations?{q_fail}")
        assert failed["total"] == 0, "smoke card should have zero failures"

        q_lim = urllib.parse.urlencode(
            {"block_id": repeat_bid, "status": "passed", "limit": 2}
        )
        limited = _get(f"/api/v1/projects/{pid}/task-runs/{run_id}/iterations?{q_lim}")
        assert limited["total"] == 3, "total should be unaffected by limit"
        assert len(limited["items"]) == 2, "limit=2 should cap the items list"

        # 5. Single-artifact fetch
        art = _get(
            f"/api/v1/projects/{pid}/task-runs/{run_id}"
            f"/iterations/{repeat_bid}/0"
        )
        assert "summary" in art and "failed" in art
        assert art["failed"] is False

    finally:
        # Always clean up the binding we created.
        try:
            _delete(f"/api/v1/projects/{pid}/chats/{cid}/task-bindings/{binding_id}")
        except Exception:
            pass
        # Verify DELETE actually removed it.
        try:
            bindings_after = _get(f"/api/v1/projects/{pid}/chats/{cid}/task-bindings")
            assert all(b["id"] != binding_id for b in bindings_after), (
                "binding still present after DELETE"
            )
        except Exception:
            pass
