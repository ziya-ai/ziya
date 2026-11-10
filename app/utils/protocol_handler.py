"""
Register ``ziya://`` as a URL scheme with the operating system.

Inside the page, a ``ziya://chat/<id>/<idx>`` link works because
ZiyaLinkNavigator intercepts the click.  Outside the page (a link pasted into
Slack, an exported markdown file, another app's preview) the browser has no
handler for the scheme and shows "unknown protocol".  A browser cannot be
taught the scheme either: ``navigator.registerProtocolHandler`` accepts only a
safelist plus ``web+`` schemes.  So the handler is registered with the OS,
and all it does is translate the link into the http form the SPA understands
and hand that to the default browser::

    ziya://chat/abc/12  ->  http://localhost:<port>/?open=ziya://chat/abc/12

``/`` already serves the SPA for any query string; ZiyaLinkNavigator reads
``?open=`` once at boot and follows it with the same navigation a click uses.
Nothing server-side changes.

The port is fixed at registration time (``ziya-install-extras --links
[--port N]``, default ``$ZIYA_PORT`` or 6969).  A ``ziya://`` link names no
server, so when several Ziya instances run the registered one is the target;
re-run the command to point the handler elsewhere.

Per platform (all user-level, no sudo, nothing outside the user's home):

  macOS    ``~/Applications/Ziya Link Handler.app`` compiled with osacompile
           from an AppleScript ``on open location`` handler (URL events are
           Apple Events, so a shell-script applet would never see the URL),
           ``CFBundleURLTypes`` added to its Info.plist, registered with
           ``lsregister -f``.
  Linux    ``~/.local/share/ziya/ziya-link-handler`` (sh) plus a ``.desktop``
           entry declaring ``x-scheme-handler/ziya``, made default with
           ``xdg-mime``.
  Windows  ``HKCU\\Software\\Classes\\ziya`` with a ``shell\\open\\command``
           that runs ``rundll32 url.dll,FileProtocolHandler``.

``build_*`` are the pure functions (what gets written) and are what the tests
pin; ``register`` / ``unregister`` / ``status`` do the filesystem and
subprocess work.  Also runnable as ``python -m app.utils.protocol_handler
register|unregister|status [--port N]`` (how the Settings page's installer
invokes it).
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .ziya_links import SCHEME, parse_ziya_link

SCHEME_NAME = SCHEME.rstrip(":/")          # "ziya"
OPEN_PARAM = "open"
BUNDLE_ID = "ai.ziya.link-handler"
APP_NAME = "Ziya Link Handler"
DESKTOP_ID = "ziya-link-handler.desktop"
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
              "LaunchServices.framework/Support/lsregister")


def default_port() -> int:
    """The port a registration targets when none is given: ``$ZIYA_PORT``,
    else Ziya's default.  Read through the env registry so an unset or
    unparsable value falls back the same way the server does."""
    try:
        from app.config.env_registry import ziya_env
        return int(ziya_env("ZIYA_PORT"))
    except Exception:  # noqa: BLE001 -- the registry is optional here
        try:
            return int(os.environ.get("ZIYA_PORT") or 6969)
        except ValueError:
            return 6969


def deep_link_url(href: str, port: int) -> Optional[str]:
    """The http URL the handler opens for a ``ziya://`` href, or None when the
    href is not a well-formed link (nothing is opened for those)."""
    if parse_ziya_link(href) is None:
        return None
    return f"http://localhost:{int(port)}/?{OPEN_PARAM}={href.strip()}"


def _prefix(port: int) -> str:
    return f"http://localhost:{int(port)}/?{OPEN_PARAM}="


# ── what gets written, per platform ────────────────────────────────────────

def build_applescript(port: int) -> str:
    """The ``open location`` handler.  ``quoted form of`` makes the URL a
    single shell word, so a crafted link cannot break out of the command;
    the SPA then rejects anything that does not parse as a ziya:// link."""
    return (
        'on open location theURL\n'
        f'\tif theURL starts with "{SCHEME}" then\n'
        f'\t\tdo shell script "/usr/bin/open " & quoted form of ("{_prefix(port)}" & theURL)\n'
        '\tend if\n'
        'end open location\n'
    )


