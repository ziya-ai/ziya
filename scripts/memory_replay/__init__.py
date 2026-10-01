"""Longitudinal memory replay: re-run the memory system over the real
conversation history, in timestamp order, into sandbox stores.

See design/memory-replay.md.  Modules:
  timeline  -- build the event timeline from ~/.ziya/projects/*/chats (refs only)
"""
