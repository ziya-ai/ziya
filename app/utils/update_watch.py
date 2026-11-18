"""
Notice a newer installed Ziya and restart into it when nothing is running.

Reported 2026-11-11: an upgrade left a running server on the old version,
and the first sign of it was a failed response.  The toolbox launcher now
keeps each version's venv where toolbox does not delete it, and pip/pipx
upgrades no longer replace files under a running ziya
(app/utils/install_lock.py), so the old server keeps working.  Nothing
told the user a newer version was waiting, though; that is this module.

``tick`` asks the registered UpdateProviders (app/plugins) whether a newer
version is installed, falling back to this environment's installed
distribution version, and prepares it (``UpdateProvider.prepare``) so the
restart itself is quick.  The header chip reads ``status`` through
/api/update/* (app/routes/update_routes.py).

A restart happens only when nothing runs in this process -- no chat turn,
no Task Card run, no delegate plan -- and either the user asked for one, or
auto-restart is on and nothing has run for AUTO_QUIET_SECONDS.  It is a
graceful uvicorn shutdown (lifespan shutdown runs: MCP servers stop,
delegate state is saved) followed by ``exec_if_pending`` in
app.main.start_server once ``Server.run`` returns, so the exec happens on
the main thread with the listening socket closed.  The new process binds
the same port; tabs see ``boot_id`` change.

Managed jobs (``job_start``) are not counted: they run in their own
session and the exec does not stop them.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logging_utils import logger

POLL_SECONDS = 60.0
FAST_POLL_SECONDS = 3.0
AUTO_QUIET_SECONDS = 120.0
SHUTDOWN_GRACE_SECONDS = 5
_PREFS_FILE = "update_prefs.json"
# Set by start_server for its own process after its checks; the restarted
# process must run those checks again rather than inherit "done".
_PROCESS_MARKERS = ("ZIYA_AUTH_CHECKED", "ZIYA_PARENT_AUTH_COMPLETE", "ZIYA_SKIP_INIT")

#: Different on every start; a tab compares it to tell a restart happened.
BOOT_ID = uuid.uuid4().hex

_server: Any = None              # uvicorn.Server, from start_server
_pending: Any = None             # UpdateInfo to exec once the server stops
_info: Any = None
_provider: Any = None
_fallback: Any = None
_preparing = False
_prepare_failed: Optional[str] = None
_restart_requested = False
_last_busy = time.monotonic()
_prefs: Optional[Dict[str, bool]] = None
_wake: Optional[asyncio.Event] = None


class _SameEnvironmentProvider:
    """pip / pipx / editable installs: this environment's files were replaced
    in place, so the installed distribution version no longer matches the
    one this process started with.  The restart is the same interpreter and
    command line, which then loads the new files."""

    provider_id = "same-environment"
    _DISTS = ("amzn-ziya", "ziya")

    def __init__(self) -> None:
        self._started = self._versions()

    def _versions(self) -> Dict[str, str]:
        from importlib.metadata import PackageNotFoundError, version
        out: Dict[str, str] = {}
        for dist in self._DISTS:
            try:
                out[dist] = str(version(dist))
            except PackageNotFoundError:
                pass
        return out

    def check(self):
        from app.plugins.interfaces import UpdateInfo
        from app.utils import install_lock
        now = self._versions()
        for dist in self._DISTS:
            was, is_ = self._started.get(dist), now.get(dist)
            if was and is_ and is_ != was:
                return UpdateInfo(installed_version=is_, running_version=was,
                                  exec_path=sys.executable,
                                  argv=install_lock.reexec_argv())
        return None

    def prepare(self, info) -> bool:
        return True


# -- preferences --------------------------------------------------------------

def _prefs_path():
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / _PREFS_FILE


def get_prefs() -> Dict[str, bool]:
    global _prefs
    if _prefs is None:
        try:
            raw = json.loads(_prefs_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        _prefs = {"auto_restart": bool(raw.get("auto_restart", False))}
    return dict(_prefs)


def set_prefs(auto_restart: bool) -> Dict[str, bool]:
    global _prefs
    _prefs = {"auto_restart": bool(auto_restart)}
    path = _prefs_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(_prefs), encoding="utf-8")
    os.replace(tmp, path)
    _kick()
    return dict(_prefs)


# -- what is running ----------------------------------------------------------

def busy_items() -> List[Dict[str, str]]:
    """Work a restart would stop: running chat turns, Task Card runs and
    delegate plans in this process."""
    items: List[Dict[str, str]] = []
    try:
        from app.agents.chat_turn_relay import active_conversation_ids
        items += [{"kind": "chat", "id": c} for c in active_conversation_ids()]
    except (ImportError, AttributeError) as e:
        logger.debug(f"update watch: chat turns: {e!r}")
    try:
        from app.agents.live_runs import live_run_ids
        items += [{"kind": "task_run", "id": r} for r in live_run_ids()]
    except (ImportError, AttributeError) as e:
        logger.debug(f"update watch: task runs: {e!r}")
    try:
        # Same private read as the lifespan shutdown's delegate persistence.
        from app.agents import delegate_manager as _dm
        for mgr in list(_dm._instances.values()):
            for plan_id, running in list(mgr._running.items()):
                if running:
                    items.append({"kind": "delegates", "id": plan_id})
    except (ImportError, AttributeError) as e:
        logger.debug(f"update watch: delegates: {e!r}")
    return items


# -- finding an update --------------------------------------------------------

def _providers() -> List[Any]:
    global _fallback
    try:
        from app.plugins import get_update_providers
        registered = get_update_providers()
    except ImportError:
        registered = []
    if _fallback is None:
        _fallback = _SameEnvironmentProvider()
    return registered + [_fallback]


def find_update() -> Tuple[Any, Any]:
    """(provider, UpdateInfo) from the first provider that reports one."""
    for provider in _providers():
        try:
            info = provider.check()
        except Exception as e:  # noqa: BLE001 -- one broken provider must not hide the rest
            logger.debug(f"update provider {getattr(provider, 'provider_id', '?')}: {e!r}")
            continue
        if info is not None:
            return provider, info
    return None, None


def _running_version() -> Optional[str]:
    try:
        from app.utils import version_util
        return str(version_util.get_current_version())
    except Exception:  # noqa: BLE001 -- display only
        return None


def status() -> Dict[str, Any]:
    info = _info
    return {
        "boot_id": BOOT_ID,
        "running_version": info.running_version if info is not None else _running_version(),
        "update": None if info is None else {
            "version": info.installed_version,
            "ready": bool(info.ready),
            "notes_url": info.notes_url,
        },
        "preparing": _preparing,
        "prepare_failed": info is not None and _prepare_failed == info.installed_version,
        "busy": busy_items(),
        "restart_requested": _restart_requested,
        "restarting": _pending is not None,
        "auto_restart": get_prefs()["auto_restart"],
        "can_restart": _server is not None,
    }


# -- restarting ---------------------------------------------------------------

def set_server(server: Any) -> None:
    global _server
    _server = server


def server_attached() -> bool:
    return _server is not None


def request_restart() -> None:
    global _restart_requested
    _restart_requested = True
    _kick()


def cancel_restart() -> None:
    global _restart_requested
    _restart_requested = False


def _kick() -> None:
    if _wake is not None:
        _wake.set()


def _due(busy: List[Dict[str, str]]) -> bool:
    info = _info
    if info is None or not info.ready or _pending is not None or _server is None or busy:
        return False
    if _restart_requested:
        return True
    return get_prefs()["auto_restart"] and time.monotonic() - _last_busy >= AUTO_QUIET_SECONDS


def _begin_restart(info: Any) -> None:
    global _pending
    _pending = info
    logger.info(f"🔄 Restarting into ziya {info.installed_version}: nothing is running")
    _server.config.timeout_graceful_shutdown = SHUTDOWN_GRACE_SECONDS
    _server.should_exit = True


def exec_if_pending() -> None:
    """Called by start_server after uvicorn returns.  Replaces this process
    with the new version when a restart was begun; otherwise returns."""
    info = _pending
    if info is None:
        return
    env = dict(os.environ)
    env.update(info.env)
    for key in _PROCESS_MARKERS:
        env.pop(key, None)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass
    try:
        os.execve(info.exec_path, info.argv, env)
    except OSError as e:
        logger.error(f"Could not restart into ziya {info.installed_version} ({e}); "
                     f"start ziya again to use it.")


# -- the loop -----------------------------------------------------------------

async def tick() -> None:
    """One poll: refresh what is installed, prepare it, restart if due."""
    global _info, _provider, _preparing, _prepare_failed, _restart_requested, _last_busy
    if _pending is not None:
        return
    _provider, _info = await asyncio.to_thread(find_update)
    if _info is None:
        _restart_requested = False
        return
    if not _info.ready and not _preparing and _prepare_failed != _info.installed_version:
        version = _info.installed_version
        _preparing = True
        try:
            ok = await asyncio.to_thread(_provider.prepare, _info)
        except Exception as e:  # noqa: BLE001 -- a provider bug must not stop the watcher
            logger.warning(f"Preparing ziya {version} failed: {e}")
            ok = False
        finally:
            _preparing = False
        if ok:
            _provider, _info = await asyncio.to_thread(find_update)
        if not ok or (_info is not None and not _info.ready):
            _prepare_failed = version
            return
        if _info is None:
            return
        logger.info(f"🔄 ziya {version} is ready to restart into")
    busy = busy_items()
    if busy:
        _last_busy = time.monotonic()
    if _due(busy):
        _begin_restart(_info)


def _interval() -> float:
    if _info is not None and (_restart_requested or get_prefs()["auto_restart"]):
        return FAST_POLL_SECONDS
    return POLL_SECONDS


async def _sleep(seconds: float) -> None:
    if _wake is None:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(_wake.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
    _wake.clear()


async def run_forever() -> None:
    """Background loop, started from the server lifespan."""
    global _wake
    _wake = asyncio.Event()
    _providers()  # snapshot the versions this server started with
    await _sleep(10.0)
    while _pending is None:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug(f"update watch: {e!r}")
        if _pending is None:
            await _sleep(_interval())


def _reset_for_tests() -> None:
    global _server, _pending, _info, _provider, _fallback, _preparing
    global _prepare_failed, _restart_requested, _last_busy, _prefs, _wake
    _server = _pending = _info = _provider = _fallback = None
    _preparing = False
    _prepare_failed = None
    _restart_requested = False
    _last_busy = time.monotonic()
    _prefs = None
    _wake = None
