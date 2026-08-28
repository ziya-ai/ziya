"""Executable policy for registry-supplied MCP commands (ASR SC-01 / SC-02).

A registry API response is not a trust boundary. A compromised or MITM'd
registry entry can name any executable, and two distinct sinks consume that
value:

1. **Install sink** — ``subprocess.run([executable] + args)`` at install time.
2. **Run sink** — the value written to ``config_entries['command']``, which is
   persisted into ``~/.ziya/mcp_config.json`` and executed on every subsequent
   MCP server start. This one is *persistent* code execution.

Before this module only the install sink was allowlisted, in one provider. The
run sink was unconstrained in all four providers that write it. Both halves now
share these definitions so the two cannot drift and a new provider cannot
silently reintroduce the gap.
"""

import os
import re
from pathlib import Path
from typing import Any, FrozenSet, List, Optional, Sequence, Union

from app.utils.logging_utils import logger


# Executables permitted as the argv[0] of a *persisted MCP run command*.
# These are interpreters/launchers whose argv we construct ourselves
# (InstallationHelper) or that take a package/module spec rather than a shell
# string. Deliberately excludes shells (sh/bash/zsh) and anything that takes an
# arbitrary command to run.
#
# Note `docker` IS permitted here but NOT as an installer (see
# DEFAULT_INSTALL_EXECUTABLES): the run argv is built by
# InstallationHelper.setup_docker_container() as a fixed
# ['docker','run','-i','--rm',<image>], whereas a registry-supplied *install*
# docker argv is unconstrainable by any flag denylist ('docker run -v /:/host'
# is a full host escape using no denied flag).
ALLOWED_RUN_EXECUTABLES: FrozenSet[str] = frozenset({
    "npx", "node", "python", "python3", "uv", "uvx", "docker",
})

# Executables permitted as an *installer*. Package managers only.
DEFAULT_INSTALL_EXECUTABLES: FrozenSet[str] = frozenset({
    "pip", "pip3", "npm", "npx", "uv", "uvx",
})

# Source-redirection flags, per installer. These are the dependency-confusion
# vector: they repoint the installer at an attacker-controlled index.
_REDIRECT_FLAGS = {
    "pip":  ("-i", "--index-url", "--extra-index-url", "--find-links",
             "-f", "--trusted-host", "--config-settings", "--target",
             "--prefix", "--root", "--editable", "-e"),
    "npm":  ("--registry", "--userconfig", "--globalconfig", "--prefix"),
    "npx":  ("--registry", "--userconfig", "--globalconfig"),
    "uv":   ("--index-url", "--extra-index-url", "--default-index",
             "--find-links", "--index"),
}
_REDIRECT_FLAGS["pip3"] = _REDIRECT_FLAGS["pip"]
_REDIRECT_FLAGS["uvx"] = _REDIRECT_FLAGS["uv"]

# Flags that turn an allowlisted *run* launcher into an arbitrary-code
# executor, per launcher. The run sink is persisted to mcp_config.json and
# re-executed on every server start, so "python3 -c <payload>" there is
# durable RCE even though python3 itself is a legitimate launcher.
#
# Deliberately narrow. Run args legitimately carry URLs and absolute paths
# ("npx mcp-remote https://...", "uvx --from git+ssh://...",
# "python /abs/server.py"), so the install-side "://" and absolute-path rules
# must NOT be applied here -- they would break real servers. Only inline-code
# and container-escape flags are denied; -m, -u, -y and --from stay allowed.
_RUN_CODE_EXEC_FLAGS = {
    "python":  ("-c",),
    "python3": ("-c",),
    "node":    ("-e", "--eval", "-p", "--print"),
    "docker":  ("-v", "--volume", "--mount", "--privileged", "--device",
                "--cap-add", "--pid", "--userns", "--network"),
}

# BuilderToolbox is the ONLY transport for an Amazon-internal MCP tool:
# "toolbox registry add s3://<bucket>/tools.json" then "toolbox install <tool>".
# Refusing every "://" arg therefore made the whole toolbox-vended slice of the
# internal registry uninstallable -- a package spec really does need a URI here,
# unlike pip/npm where a URL is the dependency-confusion vector. The exemption
# is confined to the toolbox installer and to a strictly-shaped s3:// URI.
#
# Residual risk, accepted deliberately: a compromised registry entry can name an
# arbitrary S3 bucket. That is inherent to the toolbox mechanism -- there is no
# allowlist of legitimate buckets to check against (observed buckets span
# buildertoolbox-*, team-owned and account-id-prefixed names), so the bucket
# name is validated for SHAPE only.
_S3_SOURCE_INSTALLERS: FrozenSet[str] = frozenset({"toolbox"})

