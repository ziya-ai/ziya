"""Granularity/atomicity contract for the memory extraction prompt.

Iteration 0 (backlog H6, sharpened): the golden-set rubric-v1 baseline
measured GRANULARITY = 0.767, which FAILS the >=0.85 hard acceptance gate.
The root cause identified in code was the extraction system prompt directive
"Prefer ONE comprehensive memory over multiple fragments about the same
entity", which instructs the small extractor to fuse several distinct
durable facts into a single multi-fact blob.  The Opus granularity judge
scores such blobs as non-atomic, dragging the dimension down.

The fix replaces that directive with an explicit ATOMICITY rule (one
self-contained fact/decision/principle per memory; split multi-fact
candidates; only collapse genuine paraphrases).

These tests pin the contract at the production seam:
  * the module-level EXTRACTION_SYSTEM_PROMPT carries the atomicity rule and
    no longer carries the blob-encouraging directive;
  * extract_memories() actually passes that prompt to the extraction model
    (call_service_model), so the guarantee is reached from the real call
    path, not just a constant.

They FAIL on the pre-change (backup) prompt and PASS after the change.
"""

import asyncio
from unittest.mock import patch

from app.memory import extractor
from app.memory.extractor import EXTRACTION_SYSTEM_PROMPT, extract_memories


# The exact directive that was pushing the extractor toward multi-fact blobs.
_BLOB_DIRECTIVE = "Prefer ONE comprehensive memory over multiple fragments"


def _has_atomicity_rule(prompt: str) -> bool:
    low = prompt.lower()
    return "atomic" in low and (
        "separate" in low or "split" in low
    ) and "1-3 sentences" in low


def test_prompt_drops_blob_directive():
    """The blob-encouraging directive must be gone."""
    assert _BLOB_DIRECTIVE not in EXTRACTION_SYSTEM_PROMPT, (
        "extraction prompt still tells the model to prefer ONE comprehensive "
        "memory over multiple fragments -- this fuses distinct facts into "
        "non-atomic blobs and drags GRANULARITY below the 0.85 gate"
    )


def test_prompt_requires_atomic_memories():
    """The prompt must require one self-contained fact per memory and
    instruct splitting of multi-fact candidates."""
    assert _has_atomicity_rule(EXTRACTION_SYSTEM_PROMPT), (
        "extraction prompt lacks an explicit atomicity rule (one "
        "self-contained fact/decision/principle in 1-3 sentences; split "
        "multi-fact candidates)"
    )


def test_extract_memories_passes_atomic_prompt_to_model():
    """Seam test: the atomicity contract is reached from the real
    extract_memories() call path, i.e. the prompt handed to the extraction
    model carries it."""
    captured = {}

    async def fake_call_service_model(*args, **kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt")
        return "[]"

    with patch(
        "app.services.model_resolver.call_service_model",
        side_effect=fake_call_service_model,
    ):
        result = asyncio.run(
            extract_memories(
                "USER: We decided to use exponential backoff for retries to "
                "the Foo service, and separately Foo stores its state in "
                "DynamoDB table 'bar'.\nASSISTANT: Understood.",
                existing_memories=[],
            )
        )

    assert result == []  # stubbed model returned no candidates
    sp = captured.get("system_prompt")
    assert sp is not None, "extract_memories did not call the extraction model"
    assert _BLOB_DIRECTIVE not in sp
    assert _has_atomicity_rule(sp), (
        "the prompt actually sent to the extraction model lacks the "
        "atomicity rule"
    )
