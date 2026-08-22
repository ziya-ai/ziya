"""Shell commands require a caller-supplied project root; the environment is not a fallback.

Background
----------
The shell server used to derive BOTH its working directory and the root its
write policy resolved relative ``safe_write_paths`` against from
``ZIYA_USER_CODEBASE_DIR`` **in its own process environment**, fixed at spawn::

    cwd = os.environ.get("ZIYA_USER_CODEBASE_DIR")          # shell_server.py
    project_root = ... or os.environ.get("ZIYA_USER_CODEBASE_DIR", "")  # write_policy.py

That value names whichever project the process was launched in — not the
project the current call belongs to. The two coincide for a single server
launched in a single project, which is why this held for years. It diverges
when a subprocess outlives a project switch, when a second server runs on
another port, or when an MCP "restart" respawns the shell with a config that
never set the variable. The observed symptom was a shell rooted one directory
ABOVE the selected project for an entire session, silently: relative
``safe_write_paths`` such as ``.ziya/`` and ``tests/`` resolved against the
wrong tree, so the write gate authorized writes into a project nobody in the
request chain had named, while ``file_write`` on the same paths resolved
correctly.

The fix makes the root per-call data end to end and its absence an error:

  1. ``MCPManager.call_tool`` resolves the root and re-attaches it to shell
     calls as ``_project_root`` (the routing keys are otherwise stripped).
  2. ``MCPClient`` passes it through argument validation.
  3. ``ShellWriteChecker`` holds it in a ContextVar and threads it into
     ``WritePolicyManager``, which also loads THAT project's policy.
  4. ``ShellServer`` uses it as the cwd and REFUSES the call if absent.

What these tests pin
--------------------
Per-hop forwarding is asserted at each seam, because the costliest version of
this bug is two correct halves that never meet — the manager attaching a key
the server never reads, or the server reading a key nothing attaches. The
end-to-end tests drive the real ``handle_request`` and assert on the outermost
surface (the JSON-RPC response and observable filesystem effects) rather than
on an intermediate the test constructed.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.mcp_servers.shell_server import ShellServer
from app.mcp_servers.write_policy import ShellWriteChecker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def shell_server():
    """A ShellServer at the default policy floor, YOLO disabled.

    YOLO is cleared because it short-circuits the write check entirely, which
    would mask the root-anchoring behaviour under test.
    """
    old_yolo = os.environ.pop("YOLO_MODE", None)
    try:
        yield ShellServer()
    finally:
        if old_yolo is not None:
            os.environ["YOLO_MODE"] = old_yolo


@pytest.fixture
def two_roots():
    """Two distinct real directories, each with a ``.ziya/`` subdirectory.

    ``.ziya/`` is a default relative safe_write_path, so it is the cleanest
    probe for *which* root the write gate anchored to: the same relative path
    is writable under both roots, and only the anchoring distinguishes them.
    """
    with tempfile.TemporaryDirectory(prefix="ziya_root_a_") as a, \
         tempfile.TemporaryDirectory(prefix="ziya_root_b_") as b:
        os.makedirs(os.path.join(a, ".ziya"), exist_ok=True)
        os.makedirs(os.path.join(b, ".ziya"), exist_ok=True)
        yield os.path.realpath(a), os.path.realpath(b)


def _call(command, project_root=None, task_scope=None, req_id=1):
    """Build a tools/call request, omitting _project_root when None."""
    arguments = {"command": command}
    if project_root is not None:
        arguments["_project_root"] = project_root
    if task_scope is not None:
        arguments["_task_scope"] = task_scope
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": "run_shell_command", "arguments": arguments},
    }


# ---------------------------------------------------------------------------
# 1. Refusal when no root is supplied  (the fail-closed contract)
# ---------------------------------------------------------------------------

class TestRefusalWithoutRoot:

    @pytest.mark.asyncio
    async def test_missing_root_is_refused(self, shell_server):
        """A call with no _project_root must not execute."""
        resp = await shell_server.handle_request(_call("echo hello"))
        assert "error" in resp, f"expected refusal, got: {resp}"
        assert "NO PROJECT ROOT" in resp["error"]["message"]
        assert "result" not in resp

    @pytest.mark.asyncio
    async def test_environment_is_not_a_fallback(self, shell_server, two_roots):
        """Setting ZIYA_USER_CODEBASE_DIR must NOT rescue a rootless call.

        This is the specific regression: the env var used to be consulted here,
        which is what let a misrooted subprocess run silently.
        """
        root_a, _ = two_roots
        old = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        os.environ["ZIYA_USER_CODEBASE_DIR"] = root_a
        try:
            resp = await shell_server.handle_request(_call("pwd"))
        finally:
            if old is None:
                os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)
            else:
                os.environ["ZIYA_USER_CODEBASE_DIR"] = old
        assert "error" in resp, (
            "the process environment rescued a call that supplied no root — "
            "the env fallback has returned"
        )
        assert "NO PROJECT ROOT" in resp["error"]["message"]

    @pytest.mark.asyncio
    async def test_nonexistent_root_is_refused(self, shell_server):
        """A root that isn't a directory is unusable, so refuse rather than
        silently fall back to the process cwd."""
        resp = await shell_server.handle_request(
            _call("echo hi", project_root="/nonexistent/ziya/xyz123")
        )
        assert "error" in resp
        assert "NO PROJECT ROOT" in resp["error"]["message"]

    @pytest.mark.asyncio
    async def test_empty_root_is_refused(self, shell_server):
        resp = await shell_server.handle_request(_call("echo hi", project_root=""))
        assert "error" in resp
        assert "NO PROJECT ROOT" in resp["error"]["message"]


# ---------------------------------------------------------------------------
# 2. Positive path — the supplied root is actually USED as the cwd
# ---------------------------------------------------------------------------

class TestSuppliedRootIsHonored:
    """Paired with the refusals above: assert the path RAN, not just that it
    didn't refuse."""

    @pytest.mark.asyncio
    async def test_pwd_reports_the_supplied_root(self, shell_server, two_roots):
        root_a, _ = two_roots
        resp = await shell_server.handle_request(_call("pwd", project_root=root_a))
        assert "error" not in resp, f"expected success, got: {resp}"
        text = resp["result"]["content"][0]["text"]
        assert root_a in text, f"command did not run in {root_a}: {text}"

    @pytest.mark.asyncio
    async def test_root_switches_per_call_on_one_server(self, shell_server, two_roots):
        """The decisive test: ONE server instance, two calls, two roots.

        Under the old design the root was fixed for the subprocess's lifetime,
        so a single instance could not honour two different roots. This is what
        makes the root per-call data rather than process state.
        """
        root_a, root_b = two_roots
        resp_a = await shell_server.handle_request(
            _call("pwd", project_root=root_a, req_id=1)
        )
        resp_b = await shell_server.handle_request(
            _call("pwd", project_root=root_b, req_id=2)
        )
        text_a = resp_a["result"]["content"][0]["text"]
        text_b = resp_b["result"]["content"][0]["text"]
        assert root_a in text_a and root_b not in text_a
        assert root_b in text_b and root_a not in text_b

    @pytest.mark.asyncio
    async def test_env_disagreeing_with_call_root_does_not_win(
        self, shell_server, two_roots
    ):
        """When the env names root A and the call names root B, B wins.

        This is the exact production divergence: a subprocess whose spawn-time
        env points at the wrong tree.
        """
        root_a, root_b = two_roots
        old = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        os.environ["ZIYA_USER_CODEBASE_DIR"] = root_a
        try:
            resp = await shell_server.handle_request(_call("pwd", project_root=root_b))
        finally:
            if old is None:
                os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)
            else:
                os.environ["ZIYA_USER_CODEBASE_DIR"] = old
        text = resp["result"]["content"][0]["text"]
        assert root_b in text, f"call root lost to the environment: {text}"
        assert root_a not in text


