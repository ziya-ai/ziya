"""
Regression tests for ASR F-027: the headless diagram-rendering Chromium must
run WITH its sandbox by default. --no-sandbox is an explicit, env-gated opt-in
only, because the renderer processes attacker-influenced SVG/HTML from model
output and the sandbox is the primary renderer-escape defense.

Also covers D-045 / D-237: the software-WebGL (ANGLE + SwiftShader) launch
args used for plotly 3D / parcoords traces, and the requirement that
``--disable-gpu`` is NOT present -- it would disable the GPU process outright
and neutralise the SwiftShader path, so WebGL-only traces would still fall
back to the grey "WebGL is not supported" panel.
"""

import unittest

from app.services.diagram_renderer import build_chromium_launch_args


class TestChromiumLaunchArgs(unittest.TestCase):
    def test_sandbox_on_by_default(self):
        args = build_chromium_launch_args()
        self.assertNotIn("--no-sandbox", args)

    def test_sandbox_on_when_explicitly_false(self):
        args = build_chromium_launch_args(no_sandbox=False)
        self.assertNotIn("--no-sandbox", args)

    def test_no_sandbox_only_when_opted_in(self):
        args = build_chromium_launch_args(no_sandbox=True)
        self.assertIn("--no-sandbox", args)

    def test_baseline_args_always_present(self):
        # --disable-dev-shm-usage is a safe container baseline (avoids /dev/shm
        # exhaustion) and does not affect GPU/WebGL.
        for kwargs in ({}, {"no_sandbox": True}):
            self.assertIn("--disable-dev-shm-usage", build_chromium_launch_args(**kwargs))

    def test_disable_gpu_absent_so_software_webgl_works(self):
        # D-045: --disable-gpu disables the GPU process entirely, which also
        # kills the software (SwiftShader) WebGL context. With it present the
        # D-237 ANGLE/SwiftShader flags are inert and plotly WebGL-only traces
        # (surface / scatter3d / mesh3d / volume / cone / streamtube /
        # parcoords) render only the grey "WebGL is not supported" panel.
        # It must NOT be in the args in either sandbox mode.
        for kwargs in ({}, {"no_sandbox": True}):
            self.assertNotIn("--disable-gpu", build_chromium_launch_args(**kwargs))

    def test_default_matches_env_default(self):
        # The env var defaults to False (sandbox on); the builder default must
        # agree so the env-driven path and the bare call can't diverge.
        from app.config.env_registry import ziya_env, REGISTRY
        self.assertIn("ZIYA_CHROMIUM_NO_SANDBOX", REGISTRY)
        self.assertFalse(REGISTRY["ZIYA_CHROMIUM_NO_SANDBOX"].default)

    def test_software_webgl_flags_present(self):
        # D-237: WebGL-only plotly trace families (surface / scatter3d /
        # parcoords) need a software WebGL context or they render only a grey
        # "WebGL is not supported" panel -> total data loss. ANGLE+SwiftShader
        # supplies one. These flags must be present in BOTH sandbox modes.
        for kwargs in ({}, {"no_sandbox": True}):
            args = build_chromium_launch_args(**kwargs)
            self.assertIn("--use-gl=angle", args)
            self.assertIn("--use-angle=swiftshader", args)
            self.assertIn("--enable-unsafe-swiftshader", args)

    def test_webgl_survives_gpu_process_crashes(self):
        # D-237 follow-up: SwiftShader is only the GPU process's FIRST life.
        # Chromium's default crash fallback relaunches the GPU process with
        # --use-gl=disabled after the third crash (permanent for the browser
        # lifetime), and its per-origin 3D-API blocklist hides WebGL after
        # repeated context losses. A long-lived server hit both: day-old
        # renderers showed only the grey "WebGL is not supported" panel.
        # Both valves must be off in BOTH sandbox modes. The behavioural
        # half of this invariant is tests/test_headless_webgl_survives_gpu_crash.py.
        for kwargs in ({}, {"no_sandbox": True}):
            args = build_chromium_launch_args(**kwargs)
            self.assertIn("--disable-gpu-process-crash-limit", args)
            self.assertIn("--disable-domain-blocking-for-3d-apis", args)

    def test_software_webgl_flags_not_neutralised_by_disable_gpu(self):
        # The combined invariant that is the actual D-045 fix: the SwiftShader
        # flags are only effective when --disable-gpu is absent. Assert both
        # halves together so a future re-introduction of --disable-gpu (which
        # would silently break WebGL again) fails this test.
        for kwargs in ({}, {"no_sandbox": True}):
            args = build_chromium_launch_args(**kwargs)
            self.assertIn("--use-angle=swiftshader", args)
            self.assertNotIn("--disable-gpu", args)


if __name__ == "__main__":
    unittest.main()
