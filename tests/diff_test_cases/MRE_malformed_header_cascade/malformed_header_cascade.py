"""Module docstring for the test fixture."""
import asyncio
import logging

logger = logging.getLogger(__name__)


def helper_one():
    """A simple helper."""
    return 1


def helper_two():
    """Another simple helper."""
    return 2


def helper_three():
    """Yet another helper."""
    return 3


class StreamingExecutor:
    """Main executor class — mirrors the shape of the real-world failure."""

    def __init__(self, conversation_id=None):
        self.conversation_id = conversation_id
        self.results = []

    async def stream(self, messages, tools=None, conversation_id=None):
        # --- Concurrent feedback monitor ---
        # The real code has ~70 lines of closure logic here that Gemini
        # tried to extract into a class.  We'll represent it compactly.
        _pending_feedback = []
        _feedback_monitor_task = None

        async def _feedback_monitor(conv_id):
            """Background coroutine."""
            try:
                while True:
                    await asyncio.sleep(0.3)
                    if conv_id is None:
                        continue
                    # Imagine 40 more lines of queue-draining logic here
                    break
            except asyncio.CancelledError:
                pass

        def _drain_pending_feedback():
            if not _pending_feedback:
                return []
            drained = _pending_feedback.copy()
            _pending_feedback.clear()
            return drained

        # Start the monitor
        if conversation_id:
            _feedback_monitor_task = asyncio.create_task(_feedback_monitor(conversation_id))
            logger.debug(f"Started monitor for {conversation_id}")

        # Early guard
        if self.conversation_id is None:
            yield {'type': 'error'}
            yield {'type': 'stream_end'}
            if _feedback_monitor_task:
                _feedback_monitor_task.cancel()
            return

        # --- Main iteration loop ---
        for iteration in range(10):
            logger.debug(f"iter {iteration}")

            # Feedback check point 1
            if conversation_id and iteration > 0:
                for fb in _drain_pending_feedback():
                    if fb['type'] == 'interrupt':
                        yield {'type': 'text', 'content': 'stop'}
                        yield {'type': 'stream_end'}
                        if _feedback_monitor_task:
                            _feedback_monitor_task.cancel()
                        return
                    if 'halt' in fb.get('message', '').lower():
                        yield {'type': 'text', 'content': 'halting'}
                        yield {'type': 'stream_end'}
                        if _feedback_monitor_task:
                            _feedback_monitor_task.cancel()
                        return

            # Mid-stream processing
            event_count = 0
            async for event in self._inner_stream():
                event_count += 1

                # Feedback check point 2 — periodic mid-stream drain
                if event_count % 50 == 0:
                    for _fb in _drain_pending_feedback():
                        if _fb['type'] == 'interrupt':
                            yield {'type': 'text', 'content': 'stop'}
                            yield {'type': 'stream_end'}
                            if _feedback_monitor_task:
                                _feedback_monitor_task.cancel()
                            return
                        _msg = _fb.get('message', '')
                        if 'halt' in _msg.lower():
                            yield {'type': 'text', 'content': 'halting'}
                            yield {'type': 'stream_end'}
                            if _feedback_monitor_task:
                                _feedback_monitor_task.cancel()
                            return

                yield event

            # Tool execution point
            if self._needs_tools():
                result = await self._exec_tool(
                    drain_feedback_fn=_drain_pending_feedback,
                )
                if result.should_stop:
                    if _feedback_monitor_task:
                        _feedback_monitor_task.cancel()
                    return
                self.results.append(result)

            # Post-iteration feedback check
            if conversation_id:
                for fb in _drain_pending_feedback():
                    if fb['type'] == 'interrupt':
                        yield {'type': 'text', 'content': 'stop'}
                        yield {'type': 'stream_end'}
                        if _feedback_monitor_task:
                            _feedback_monitor_task.cancel()
                        return
                    if 'halt' in fb.get('message', '').lower():
                        yield {'type': 'text', 'content': 'halting'}
                        yield {'type': 'stream_end'}
                        if _feedback_monitor_task:
                            _feedback_monitor_task.cancel()
                        return

            # Check pending feedback before iteration end
            pending = [fb.get('message', '') for fb in _drain_pending_feedback()
                       if fb['type'] == 'feedback']
            if not pending:
                pending = [fb.get('message', '') for fb in _drain_pending_feedback()
                           if fb['type'] == 'feedback']

            # Cleanup point
            if conversation_id:
                if _feedback_monitor_task:
                    _feedback_monitor_task.cancel()
                    try:
                        await _feedback_monitor_task
                    except asyncio.CancelledError:
                        pass
                    _feedback_monitor_task = None

                post_cancel = [fb.get('message', '') for fb in _drain_pending_feedback()
                               if fb['type'] == 'feedback']

        # Final cleanup
        if _feedback_monitor_task and not _feedback_monitor_task.done():
            _feedback_monitor_task.cancel()

    async def _inner_stream(self):
        """Placeholder for the real streaming logic."""
        yield {'type': 'text', 'content': 'chunk'}

    def _needs_tools(self):
        return False

    async def _exec_tool(self, drain_feedback_fn):
        class R:
            should_stop = False
        return R()


def trailer_function():
    """Module-level function that comes after the class."""
    return "trailer"
