"""
Regression guard for the sam/cdk infrastructure-deploy block (ASR H-6).

`ShellServer._iac_deploy_blocked` keeps `sam` and `cdk` allowlisted for all
read/build use but denies the verbs that provision or destroy real account
infrastructure: `sam deploy`, `cdk deploy`, `cdk destroy`. This pins that
narrow scope so a future edit that widens the blocked set (re-adding
build/synth/diff/bootstrap/etc.) or narrows it (dropping a deploy verb) fails
loudly.

The guard has no instance-state dependency, so we construct the server with
__new__ to avoid the env/config setup ShellServer.__init__ performs.
"""

import pytest

from app.mcp_servers.shell_server import ShellServer


@pytest.fixture(scope="module")
def guard():
    srv = ShellServer.__new__(ShellServer)  # no __init__: guard is pure
    return srv._iac_deploy_blocked


# Verbs that stand up or tear down real infrastructure — must be denied.
BLOCKED = [
    "sam deploy",
    "sam deploy --stack-name foo --region us-west-2",
    "cdk deploy",
    "cdk deploy MyStack",
    "cdk destroy",
    "cdk destroy --force MyStack",
    # global option consuming a value before the subcommand must not hide it
    "cdk --app build deploy",
    "cdk --profile prod deploy",
    "cdk --context env=prod deploy",
]

# Everything else sam/cdk can do, plus unrelated commands — must pass through.
ALLOWED = [
    "sam build",
    "sam validate",
    "sam local invoke",
    "sam local start-api",
    "sam sync",
    "sam publish",
    "cdk synth",
    "cdk diff",
    "cdk ls",
    "cdk bootstrap",
    "cdk import",
    "cdk migrate",
    "cdk rollback",
    "cdk --app build synth",
    # not sam/cdk at all -> guard is a no-op
    "aws s3 ls",
    "echo deploy",
    "ls -la",
    "git status",
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_deploy_destroy_is_blocked(guard, cmd):
    reason = guard(cmd)
    assert reason is not None, f"expected {cmd!r} to be blocked"
    assert "blocked" in reason.lower()


@pytest.mark.parametrize("cmd", ALLOWED)
def test_non_deploy_is_allowed(guard, cmd):
    assert guard(cmd) is None, f"expected {cmd!r} to be allowed"


def test_scope_is_exactly_deploy_destroy(guard):
    """Belt-and-suspenders: a brand-new sam/cdk verb is allowed by default
    (fail-open for non-deploy), and only the three known-dangerous verbs flip
    to blocked. Guards against an over-broad future denylist."""
    assert guard("sam package") is None
    assert guard("cdk doctor") is None
    assert guard("cdk acknowledge 12345") is None
    # the three that must stay blocked
    assert guard("sam deploy") is not None
    assert guard("cdk deploy") is not None
    assert guard("cdk destroy") is not None
