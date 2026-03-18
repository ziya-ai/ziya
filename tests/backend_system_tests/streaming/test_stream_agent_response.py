"""
Test placeholder for stream_agent_response.

The original stream_agent_response function was removed from server.py.
Streaming is now handled through the middleware/streaming.py pipeline.
This file is kept as a placeholder to document the removal.
"""

import pytest


@pytest.mark.skip(reason="stream_agent_response removed from server.py; streaming handled via middleware")
def test_placeholder():
    """Placeholder — stream_agent_response no longer exists."""
    pass
