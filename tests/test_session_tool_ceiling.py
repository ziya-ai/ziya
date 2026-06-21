"""
Regression tests for ASR F-010: per-turn tool-call ceiling (circuit breaker).

Bounds the number of tool invocations within a single turn (streaming
response), stopping a prompt-injection / hallucination loop from chaining
tools at machine speed. The counter resets at the start of every turn, so it
never locks a long-running conversation out of tools.
"""

import os
import unittest
from unittest.mock import patch

from app.mcp.manager import MCPManager


def _mgr():
    # Construct without running async initialize(); we only exercise the
    # synchronous counter helpers.
    m = MCPManager.__new__(MCPManager)
    m._turn_tool_counts = {}
    return m


class TestTurnCeiling(unittest.TestCase):
    def setUp(self):
        self.m = _mgr()

    def test_under_ceiling_allowed(self):
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "5"}):
            for _ in range(5):
                self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))

    def test_exceeding_ceiling_blocked(self):
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "3"}):
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))  # 1
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))  # 2
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))  # 3
            self.assertTrue(self.m._exceeds_turn_ceiling("conv-a"))   # 4 > 3

    def test_per_conversation_isolation(self):
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "2"}):
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))  # a=1
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))  # a=2
            self.assertTrue(self.m._exceeds_turn_ceiling("conv-a"))   # a=3 > 2
            # conv-b has its own independent budget
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-b"))  # b=1
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-b"))  # b=2

    def test_zero_disables_breaker(self):
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "0"}):
            for _ in range(1000):
                self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))

    def test_none_conversation_id_uses_default_bucket(self):
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "1"}):
            self.assertFalse(self.m._exceeds_turn_ceiling(None))  # default=1
            self.assertTrue(self.m._exceeds_turn_ceiling(None))   # default=2 > 1

    def test_reset_clears_counter_each_turn(self):
        # The reset is the heart of the per-turn fix: a conversation that hit
        # the ceiling last turn gets a FRESH budget this turn — never a
        # permanent lockout.
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "2"}):
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))
            self.assertTrue(self.m._exceeds_turn_ceiling("conv-a"))
            self.m.reset_turn_tool_count("conv-a")  # next turn starts
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))
            self.assertFalse(self.m._exceeds_turn_ceiling("conv-a"))

    def test_long_conversation_never_locked_out(self):
        # Simulate many turns, each doing real tool work just under the limit.
        # With per-turn reset the conversation works indefinitely; a lifetime
        # counter would have locked it out. This is the regression guard for
        # the session-cumulative bug.
        with patch.dict(os.environ, {"ZIYA_MAX_TOOLS_PER_TURN": "3"}):
            for _turn in range(50):
                self.m.reset_turn_tool_count("conv-long")
                for _ in range(3):  # 3 tools/turn, at the ceiling but not over
                    self.assertFalse(self.m._exceeds_turn_ceiling("conv-long"))

    def test_default_limit_is_1000(self):
        os.environ.pop("ZIYA_MAX_TOOLS_PER_TURN", None)
        self.assertEqual(self.m._turn_limit(), 1000)

    def test_registry_declares_var(self):
        from app.config.env_registry import REGISTRY
        self.assertIn("ZIYA_MAX_TOOLS_PER_TURN", REGISTRY)
        self.assertEqual(REGISTRY["ZIYA_MAX_TOOLS_PER_TURN"].default, 1000)


if __name__ == "__main__":
    unittest.main()
