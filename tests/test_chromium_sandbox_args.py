"""
Regression tests for ASR F-027: the headless diagram-rendering Chromium must
run WITH its sandbox by default. --no-sandbox is an explicit, env-gated opt-in
only, because the renderer processes attacker-influenced SVG/HTML from model
output and the sandbox is the primary renderer-escape defense.
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
        for flag in ("--disable-gpu", "--disable-dev-shm-usage"):
            self.assertIn(flag, build_chromium_launch_args())
            self.assertIn(flag, build_chromium_launch_args(no_sandbox=True))

    def test_default_matches_env_default(self):
        # The env var defaults to False (sandbox on); the builder default must
        # agree so the env-driven path and the bare call can't diverge.
        from app.config.env_registry import ziya_env, REGISTRY
        self.assertIn("ZIYA_CHROMIUM_NO_SANDBOX", REGISTRY)
        self.assertFalse(REGISTRY["ZIYA_CHROMIUM_NO_SANDBOX"].default)


if __name__ == "__main__":
    unittest.main()
