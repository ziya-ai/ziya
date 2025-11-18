"""
Callback handlers for LLM interactions.
"""
from langchain_classic.callbacks.base import BaseCallbackHandler


class EmptyMessageFilter(BaseCallbackHandler):
    """Filter for empty messages in Google models."""
    def on_llm_start(self, *args, **kwargs):
        pass
    
    def on_llm_end(self, *args, **kwargs):
        pass
    
    def on_llm_error(self, *args, **kwargs):
        pass
