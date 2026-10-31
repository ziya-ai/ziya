"""
Regression guard for ``ShellServer._aws_subcommand_blocked``.

Three properties, each of which was wrong on 2026-10-16:

1. ``aws s3 cp s3://bucket/key /tmp/x`` is a download -- the same read as
   ``s3api get-object``, which the guard allows -- and must pass.  Only
   writes INTO S3 (upload, bucket-to-bucket, mv/rm/rb/mb) stay blocked.
2. The refusal must say the rule is Ziya's built-in subcommand policy, not
   the user's allowlist; the previous message read as a bug when the Shell
   Configuration modal plainly showed ``aws`` approved.
3. An explicit, verified allowlist grant of ``aws`` (or ``aws <service>`` /
   ``aws <service> <action>``) lifts the policy.  The grant is read from the
   clamped scope env, so an unsigned ``aws`` hand-written into
   mcp_config.json must NOT lift it.
"""
import os

import pytest

from app.config.scope_canonical import ESCALATION_ENV_KEYS
from app.mcp_servers.shell_server import ShellServer


def _pure(explicit=()):
    srv = ShellServer.__new__(ShellServer)  # guard needs no __init__ state
    srv.explicit_commands = set(explicit)
    return srv


# -- 1. download vs. write ---------------------------------------------------

DOWNLOADS = [
    "aws s3 cp s3://buildertoolbox-ziya-us-west-2/index.json /tmp/ziya-index.json",
    "aws --profile ziya-admin s3 cp s3://b/k /tmp/k",
    "aws s3 cp s3://b/prefix/ /tmp/out/ --recursive",
    "aws s3 sync s3://b/prefix /tmp/out",
    "aws s3 ls s3://b/",
    "aws s3api get-object --bucket b --key k /tmp/k",
]

S3_WRITES = [
    "aws s3 cp /tmp/k s3://b/k",
    "aws s3 cp /tmp/k s3://b/k --sse",            # dest not last: still an upload
    "aws s3 cp - s3://b/k",                       # stdin upload
    "aws s3 cp s3://a/k s3://b/k",                # bucket-to-bucket
    "aws s3 sync /tmp/out s3://b/prefix",
    "aws s3 mv s3://b/k /tmp/k",                  # deletes the source object
    "aws s3 rm s3://b/k",
    "aws s3 rb s3://b",
    "aws s3 mb s3://b",
    "aws s3 cp s3://b/k",                         # no local destination
]


@pytest.mark.parametrize("cmd", DOWNLOADS)
def test_s3_reads_are_allowed(cmd):
    assert _pure()._aws_subcommand_blocked(cmd) is None, cmd


@pytest.mark.parametrize("cmd", S3_WRITES)
def test_s3_writes_stay_blocked(cmd):
    assert _pure()._aws_subcommand_blocked(cmd) is not None, cmd


@pytest.mark.parametrize("cmd", [
    "aws sts assume-role --role-arn x --role-session-name y",
    "aws iam create-user --user-name x",
    "aws --region us-west-2 iam attach-user-policy --user-name x --policy-arn y",
    "aws s3api put-object --bucket b --key k --body /tmp/k",
    "aws lambda invoke --function-name f /tmp/o",
    "aws cloudformation deploy --stack-name s",
    "aws secretsmanager get-secret-value --secret-id s",
])
def test_other_escalation_legs_unchanged(cmd):
    assert _pure()._aws_subcommand_blocked(cmd) is not None, cmd


# -- 2. the refusal names its source -------------------------------------------

def test_refusal_says_built_in_policy_not_allowlist():
    reason = _pure()._aws_subcommand_blocked("aws s3 cp /tmp/k s3://b/k")
    assert "'aws s3 cp'" in reason
    assert "built-in aws subcommand policy" in reason
    assert "not by your command allowlist" in reason
    assert "Shell Configuration" in reason


# -- 3. an explicit grant lifts it --------------------------------------------

UPLOAD = "aws s3 cp /tmp/k s3://b/k"
IAM = "aws iam create-user --user-name x"


def test_bare_aws_grant_lifts_every_leg():
    srv = _pure({"aws"})
    assert srv._aws_subcommand_blocked(UPLOAD) is None
    assert srv._aws_subcommand_blocked(IAM) is None


def test_service_grant_lifts_only_that_service():
    srv = _pure({"aws s3"})
    assert srv._aws_subcommand_blocked(UPLOAD) is None
    assert srv._aws_subcommand_blocked(IAM) is not None


def test_action_grant_lifts_only_that_action():
    srv = _pure({"aws s3 cp"})
    assert srv._aws_subcommand_blocked(UPLOAD) is None
    assert srv._aws_subcommand_blocked("aws s3 rm s3://b/k") is not None


def test_guard_without_init_state_still_blocks():
    srv = ShellServer.__new__(ShellServer)  # no explicit_commands attribute at all
    assert srv._aws_subcommand_blocked(UPLOAD) is not None


# -- seam: ALLOW_COMMANDS env -> explicit_commands -> is_command_allowed ------

@pytest.fixture
def clean_scope(monkeypatch):
    for key in ESCALATION_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("YOLO_MODE", raising=False)


def test_within_floor_grants_reach_explicit_commands(clean_scope, monkeypatch):
    monkeypatch.setenv("ALLOW_COMMANDS", "ls,cat")
    srv = ShellServer()
    assert {"ls", "cat"} <= srv.explicit_commands


def test_unsigned_aws_in_env_does_not_lift_the_policy(clean_scope, monkeypatch):
    # The override must be forgery-resistant: 'aws' is beyond the public
    # floor, so without a ziya-approve signature the scope is clamped and the
    # entry never becomes an explicit grant.
    monkeypatch.setenv("ALLOW_COMMANDS", "ls,aws")
    srv = ShellServer()
    assert "aws" not in srv.explicit_commands
    assert srv._aws_subcommand_blocked(UPLOAD) is not None


def test_download_passes_is_command_allowed_when_aws_is_allowlisted(clean_scope):
    # Outermost surface: with 'aws' admitted the way the enterprise plugin
    # admits it (allowlist, no explicit grant), a download is allowed and an
    # upload is refused with the explanatory message.
    srv = ShellServer()
    srv.allowed_commands.append("aws")
    srv.safe_command_patterns = srv._build_safe_command_patterns()
    ok, _ = srv.is_command_allowed("aws s3 cp s3://b/k /tmp/k")
    assert ok
    ok, reason = srv.is_command_allowed(UPLOAD)
    assert not ok and "built-in aws subcommand policy" in reason