def plist_additions(port: int) -> dict:
    """Keys merged into the osacompile-generated Info.plist."""
    return {
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": APP_NAME,
        # No Dock icon, no menu bar: it exists only to relay the URL.
        "LSUIElement": True,
        "CFBundleURLTypes": [{
            "CFBundleURLName": "Ziya link",
            "CFBundleURLSchemes": [SCHEME_NAME],
        }],
        # Recorded so ``status`` can report which port the handler targets.
        "ZiyaPort": int(port),
    }


def build_linux_script(port: int) -> str:
    return (
        "#!/bin/sh\n"
        "# Opens a ziya:// link in the Ziya tab (ziya-install-extras --links).\n"
        'case "$1" in\n'
        f'  {SCHEME}*) exec xdg-open "{_prefix(port)}$1" ;;\n'
        "esac\n"
    )


def build_desktop_entry(script_path: Path) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={script_path} %u\n"
        "NoDisplay=true\n"
        "Terminal=false\n"
        f"MimeType=x-scheme-handler/{SCHEME_NAME};\n"
    )


def windows_command(port: int) -> str:
    """``%1`` is the full ``ziya://`` URL Windows passes to the handler."""
    return f'rundll32.exe url.dll,FileProtocolHandler "{_prefix(port)}%1"'


# ── locations ──────────────────────────────────────────────────────────────

def macos_app_path(home: Optional[Path] = None) -> Path:
    return (home or Path.home()) / "Applications" / f"{APP_NAME}.app"


def linux_paths(home: Optional[Path] = None) -> Tuple[Path, Path]:
    """(handler script, .desktop file) under XDG_DATA_HOME."""
    home = home or Path.home()
    data = Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share"))
    return data / "ziya" / "ziya-link-handler", data / "applications" / DESKTOP_ID


# ── register / unregister / status ─────────────────────────────────────────

Runner = Callable[[List[str]], int]


def _run(cmd: List[str]) -> int:
    try:
        return subprocess.run(cmd, check=False, capture_output=True).returncode
    except OSError:
        return 127


def _say(lines: List[str], msg: str) -> None:
    lines.append(msg)
    print(msg, flush=True)


def register(port: Optional[int] = None, dry_run: bool = False,
             platform: Optional[str] = None, home: Optional[Path] = None,
             runner: Runner = _run) -> int:
    """Register the handler for this user.  Returns a process exit code."""
    port = int(port if port is not None else default_port())
    platform = platform or sys.platform
    out: List[str] = []
    if platform == "darwin":
        app = macos_app_path(home)
        _say(out, f"==> ziya:// links: {app} -> {_prefix(port)}<link>")
        if dry_run:
            return 0
        if not shutil.which("osacompile") and not Path("/usr/bin/osacompile").exists():
            _say(out, "!! osacompile not found; cannot build the handler app.")
            return 1
        app.parent.mkdir(parents=True, exist_ok=True)
        if app.exists():
            shutil.rmtree(app)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "handler.applescript"
            src.write_text(build_applescript(port), encoding="utf-8")
            rc = runner(["/usr/bin/osacompile", "-o", str(app), str(src)])
        if rc != 0 or not app.is_dir():
            _say(out, f"!! osacompile failed (exit {rc}).")
            return rc or 1
        plist = app / "Contents" / "Info.plist"
        with plist.open("rb") as f:
            info = plistlib.load(f)
        info.update(plist_additions(port))
        with plist.open("wb") as f:
            plistlib.dump(info, f)
        rc = runner([LSREGISTER, "-f", str(app)])
        if rc != 0:
            _say(out, f"!! lsregister exited {rc}; the app is built but may not be "
                      "registered until the next login.")
            return rc
        _say(out, "ok ziya:// links now open in the browser tab at "
                  f"http://localhost:{port}/")
        return 0
    if platform.startswith("linux"):
        script, desktop = linux_paths(home)
        _say(out, f"==> ziya:// links: {desktop} -> {_prefix(port)}<link>")
        if dry_run:
            return 0
        if not shutil.which("xdg-mime"):
            _say(out, "!! xdg-mime not found (install xdg-utils); cannot register the scheme.")
            return 1
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(build_linux_script(port), encoding="utf-8")
        script.chmod(0o755)
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(build_desktop_entry(script), encoding="utf-8")
        rc = runner(["xdg-mime", "default", DESKTOP_ID, f"x-scheme-handler/{SCHEME_NAME}"])
        if rc != 0:
            _say(out, f"!! xdg-mime exited {rc}.")
            return rc
        if shutil.which("update-desktop-database"):
            runner(["update-desktop-database", str(desktop.parent)])
        _say(out, "ok ziya:// links now open in the browser tab at "
                  f"http://localhost:{port}/")
        return 0
    if platform == "win32":
        _say(out, f"==> ziya:// links: HKCU\\Software\\Classes\\{SCHEME_NAME} -> {_prefix(port)}<link>")
        if dry_run:
            return 0
        import winreg  # type: ignore[import-not-found]
        root = winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"Software\\Classes\\{SCHEME_NAME}")
        winreg.SetValueEx(root, None, 0, winreg.REG_SZ, "URL:Ziya link")
        winreg.SetValueEx(root, "URL Protocol", 0, winreg.REG_SZ, "")
        winreg.SetValueEx(root, "ZiyaPort", 0, winreg.REG_SZ, str(port))
        cmd = winreg.CreateKey(root, "shell\\open\\command")
        winreg.SetValueEx(cmd, None, 0, winreg.REG_SZ, windows_command(port))
        _say(out, "ok ziya:// links now open in the browser tab at "
                  f"http://localhost:{port}/")
        return 0
    _say(out, f"!! ziya:// link registration is not supported on {platform}.")
    return 1