# Bucket: 3-63 chars, lowercase alnum/dot/hyphen, alnum at both ends.
# Key: no whitespace and no shell metacharacters. ".." is rejected separately
# because the character class alone would happily match a traversal.
_S3_URI_RE = re.compile(
    r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](/[A-Za-z0-9!_.*'()=/-]*)?$"
)

# Directory BuilderToolbox installs into. Registry entries name their run
# command as a bare tool name ("aae-atlas") or as $HOME/.toolbox/bin/<tool>.
_TOOLBOX_DIRNAME = ".toolbox"


class RegistryCommandRejected(ValueError):
    """Raised when a registry-supplied executable or argv is refused."""


def _basename(candidate: str) -> str:
    return os.path.basename(candidate.strip())


def _toolbox_root() -> Path:
    """Root of the BuilderToolbox install tree."""
    return Path.home() / _TOOLBOX_DIRNAME


def _resolve_toolbox_tool(candidate: str) -> Optional[str]:
    """Resolve a BuilderToolbox-managed executable, or None.

    Accepts a bare tool name ("aae-atlas") or an explicit path
    ("$HOME/.toolbox/bin/aae-atlas", "~/.toolbox/bin/aae-atlas"). Returns the
    stable <toolbox>/bin/<tool> path when the candidate really resolves to
    a file inside the toolbox tree, else None.

    Containment is checked on the REALPATH so a planted symlink pointing out
    of the tree is refused, but the returned value is the un-resolved symlink
    path so the persisted config does not pin a specific tool version.
    """
    raw = str(candidate).strip()
    if not raw:
        return None
    # Only HOME is expanded, and via Path.home() rather than os.environ, so a
    # registry-supplied value cannot be steered by an unrelated environment
    # variable (a generic expandvars() would interpolate anything).
    for token in ("${HOME}", "$HOME"):
        if raw.startswith(token):
            raw = str(Path.home()) + raw[len(token):]
            break
    raw = os.path.expanduser(raw)

    root = _toolbox_root()
    target = raw if os.sep in raw else str(root / "bin" / raw)
    if not os.path.isabs(target):
        return None

    try:
        root_real = os.path.realpath(str(root))
        target_real = os.path.realpath(target)
        # A name that is not installed yet resolves to nothing: refuse rather
        # than persist a command we cannot see on disk.
        if not os.path.isfile(target_real):
            return None
        if target_real == root_real or target_real.startswith(root_real + os.sep):
            return target
    except OSError:
        return None
    return None


def _is_allowed_s3_source(head: str, arg: str) -> bool:
    """Whether *arg* is an s3:// source URI this installer may be pointed at."""
    if head not in _S3_SOURCE_INSTALLERS:
        return False
    if ".." in arg:
        return False
    return bool(_S3_URI_RE.match(arg))


def _validate_run_args(
    head: str,
    args: Optional[Sequence[Any]],
    *,
    source: str,
) -> List[str]:
    """Filter registry-supplied run args (see _RUN_CODE_EXEC_FLAGS)."""
    if args is None:
        return []
    denied = _RUN_CODE_EXEC_FLAGS.get(head, ())
    clean: List[str] = []
    for arg in args:
        if not isinstance(arg, str):
            raise RegistryCommandRejected(
                f"{source}: run arg is {type(arg).__name__}, expected str"
            )
        if arg.split("=", 1)[0] in denied:
            raise RegistryCommandRejected(
                f"{source}: refusing run arg {arg!r} — it makes {head} "
                f"execute caller-supplied code on every server start"
            )
        clean.append(arg)
    return clean


def validate_run_command(
    command: Union[str, Sequence[str], None],
    *,
    source: str,
    install_dir: Optional[Union[str, Path]] = None,
    extra_executables: FrozenSet[str] = frozenset(),
    args: Optional[Sequence[Any]] = None,
) -> List[str]:
    """Validate a command destined for ``config_entries['command']``.

    Returns the command normalized to a list. Raises
    ``RegistryCommandRejected`` if argv[0] is neither an allowlisted launcher
    nor an absolute path contained in *install_dir* (the legitimate
    "run the entrypoint we just installed" case).

    *args*, when supplied, are the launcher's arguments; they are filtered
    against _RUN_CODE_EXEC_FLAGS and appended to the returned argv. Omitting
    args preserves the previous behaviour exactly (argv[0] only).

    *source* is a human-readable identifier (service id / provider) used only
    in the error and log message.
    """
    if command is None:
        raise RegistryCommandRejected(f"{source}: no run command supplied")
    if isinstance(command, str):
        argv = [command]
    else:
        argv = [str(part) for part in command]
    if not argv or not str(argv[0]).strip():
        raise RegistryCommandRejected(f"{source}: empty run command")

    head = str(argv[0]).strip()
    allowed = ALLOWED_RUN_EXECUTABLES | extra_executables
    # Everything after argv[0] is caller-supplied, whether it arrived inside a
    # command_array (official-mcp / github pass registry instructions.command
    # verbatim) or via the args= keyword. Both tails must be filtered, or
    # ['python3', '-c', <payload>] slips past the args= guard entirely.
    tail = list(argv[1:]) + list(args or [])

    # A bare name only. A RELATIVE path whose basename is allowlisted
    # ("../../tmp/evil/npx") was previously accepted here, because the branch
    # only rejected ABSOLUTE paths -- and the manager hands the value to
    # subprocess verbatim, resolving it against Ziya's cwd at every server
    # start. Absolute paths fall through to the install_dir check below.
    # Nothing legitimate takes this shape: every provider builds its own
    # entry point as str(install_dir / ...), i.e. absolute, so a relative
    # command can only come from a registry-supplied instructions.command.
    if head == _basename(head) and head in allowed:
        return argv[:1] + _validate_run_args(_basename(head), tail, source=source)

    # An absolute path is acceptable only when it resolves inside the
    # installation directory we created for this service.
    if os.path.isabs(head) and install_dir:
        try:
            root = os.path.realpath(str(install_dir))
            target = os.path.realpath(head)
            if target == root or target.startswith(root + os.sep):
                return argv[:1] + _validate_run_args(
                    _basename(head), tail, source=source
                )
        except OSError:
            pass

    # A BuilderToolbox-managed tool: the shape used by the toolbox-vended
    # slice of the internal registry, where the run command is the bare name
    # of the tool the install step just installed (e.g. "aae-atlas"), or an
    # explicit $HOME/.toolbox/bin/<tool> path. Accepted only when it really
    # resolves to a file inside the toolbox tree -- which is what keeps
    # "curl", "bash" and "open" refused: they live in /usr/bin, not ~/.toolbox.
    toolbox_target = _resolve_toolbox_tool(head)
    if toolbox_target:
        return [toolbox_target] + _validate_run_args(
            _basename(toolbox_target), tail, source=source
        )

    raise RegistryCommandRejected(
        f"{source}: refusing registry-supplied run command {head!r}. "
        f"argv[0] must be one of {sorted(allowed)}, an absolute path inside "
        f"the service's installation directory, or a BuilderToolbox tool "
        f"present under {_toolbox_root()}/bin. This value would be persisted "
        f"to mcp_config.json and executed on every server start."
    )


def validate_install_argv(
    executable: str,
    args: Sequence[Any],
    *,
    source: str,
    allowed_executables: FrozenSet[str] = DEFAULT_INSTALL_EXECUTABLES,
) -> List[str]:
    """Validate a registry-supplied installer invocation (ASR SC-02).

    There is no ``shell=True`` at the sink, so shell metacharacters are not
    the concern. The concern is *source redirection* — an arg set that points
    an allowlisted installer at an attacker-controlled index or file.

    Constraints, smallest set that leaves ordinary
    ``pip install name==ver`` / ``npm install pkg`` intact:
      1. argv[0] must be an allowlisted package manager.
      2. Every arg must be a ``str`` (a dict/list here is type confusion
         into subprocess).
      3. No source-redirection flag for that installer, including the
         ``--flag=value`` form.
      4. No arg containing ``://`` and no absolute path — a package spec
         needs neither, and both are how a poisoned entry redirects.
    """
    raw = str(executable).strip()
    head = _basename(raw)
    # Bare name only. Allowlisting on the basename alone accepted
    # "/tmp/evil/pip" and "../../evil/pip": the basename satisfies the
    # allowlist while the ORIGINAL path is what subprocess executes. Same
    # defect the run sink closes with `head == _basename(head)`. Installers
    # are resolved from PATH, so nothing legitimate supplies a path here.
    if raw != head or head not in allowed_executables:
        raise RegistryCommandRejected(
            f"{source}: refusing registry-supplied install executable "
            f"{executable!r}: must be a bare name drawn from "
            f"{sorted(allowed_executables)}"
        )

    denied = _REDIRECT_FLAGS.get(head, ())
    clean: List[str] = []
    for arg in args:
        if not isinstance(arg, str):
            raise RegistryCommandRejected(
                f"{source}: install arg is {type(arg).__name__}, expected str"
            )
        flag = arg.split("=", 1)[0]
        if flag in denied:
            raise RegistryCommandRejected(
                f"{source}: refusing install arg {arg!r} — {flag} redirects "
                f"{head} to a caller-supplied package source "
                f"(dependency confusion)"
            )
        if "://" in arg and not _is_allowed_s3_source(head, arg):
            raise RegistryCommandRejected(
                f"{source}: refusing install arg {arg!r} — a package spec "
                f"never contains a URL"
            )
        if os.path.isabs(arg):
            raise RegistryCommandRejected(
                f"{source}: refusing absolute-path install arg {arg!r}"
            )
        clean.append(arg)

    logger.info(f"{source}: install argv accepted: {[head] + clean}")
    # Return the canonical bare name rather than the raw input: the log line
    # previously reported ``pip`` while ``/tmp/evil/pip`` was what executed,
    # so the audit trail disagreed with what actually ran.
    return [head] + clean