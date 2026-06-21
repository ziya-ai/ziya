"""
Regression tests for ASR F-005: Content-Security-Policy production toggle.

The default 'relaxed' CSP preserves the historical behaviour (unsafe-inline,
unsafe-eval, whole jsdelivr origin) so Mermaid/Vega CDN diagrams render.
'strict' mode drops unsafe-eval and pins jsdelivr to specific script paths.
"""

import unittest

from app.middleware.security_headers import build_csp


class TestBuildCsp(unittest.TestCase):
    def test_relaxed_is_default_and_permissive(self):
        csp = build_csp("relaxed")
        self.assertIn("'unsafe-eval'", csp)
        self.assertIn("'unsafe-inline'", csp)
        self.assertIn("https://cdn.jsdelivr.net;", csp)  # whole origin

    def test_unknown_mode_falls_back_to_relaxed(self):
        # Anything that isn't "strict" must behave like relaxed (fail-open
        # on availability, not on security — relaxed is the prior default).
        self.assertEqual(build_csp("banana"), build_csp("relaxed"))

    def test_strict_drops_unsafe_eval(self):
        csp = build_csp("strict")
        self.assertNotIn("'unsafe-eval'", csp)

    def test_strict_pins_jsdelivr_paths(self):
        csp = build_csp("strict")
        # The bare origin must NOT be allowlisted in strict mode...
        self.assertNotIn("cdn.jsdelivr.net;", csp)
        self.assertNotIn("'unsafe-eval' https://cdn.jsdelivr.net", csp)
        # ...but the specific script paths the UI loads must be present.
        self.assertIn("cdn.jsdelivr.net/npm/mermaid@10", csp)
        self.assertIn("cdn.jsdelivr.net/npm/vega-embed@6", csp)
        self.assertIn("cdn.jsdelivr.net/npm/marked/marked.min.js", csp)

    def test_strict_retains_script_unsafe_inline(self):
        # CRA inlines its runtime chunk as an inline <script>; removing
        # unsafe-inline requires a nonce build step (out of scope here).
        self.assertIn("'unsafe-inline'", build_csp("strict"))

    def test_invariant_directives_present_in_both_modes(self):
        for mode in ("relaxed", "strict"):
            csp = build_csp(mode)
            self.assertIn("default-src 'self'", csp)
            self.assertIn("object-src 'none'", csp)
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertIn("base-uri 'self'", csp)
            self.assertIn("form-action 'self'", csp)
            self.assertIn("style-src 'self' 'unsafe-inline'", csp)


if __name__ == "__main__":
    unittest.main()
