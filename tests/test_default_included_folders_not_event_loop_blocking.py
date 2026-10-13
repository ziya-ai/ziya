"""GET /api/default-included-folders must not walk the project tree on the
asyncio event loop.

Attribution (SIGUSR1 faulthandler dump of a live server, 2026-10-09, after
py-spy proved unusable): the "Current thread" -- the event loop -- was inside
``os.walk`` under ``collect_documentation_file_keys`` under
``get_default_included_folders``, reached directly from FastAPI's
``run_endpoint_function``.  The handler is ``async def`` with no await, so the
whole walk ran inline on the loop; the walk skips only dot-directories, so on a
large tree (or one with a vendored ``node_modules``) it is seconds to minutes
during which NO request is served.  It fires on load and on every project
switch, which is why the config modals could not open while the file browser
was "syncing".  Same defect class as the embedding backfill fixed the same
night, and the one the chats.py docstring describes.

Two assertions, both of which fail on the unpatched handler:

  1. A ticker coroutine keeps ticking while the (stubbed, slow) walk runs.
  2. The handler source contains at least one ``await`` -- the structural
     property the first assertion depends on, pinned so a later edit that
     drops the offload fails even when the stub is too fast to starve a loop.
"""
import ast
import asyncio
import inspect
import time

import pytest

from app.routes import folder_routes as fr


@pytest.mark.asyncio
async def test_default_included_folders_keeps_loop_responsive(monkeypatch, tmp_path):
    calls = []

    def slow_walk(root, **kwargs):
        calls.append((root, kwargs))
        time.sleep(0.3)           # stands in for os.walk over a large tree
        return ["README.md", "sub/AGENTS.md"]

    monkeypatch.setattr(fr, "_collect_documentation_file_keys", slow_walk)

    ticks = 0
    stop = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    t = asyncio.create_task(ticker())
    result = await fr.get_default_included_folders(project_path=str(tmp_path))
    stop.set()
    await t

    # Positive: the walk ran, against the requested root, and its keys came back.
    assert len(calls) == 1
    assert calls[0][0] == str(tmp_path)
    assert calls[0][1]["readme_root_only"] is True
    assert result == {"defaultIncludedFolders": ["README.md", "sub/AGENTS.md"]}
    # The property under test: a 300 ms synchronous walk must not park the loop.
    # Inline on the loop the ticker runs ~once; offloaded it runs ~30 times.
    assert ticks >= 10, f"event loop starved during documentation walk: {ticks} ticks"


def test_default_included_folders_handler_awaits_its_walk():
    src = inspect.getsource(fr.get_default_included_folders)
    fn = ast.parse(src).body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    awaits = [n for n in ast.walk(fn) if isinstance(n, ast.Await)]
    assert awaits, "handler is a zero-await async def: its body runs on the event loop"
