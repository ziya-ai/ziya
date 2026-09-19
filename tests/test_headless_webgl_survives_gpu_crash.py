"""
Behavioural half of the D-237 follow-up in ``build_chromium_launch_args``.

Symptom: after a day or two of uptime the headless diagram renderer returned
the grey "WebGL is not supported by your browser" panel for every plotly
surface / scatter3d / parcoords render, silently.  ``ps`` showed the
browser's GPU child running with ``--use-gl=disabled`` -- Chromium's own
fallback once its GPU process has crashed three times.  A second Chromium
valve (the per-origin 3D-API blocklist) hides WebGL after repeated context
losses even while the GPU process is healthy.

The unit test in ``test_chromium_sandbox_args.py`` asserts the two flags
are in the arg list.  This module asserts the thing that actually matters:
a browser launched with our args still hands out a *working* WebGL context
(draws, reads back a pixel) after its GPU process has been SIGKILLed more
times than Chromium's crash limit.  It also runs the same kill loop against
the arg list with the two flags stripped and asserts WebGL is LOST -- so the
passing test is known to exercise the failure path rather than a kill loop
that never landed.

Needs a real Chromium (``playwright install chromium``); ~30s; no Ziya server.
"""

import asyncio
import os
import signal
import subprocess
import sys

import pytest

from app.services.diagram_renderer import build_chromium_launch_args

try:
    import playwright.async_api  # noqa: F401
    _HAVE_PLAYWRIGHT = True
except ImportError:
    _HAVE_PLAYWRIGHT = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _HAVE_PLAYWRIGHT, reason="playwright not installed"),
    pytest.mark.skipif(sys.platform == "win32", reason="uses ps/SIGKILL"),
    pytest.mark.timeout(180),
]

RESILIENCE_FLAGS = (
    "--disable-gpu-process-crash-limit",
    "--disable-domain-blocking-for-3d-apis",
)

# Chromium falls back to --use-gl=disabled after the 3rd GPU crash; go one past.
GPU_KILLS = 4

# A context that draws and reads back is what plotly's gl-plot3d needs.  A
# bare non-null getContext() is NOT enough: a lost context is still truthy.
WEBGL_PROBE = """() => {
  const c = document.createElement('canvas'); c.width = 4; c.height = 4;
  const gl = c.getContext('webgl');
  if (!gl) return 'no-context';
  if (gl.isContextLost()) return 'lost';
  gl.clearColor(1, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT);
  const px = new Uint8Array(4);
  gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return (px[0] === 255 && px[3] === 255) ? 'ok' : 'bad-pixel';
}"""


def _ps() -> list[tuple[int, int, str]]:
    out = subprocess.run(["ps", "-eo", "pid,ppid,args"], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) == 3:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def _browser_pid(before: set[int]) -> int:
    """The Chromium main process our launch created: a chrome(-headless-shell)
    with --remote-debugging-pipe and no --type=, absent from ``before``."""
    for pid, _ppid, args in _ps():
        if pid in before:
            continue
        if "--remote-debugging-pipe" in args and "--type=" not in args and (
            "chrome-headless-shell" in args or "Chromium" in args or "chrome" in args
        ):
            return pid
    raise RuntimeError("could not locate launched Chromium main process")


def _gpu_children(browser_pid: int) -> list[tuple[int, str]]:
    return [(pid, args) for pid, ppid, args in _ps()
            if ppid == browser_pid and "--type=gpu-process" in args]


async def _probe(browser) -> str:
    page = await browser.new_page()
    try:
        await page.set_content("<html><body></body></html>")
        return await page.evaluate(WEBGL_PROBE)
    finally:
        await page.close()


async def _kill_gpu_then_probe(args: list[str]) -> tuple[str, list[str], str]:
    """Launch with ``args``; return (initial probe, probe after each kill,
    final GPU-process argv) so a failure message says which valve tripped."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        before = {pid for pid, _, _ in _ps()}
        browser = await p.chromium.launch(headless=True, args=args)
        try:
            bpid = _browser_pid(before)
            initial = await _probe(browser)
            after: list[str] = []
            for _ in range(GPU_KILLS):
                kids = _gpu_children(bpid)
                for pid, _a in kids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                # Let Chromium notice the crash and decide on its fallback.
                await asyncio.sleep(3.0)
                after.append(await _probe(browser))
            final_gpu = " ".join(a for _pid, a in _gpu_children(bpid))
            return initial, after, final_gpu
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_webgl_context_survives_repeated_gpu_process_crashes():
    args = build_chromium_launch_args()
    initial, after, final_gpu = await _kill_gpu_then_probe(args)

    # Positive half: the launch actually had SwiftShader WebGL to begin with,
    # otherwise "survives" is vacuous.
    assert initial == "ok", f"no software WebGL at launch: {initial!r}"
    # The invariant: every probe after every crash still draws.
    assert all(r == "ok" for r in after), (
        f"WebGL degraded after GPU crashes: {after}; "
        f"gpu-process argv now: {final_gpu[:300]!r}"
    )
    # And the GPU process did not take Chromium's --use-gl=disabled fallback.
    assert "--use-gl=disabled" not in final_gpu, final_gpu[:300]


@pytest.mark.asyncio
async def test_baseline_without_resilience_flags_loses_webgl():
    """Certifies that the kill loop reaches Chromium's fallback: with the two
    flags stripped (the pre-fix arg list) WebGL is lost.  If this ever starts
    PASSING WebGL, the kill loop is no longer exercising the path and the test
    above proves nothing."""
    args = [a for a in build_chromium_launch_args() if a not in RESILIENCE_FLAGS]
    initial, after, _final_gpu = await _kill_gpu_then_probe(args)

    assert initial == "ok", f"no software WebGL at launch: {initial!r}"
    assert after[-1] != "ok", (
        f"baseline unexpectedly kept WebGL after {GPU_KILLS} GPU kills: {after}; "
        "the crash simulation is not reaching Chromium's fallback"
    )
