"""OS registration of the ``ziya://`` scheme (app/utils/protocol_handler.py).

A ziya:// link only resolves inside the page, where ZiyaLinkNavigator
intercepts the click; pasted anywhere else the browser has no handler.  The
registered handler translates ``ziya://chat/<id>/<idx>`` into
``http://localhost:<port>/?open=<link>`` and opens it, and the SPA follows
``?open=`` at boot.  Pinned here:

  - the translation is exactly the prefix the SPA reads, for valid links
    only, and the artefact written for each platform carries it verbatim;
  - macOS: osacompile builds the applet, the plist gets the URL scheme and
    the port, lsregister is told about it; status reports that port;
  - Linux: script + .desktop under XDG_DATA_HOME, xdg-mime makes it default;
  - unregister removes what register wrote, and status goes back to missing;
  - the installer routes ``--links [--port N]`` here and never to the shell
    script (which rejects unknown options), and the registry does not fold
    it into ``--all``.
"""
import plistlib
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils import protocol_handler as ph


@pytest.fixture
def fake_osacompile(tmp_path):
    """Stand-in runner: osacompile writes the minimal bundle the real one
    would; every other command is recorded and succeeds."""
    calls = []

    def runner(cmd):
        calls.append(cmd)
        if cmd[0].endswith("osacompile"):
            app = Path(cmd[cmd.index("-o") + 1])
            (app / "Contents" / "MacOS").mkdir(parents=True)
            with (app / "Contents" / "Info.plist").open("wb") as f:
                plistlib.dump({"CFBundleExecutable": "applet"}, f)
            # The compiled handler is what the applet runs; keep its source
            # beside it so the test can assert on what was compiled.
            (app / "Contents" / "source.applescript").write_text(
                Path(cmd[-1]).read_text())
        return 0
    return runner, calls


def test_deep_link_url_is_the_spa_contract_for_valid_links_only():
    assert ph.deep_link_url("ziya://chat/abc-1/12", 6969) == \
        "http://localhost:6969/?open=ziya://chat/abc-1/12"
    assert ph.deep_link_url("ziya://project/p1", 8080) == \
        "http://localhost:8080/?open=ziya://project/p1"
    for bad in ("ziya://chat/../x", "https://example.com", "ziya://folder/x", ""):
        assert ph.deep_link_url(bad, 6969) is None