# ---------------------------------------------------------------------------
# 3. The write gate anchors to the CALL's root, not the environment
# ---------------------------------------------------------------------------

class TestWriteGateAnchoring:
    """The consequence that mattered most: relative safe_write_paths resolving
    against the wrong tree authorized writes into an unnamed project."""

    def test_checker_exposes_the_call_root(self):
        from app.config.write_policy import WritePolicyManager
        checker = ShellWriteChecker(WritePolicyManager())
        assert checker._project_root == ""
        checker.set_project_root("/some/root")
        try:
            assert checker._project_root == "/some/root"
        finally:
            checker.clear_project_root()
        assert checker._project_root == ""

    def test_root_is_passed_into_write_policy_manager(self, two_roots):
        """``is_write_allowed`` must receive the call root as its 2nd argument.

        Without it the manager falls back to its own resolution order (which
        ends at the environment), so the relative path is anchored to whatever
        project last touched the shared singleton.
        """
        root_a, _ = two_roots
        seen = {}

        class _RecordingPM:
            policy = {"safe_write_paths": [".ziya/"], "always_blocked": []}

            def is_write_allowed(self, target_path, project_root=""):
                seen["target"] = target_path
                seen["root"] = project_root
                return True

        checker = ShellWriteChecker(_RecordingPM())
        checker.set_project_root(root_a)
        try:
            checker._is_write_allowed(".ziya/state.json")
        finally:
            checker.clear_project_root()

        assert seen["root"] == root_a, (
            f"write policy was consulted with root {seen.get('root')!r} instead "
            f"of the call's root {root_a!r}"
        )

    @pytest.mark.asyncio
    async def test_relative_safe_path_write_lands_in_the_call_root(
        self, shell_server, two_roots
    ):
        """End-to-end: ``.ziya/`` is writable under BOTH roots, so only the
        anchoring decides where the bytes land. Assert on the filesystem."""
        root_a, root_b = two_roots
        resp = await shell_server.handle_request(
            _call("touch .ziya/probe.txt", project_root=root_b)
        )
        assert "error" not in resp, f"expected success, got: {resp}"
        assert os.path.exists(os.path.join(root_b, ".ziya", "probe.txt")), (
            "write did not land under the root the caller named"
        )
        assert not os.path.exists(os.path.join(root_a, ".ziya", "probe.txt")), (
            "write landed under a root nobody named — the anchoring bug"
        )

    @pytest.mark.asyncio
    async def test_outside_root_write_still_blocked(self, shell_server, two_roots):
        """Supplying a root must not become a way to widen the policy: a path
        outside the declared safe areas is still denied."""
        root_a, _ = two_roots
        resp = await shell_server.handle_request(
            # mkdir is a destructive command whose target is write-checked.
            _call("mkdir forbidden", project_root=root_a)
        )
        assert "error" in resp
        msg = resp["error"]["message"]
        assert "WRITE BLOCKED" in msg or "blocked" in msg.lower(), msg
        # Specifically NOT the root refusal — the root was supplied, so this
        # must be the write-policy denial.
        assert "NO PROJECT ROOT" not in msg