def unregister(platform: Optional[str] = None, home: Optional[Path] = None,
               runner: Runner = _run) -> int:
    platform = platform or sys.platform
    if platform == "darwin":
        app = macos_app_path(home)
        if app.exists():
            runner([LSREGISTER, "-u", str(app)])
            shutil.rmtree(app, ignore_errors=True)
        print("ok ziya:// handler removed")
        return 0
    if platform.startswith("linux"):
        script, desktop = linux_paths(home)
        for p in (script, desktop):
            if p.exists():
                p.unlink()
        if shutil.which("update-desktop-database"):
            runner(["update-desktop-database", str(desktop.parent)])
        print("ok ziya:// handler removed")
        return 0
    if platform == "win32":
        import winreg  # type: ignore[import-not-found]
        for sub in (f"Software\\Classes\\{SCHEME_NAME}\\shell\\open\\command",
                    f"Software\\Classes\\{SCHEME_NAME}\\shell\\open",
                    f"Software\\Classes\\{SCHEME_NAME}\\shell",
                    f"Software\\Classes\\{SCHEME_NAME}"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
        print("ok ziya:// handler removed")
        return 0
    return 1


def registered_port(platform: Optional[str] = None,
                    home: Optional[Path] = None) -> Optional[int]:
    """The port the installed handler targets; None when none is installed."""
    platform = platform or sys.platform
    try:
        if platform == "darwin":
            plist = macos_app_path(home) / "Contents" / "Info.plist"
            if not plist.is_file():
                return None
            with plist.open("rb") as f:
                info = plistlib.load(f)
            schemes = [s for t in info.get("CFBundleURLTypes", [])
                       for s in t.get("CFBundleURLSchemes", [])]
            return int(info.get("ZiyaPort", 0)) if SCHEME_NAME in schemes else None
        if platform.startswith("linux"):
            script, desktop = linux_paths(home)
            if not (script.is_file() and desktop.is_file()):
                return None
            for tok in script.read_text(encoding="utf-8").split('"'):
                if tok.startswith("http://localhost:"):
                    return int(tok[len("http://localhost:"):].split("/")[0])
            return None
        if platform == "win32":
            import winreg  # type: ignore[import-not-found]
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                f"Software\\Classes\\{SCHEME_NAME}") as k:
                return int(winreg.QueryValueEx(k, "ZiyaPort")[0])
    except (OSError, ValueError):
        return None
    return None


def status() -> Optional[str]:
    """extras_registry probe: None when installed, else what is missing."""
    if sys.platform not in ("darwin", "win32") and not sys.platform.startswith("linux"):
        return "not supported on this platform"
    return None if registered_port() is not None else "ziya:// scheme not registered with the OS"


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Register ziya:// links with the OS.")
    ap.add_argument("action", choices=("register", "unregister", "status"))
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "register":
        return register(a.port, dry_run=a.dry_run)
    if a.action == "unregister":
        return unregister()
    port = registered_port()
    print(f"registered -> http://localhost:{port}/" if port else "not registered")
    return 0 if port else 1


if __name__ == "__main__":
    sys.exit(main())