def test_every_platform_artefact_carries_the_same_prefix():
    prefix = "http://localhost:7001/?open="
    assert prefix in ph.build_applescript(7001)
    assert 'on open location' in ph.build_applescript(7001)
    assert "quoted form of" in ph.build_applescript(7001)   # one shell word
    assert prefix in ph.build_linux_script(7001)
    assert ph.windows_command(7001) == \
        f'rundll32.exe url.dll,FileProtocolHandler "{prefix}%1"'
    assert ph.plist_additions(7001)["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["ziya"]
    assert "MimeType=x-scheme-handler/ziya;" in ph.build_desktop_entry(Path("/x/h"))
    assert "Exec=/x/h %u" in ph.build_desktop_entry(Path("/x/h"))


def test_macos_register_builds_plists_and_registers(tmp_path, fake_osacompile):
    runner, calls = fake_osacompile
    with patch("app.utils.protocol_handler.shutil.which", return_value="/usr/bin/osacompile"):
        rc = ph.register(7001, platform="darwin", home=tmp_path, runner=runner)
    assert rc == 0
    app = ph.macos_app_path(tmp_path)
    assert calls[0][:2] == ["/usr/bin/osacompile", "-o"] and calls[0][2] == str(app)
    assert calls[1] == [ph.LSREGISTER, "-f", str(app)]
    with (app / "Contents" / "Info.plist").open("rb") as f:
        info = plistlib.load(f)
    assert info["CFBundleExecutable"] == "applet"            # generated keys kept
    assert info["CFBundleIdentifier"] == ph.BUNDLE_ID
    assert info["LSUIElement"] is True
    assert info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["ziya"]
    assert "http://localhost:7001/?open=" in (app / "Contents" / "source.applescript").read_text()
    assert ph.registered_port(platform="darwin", home=tmp_path) == 7001

    # Re-registering on another port replaces the app rather than failing.
    with patch("app.utils.protocol_handler.shutil.which", return_value="/usr/bin/osacompile"):
        assert ph.register(7002, platform="darwin", home=tmp_path, runner=runner) == 0
    assert ph.registered_port(platform="darwin", home=tmp_path) == 7002

    assert ph.unregister(platform="darwin", home=tmp_path, runner=runner) == 0
    assert calls[-1] == [ph.LSREGISTER, "-u", str(app)]
    assert not app.exists()
    assert ph.registered_port(platform="darwin", home=tmp_path) is None


def test_macos_dry_run_writes_nothing(tmp_path, fake_osacompile):
    runner, calls = fake_osacompile
    assert ph.register(7001, dry_run=True, platform="darwin", home=tmp_path, runner=runner) == 0
    assert calls == [] and not ph.macos_app_path(tmp_path).exists()


def test_macos_lsregister_failure_is_reported(tmp_path, fake_osacompile):
    runner, calls = fake_osacompile

    def failing(cmd):
        return 3 if cmd[0] == ph.LSREGISTER else runner(cmd)
    with patch("app.utils.protocol_handler.shutil.which", return_value="/usr/bin/osacompile"):
        assert ph.register(7001, platform="darwin", home=tmp_path, runner=failing) == 3


def test_linux_register_writes_script_desktop_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    calls = []
    with patch("app.utils.protocol_handler.shutil.which",
               side_effect=lambda n: "/usr/bin/" + n):
        rc = ph.register(7001, platform="linux", home=tmp_path, runner=lambda c: calls.append(c) or 0)
    assert rc == 0
    script, desktop = ph.linux_paths(tmp_path)
    assert script.read_text().startswith("#!/bin/sh")
    assert "http://localhost:7001/?open=$1" in script.read_text()
    assert script.stat().st_mode & 0o100
    assert f"Exec={script} %u" in desktop.read_text()
    assert ["xdg-mime", "default", ph.DESKTOP_ID, "x-scheme-handler/ziya"] in calls
    assert ph.registered_port(platform="linux", home=tmp_path) == 7001
    assert ph.unregister(platform="linux", home=tmp_path, runner=lambda c: 0) == 0
    assert not script.exists() and not desktop.exists()
    assert ph.registered_port(platform="linux", home=tmp_path) is None


def test_linux_without_xdg_mime_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    with patch("app.utils.protocol_handler.shutil.which", return_value=None):
        assert ph.register(7001, platform="linux", home=tmp_path, runner=lambda c: 0) == 1
    assert not ph.linux_paths(tmp_path)[0].exists()


def test_unsupported_platform(tmp_path):
    assert ph.register(7001, platform="freebsd13", home=tmp_path, runner=lambda c: 0) == 1


def test_default_port_follows_ziya_port(monkeypatch):
    monkeypatch.setenv("ZIYA_PORT", "7777")
    assert ph.default_port() == 7777
    monkeypatch.delenv("ZIYA_PORT")
    assert ph.default_port() == 6969


# ── seams: installer and registry ───────────────────────────────────────────

def test_installer_routes_links_to_the_handler_not_the_script():
    from app.utils import install_extras as ie
    with patch("app.utils.protocol_handler.register", return_value=0) as reg, \
         patch.object(ie, "_run_script", side_effect=AssertionError("script must not run")), \
         patch.object(ie, "_record_installed") as rec:
        assert ie.run(["--links", "--port", "7001"]) == 0
    reg.assert_called_once_with(7001, dry_run=False)
    rec.assert_called_once_with(["--links"])

    with patch("app.utils.protocol_handler.register", return_value=0) as reg, \
         patch.object(ie, "_run_script", side_effect=AssertionError("script must not run")), \
         patch.object(ie, "_record_installed") as rec:
        assert ie.run(["--links", "--dry-run"]) == 0
    reg.assert_called_once_with(None, dry_run=True)
    rec.assert_not_called()


def test_installer_combines_links_with_other_targets():
    """``--links --data``: the handler registers, and the pip path still runs
    with --links stripped from what it sees."""
    from app.utils import install_extras as ie
    with patch("app.utils.protocol_handler.register", return_value=0), \
         patch.object(ie, "_record_installed"), \
         patch.object(ie, "install_python_extras", return_value=0) as pip, \
         patch.object(ie, "_run_script", side_effect=AssertionError("script must not run")):
        assert ie.run(["--links", "--port=7001", "--data"]) == 0
    pip.assert_called_once_with(["--data"], dry_run=False)


def test_links_is_not_part_of_all_and_is_known_to_the_registry():
    from app.utils import install_extras as ie
    from app.utils.extras_registry import BY_KEY, install_command
    x = BY_KEY["links"]
    assert x.kind == "handler" and x.target == "--links"
    assert x.default_in_all is False and x.auto_refresh is False
    assert "--links" not in ie.SCRIPT_TARGETS
    assert "--links" not in ie._script_targets_requested(["--all"])
    assert "--links" not in ie.default_python_extras()
    cmd = install_command(x)
    assert cmd[1:4] == ["-m", "app.utils.protocol_handler", "register"]
    with patch("app.utils.protocol_handler.registered_port", return_value=None):
        assert x.missing()
    with patch("app.utils.protocol_handler.registered_port", return_value=6969):
        assert x.missing() is None