# ---------------------------------------------------------------------------
# 4. The manager -> shell seam  (the "two correct halves" hazard)
# ---------------------------------------------------------------------------

class TestManagerAttachesRootForShell:

    def test_manager_source_attaches_project_root_for_shell(self):
        """The manager must re-attach the root AFTER stripping routing keys.

        Pinned against the source because the attach and the strip are 15 lines
        apart in one function: a future edit that moves the strip below the
        attach would silently delete the key again, and every shell call would
        start refusing.
        """
        import inspect
        from app.mcp import manager as mgr

        src = inspect.getsource(mgr.MCPManager.call_tool)
        strip_pos = src.find("_workspace_path', 'conversation_id'")
        attach_pos = src.find('"_project_root": workspace_path')
        assert strip_pos != -1, "routing-key strip not found; update this test"
        assert attach_pos != -1, (
            "call_tool no longer attaches _project_root for the shell server — "
            "every shell command will be refused"
        )
        assert attach_pos > strip_pos, (
            "_project_root is attached BEFORE the routing-key strip, so it is "
            "removed again before dispatch"
        )

    def test_manager_has_no_environment_fallback(self):
        """The env fallback must stay gone.

        It is the mechanism by which a stale process root silently became the
        call's root, so its return would reintroduce the original bug.
        """
        import inspect
        from app.mcp import manager as mgr

        src = inspect.getsource(mgr.MCPManager.call_tool)
        env_reads = (
            "os.environ.get('ZIYA_USER_CODEBASE_DIR')",
            'os.environ.get("ZIYA_USER_CODEBASE_DIR")',
        )
        assert not any(expr in src for expr in env_reads), (
            "call_tool consults ZIYA_USER_CODEBASE_DIR again — the process "
            "environment is not a valid source for a per-call project root"
        )

    def test_client_passes_project_root_through_validation(self):
        """A key the validator doesn't know is warned about and can be dropped
        by future strictness; _project_root must be registered."""
        from app.mcp.client import MCPClient
        assert "_project_root" in MCPClient._KNOWN_ROUTING_KEYS

    def test_shell_server_reads_the_key_the_manager_sends(self):
        """Both halves must name the SAME key.

        A rename on either side leaves two individually-correct pieces that
        never meet: the manager attaches a key the server ignores, and the
        server refuses every call.
        """
        import inspect
        from app.mcp import manager as mgr

        server_src = inspect.getsource(ShellServer.handle_request)
        manager_src = inspect.getsource(mgr.MCPManager.call_tool)
        assert 'arguments.pop("_project_root"' in server_src, (
            "shell server no longer pops _project_root"
        )
        assert '"_project_root": workspace_path' in manager_src


# ---------------------------------------------------------------------------
# 5. Interaction with the task-scope envelope
# ---------------------------------------------------------------------------

