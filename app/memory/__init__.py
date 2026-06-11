"""
Structured memory subsystem.

Provides cross-session persistent memory: extraction, retrieval,
lifecycle management, and organization.

Public API — import from here rather than reaching into submodules:

    from app.memory import (
        run_post_conversation_extraction,
        get_memory_prompt_section,
        get_memory_activation_directive,
        apply_feedback,
        record_load,
    )

Internal architecture:
    models      — Pydantic data models (MemoryItem, MindMapNode)
    store       — Flat JSON persistence (CRUD, search, embeddings)
    extractor   — Post-conversation extraction pipeline
    comparator  — LLM-guided deduplication
    lifecycle   — Promotion/archival of memories
    feedback    — Retrieval signal (was memory used?)
    prompt      — Dynamic system prompt injection
    organizer   — LLM-powered clustering and mind-map bootstrap
    maintenance — Automatic structure upkeep (divide, cross-link)
    rem         — Higher-order abstraction synthesis
    eval        — Extraction quality evaluation
    history     — Organize-pass result log
"""

# ── Public API ─────────────────────────────────────────────────────────────────
# Lazy imports to avoid circular dependency issues during package initialization.
# Internal memory modules import each other via deferred `from app.memory.X`
# inside functions — eager top-level imports here would trigger those cycles
# before the package is fully loaded.


def __getattr__(name: str):
    """Lazy module-level attribute access for public API symbols."""
    _registry = {
        # Prompt integration
        "get_memory_prompt_section": ("app.memory.prompt", "get_memory_prompt_section"),
        "get_memory_activation_directive": ("app.memory.prompt", "get_memory_activation_directive"),
        # Extraction
        "run_post_conversation_extraction": ("app.memory.extractor", "run_post_conversation_extraction"),
        # Feedback
        "apply_feedback": ("app.memory.feedback", "apply_feedback"),
        "record_load": ("app.memory.feedback", "record_load"),
        "is_labile": ("app.memory.feedback", "is_labile"),
        # Lifecycle
        "run_lifecycle_pass": ("app.memory.lifecycle", "run_lifecycle_pass"),
        # Maintenance
        "run_post_save_maintenance": ("app.memory.maintenance", "run_post_save_maintenance"),
        "get_review_summary": ("app.memory.maintenance", "get_review_summary"),
        "maybe_divide_node": ("app.memory.maintenance", "maybe_divide_node"),
        "discover_cross_links": ("app.memory.maintenance", "discover_cross_links"),
        # Organizer
        "reorganize": ("app.memory.organizer", "reorganize"),
        "should_auto_organize": ("app.memory.organizer", "should_auto_organize"),
        "cleanup_corpus": ("app.memory.organizer", "cleanup_corpus"),
        # History
        "load_organize_history": ("app.memory.organize_history", "load_organize_history"),
        "append_organize_result": ("app.memory.organize_history", "append_organize_result"),
        # Models
        "Memory": ("app.models.memory", "Memory"),
        "MindMapNode": ("app.models.memory", "MindMapNode"),
        "MEMORY_LAYERS": ("app.models.memory", "MEMORY_LAYERS"),
    }

    if name in _registry:
        module_path, attr_name = _registry[name]
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, attr_name)

    raise AttributeError(f"module 'app.memory' has no attribute {name!r}")


__all__ = [
    "get_memory_prompt_section", "get_memory_activation_directive",
    "run_post_conversation_extraction",
    "apply_feedback", "record_load", "is_labile",
    "run_lifecycle_pass",
    "run_post_save_maintenance", "get_review_summary",
    "maybe_divide_node", "discover_cross_links",
    "reorganize", "should_auto_organize", "cleanup_corpus",
    "load_organize_history", "append_organize_result",
    "Memory", "MindMapNode", "MEMORY_LAYERS",
]