class TestTaskScopeInteraction:

    @pytest.mark.asyncio
    async def test_task_scope_call_still_needs_a_root(self, shell_server):
        """A task grant does not substitute for a root: the grant's own paths
        are resolved relative to one."""
        resp = await shell_server.handle_request(
            _call("echo hi", task_scope={"writable": [], "shell_commands": []})
        )
        assert "error" in resp
        assert "NO PROJECT ROOT" in resp["error"]["message"]

    def test_task_scope_root_takes_precedence_over_call_root(self, two_roots):
        """Both are caller-supplied per call, so they agree in practice; the
        envelope wins because the grant was minted against that frame."""
        root_a, root_b = two_roots
        from app.config.write_policy import WritePolicyManager

        checker = ShellWriteChecker(WritePolicyManager())
        checker.set_project_root(root_a)
        checker.set_task_scope({
            "project_root": root_b,
            "writable": [{"path": "granted", "is_dir": True}],
        })
        try:
            # The grant entry is relative, so it resolves under whichever root
            # won. Only root_b's resolution can match.
            assert checker._task_scope_grants_write(
                os.path.join(root_b, "granted", "f.txt")
            )
            assert not checker._task_scope_grants_write(
                os.path.join(root_a, "granted", "f.txt")
            )
        finally:
            checker.clear_task_scope()
            checker.clear_project_root()

    def test_task_grant_does_not_fall_back_to_environment(self, two_roots):
        """With neither root supplied, a relative grant entry must not resolve
        against ZIYA_USER_CODEBASE_DIR."""
        root_a, _ = two_roots
        from app.config.write_policy import WritePolicyManager

        old = os.environ.get("ZIYA_USER_CODEBASE_DIR")
        os.environ["ZIYA_USER_CODEBASE_DIR"] = root_a
        checker = ShellWriteChecker(WritePolicyManager())
        checker.set_task_scope({"writable": [{"path": "granted", "is_dir": True}]})
        try:
            assert not checker._task_scope_grants_write(
                os.path.join(root_a, "granted", "f.txt")
            ), (
                "a relative task grant resolved against the process "
                "environment — the env fallback has returned"
            )
        finally:
            checker.clear_task_scope()
            if old is None:
                os.environ.pop("ZIYA_USER_CODEBASE_DIR", None)
            else:
                os.environ["ZIYA_USER_CODEBASE_DIR"] = old


# ---------------------------------------------------------------------------
# 6. The CLI must state a root  (it has no HTTP layer to supply one)
# ---------------------------------------------------------------------------

class TestCLISuppliesRoot:

    def test_cli_tool_stream_passes_project_root(self):
        """``CLI._run_with_tools_from_messages`` enters the tool executor.

        It previously relied on the manager's env fallback; with that gone it
        must pass project_root explicitly or every CLI shell command refuses.
        """
        import inspect
        from app import cli

        src = inspect.getsource(cli.CLI._run_with_tools_from_messages)
        assert "project_root=" in src, (
            "CLI._run_with_tools_from_messages no longer passes project_root "
            "— CLI shell commands will all be refused"
        )


# ---------------------------------------------------------------------------
# 7. The existing propagation test's mirror must not drift
# ---------------------------------------------------------------------------

def test_propagation_mirror_matches_real_resolution():
    """``tests/test_project_root_propagation.py`` re-implements call_tool's
    root resolution locally (``_resolve_routing``) instead of importing it.

    A mirror cannot fail when the real code changes: after the env fallback was
    removed from ``call_tool``, that file's
    ``test_no_header_cli_mode_falls_back_to_env`` still passed while asserting
    behaviour the product no longer has. This guard fails if the mirror
    reintroduces an env fallback the real function doesn't have.
    """
    import inspect
    from app.mcp import manager as mgr

    real_src = inspect.getsource(mgr.MCPManager.call_tool)
    mirror_path = os.path.join(os.path.dirname(__file__),
                               "test_project_root_propagation.py")
    with open(mirror_path) as fh:
        mirror_src = fh.read()

    env_reads = (
        "os.environ.get('ZIYA_USER_CODEBASE_DIR')",
        'os.environ.get("ZIYA_USER_CODEBASE_DIR")',
    )
    real_has_env_fallback = any(expr in real_src for expr in env_reads)
    mirror_has_env_fallback = "startup_cwd" in mirror_src

    assert real_has_env_fallback == mirror_has_env_fallback, (
        f"the propagation test's _resolve_routing mirror disagrees with the "
        f"real call_tool: real env fallback={real_has_env_fallback}, "
        f"mirror env fallback={mirror_has_env_fallback}. The mirror is now "
        f"certifying behaviour the product does not have."
    )
