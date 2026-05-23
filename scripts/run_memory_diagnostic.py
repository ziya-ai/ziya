"""Memory extraction diagnostic.

Runs the full extraction pipeline against a single conversation with
exhaustive instrumentation, then dumps both a structured JSON trace and
a human-readable markdown report.

The goal is to answer, with evidence rather than speculation:
  - What did the model see at each window?
  - What did the model return?
  - Which gates fired against which candidates, and why?
  - What did the comparator decide and what was the prompt?
  - Where in the pipeline did real signal get killed, and where did
    real noise survive?

Usage:
    python scripts/run_memory_diagnostic.py <chat_id_substring>

If the substring is ambiguous it lists matching candidates and exits.
Output goes to ~/.ziya/memory-diagnostic/<chat_id>/ as trace.json and
report.md (override with --output).

The sandbox directory under the output is wiped on each run so
per-conversation traces are clean -- earlier versions accumulated
across invocations, which made cross-run paraphrase counts misleading.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

# Self-locate the project root so the script works no matter where the
# repo lives.  scripts/ is one level under the project root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def _bootstrap_plugins():
    os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")
    try:
        from app.plugins import initialize as init_plugins
        init_plugins()
    except Exception as e:
        _eprint(f"plugin init failed (continuing): {e}")


# Trace accumulator; populated by wrappers below.
TRACE: Dict[str, Any] = {
    "chat_id": None,
    "title": None,
    "human_turns": 0,
    "salience_hits": 0,
    "windows": [],          # one entry per window
    "model_calls": [],      # every call to call_service_model
    "gate_decisions": [],   # candidate -> pass/reject + reason
    "dedup_decisions": [],  # candidate -> kept/dropped + reason
    "comparator_decisions": [],  # candidate -> ADD/UPDATE/NOOP + prompt
    "result": None,
    "started_at": None,
    "duration_ms": None,
}


def _summarize_text(text: str, max_chars: int = 4000) -> str:
    """Trim a long text for the trace.  Full text is preserved in JSON
    via raw fields when the call's purpose is forensic; this is for the
    summary fields used by the markdown report."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n... [{len(text) - max_chars} chars truncated] ..."


def _wrap_call_service_model(real_fn):
    async def wrapped(category, system_prompt, user_message,
                      max_tokens=2048, temperature=0.2):
        # Resolve and record the actual model the request is routed to.
        # Earlier diagnostic runs left this implicit, which made it
        # impossible to tell whether observed quality reflected Haiku,
        # Sonnet, Nova-Lite, or whatever the resolver currently picks.
        resolved = {}
        try:
            from app.services.model_resolver import resolve_service_model
            resolved = resolve_service_model(category) or {}
        except Exception as e:
            resolved = {"resolve_error": str(e)}
        t0 = time.time()
        try:
            response = await real_fn(category, system_prompt, user_message,
                                     max_tokens=max_tokens, temperature=temperature)
            error = None
        except Exception as e:
            response = ""
            error = str(e)
            raise
        finally:
            duration_ms = int((time.time() - t0) * 1000)
            TRACE["model_calls"].append({
                "category": category,
                "resolved_model": {
                    "endpoint": resolved.get("endpoint"),
                    "model_id": resolved.get("model_id"),
                    "region": resolved.get("region"),
                },
                "system_prompt_chars": len(system_prompt),
                "user_message_chars": len(user_message),
                "user_message": _summarize_text(user_message, max_chars=12000),
                "system_prompt": _summarize_text(system_prompt, max_chars=8000),
                "response": _summarize_text(response, max_chars=8000),
                "max_tokens": max_tokens,
                "temperature": temperature,
                "duration_ms": duration_ms,
                "error": error,
            })
        return response
    return wrapped


def _wrap_extract_memories(real_fn):
    async def wrapped(stripped_conversation, existing_memories,
                      project_name=None, project_path=None):
        window_idx = len(TRACE["windows"])
        result = await real_fn(stripped_conversation, existing_memories,
                               project_name=project_name,
                               project_path=project_path)
        TRACE["windows"].append({
            "window_index": window_idx,
            "stripped_chars": len(stripped_conversation),
            "stripped_text": _summarize_text(stripped_conversation, max_chars=8000),
            "candidates_returned": len(result) if isinstance(result, list) else 0,
            "candidates": result,
            "existing_memories_in_dedup_context": len(existing_memories),
        })
        return result
    return wrapped


def _wrap_quality_gate(real_fn):
    """Replay the gate logic against each candidate and capture which
    rule fired, then return the real function's output.  We don't
    reimplement the rules here -- we run the real one twice (once per
    candidate, isolated) to capture per-candidate verdicts."""
    def wrapped(candidates):
        # First, get the real verdict
        passed = real_fn(candidates)
        passed_ids = {id(c) for c in passed}

        # Then, single-candidate replays to figure out which gate killed
        # each rejected one.  This is forensic: we want to know "the
        # 'Ziya is context-management first' candidate was rejected by
        # which structural rule?"  Running the real gate one-at-a-time
        # gives us that.
        for c in candidates:
            content = c.get("content", "")
            tags = c.get("tags", [])
            if id(c) in passed_ids:
                TRACE["gate_decisions"].append({
                    "content": content,
                    "layer": c.get("layer"),
                    "tags": tags,
                    "passed": True,
                    "reason": None,
                })
                continue
            # Rejected -- figure out why by re-running with single candidate
            single_passed = real_fn([c])
            if single_passed:
                # Cap-of-tags would be the only mutation that doesn't
                # outright drop; if real_fn dropped it in batch but kept
                # in isolation, that's a code bug worth knowing about.
                reason = "rejected_in_batch_but_passes_alone (tag cap?)"
            else:
                reason = _diagnose_gate_rejection(content, tags)
            TRACE["gate_decisions"].append({
                "content": content,
                "layer": c.get("layer"),
                "tags": tags,
                "passed": False,
                "reason": reason,
            })
        return passed
    return wrapped


def _diagnose_gate_rejection(content: str, tags: List[str]) -> str:
    """Return a human-readable reason a gate would reject this candidate.

    Mirrors the structural checks in quality_gate.  Returns the FIRST
    matching reason -- if multiple gates would fire, only the first is
    reported (which matches the actual gate's short-circuit behavior).
    """
    from app.utils.memory_extractor import (
        MIN_CONTENT_CHARS, MAX_CONTENT_CHARS,
        _DANGLING_REF_RE, _CODE_ARTIFACT_RE, _FILE_REF_RE,
        _CSS_PATTERN_RE, _REFACTORING_RE, _CODE_DESCRIPTION_RE, _CAREER_RE,
    )
    if len(content) < MIN_CONTENT_CHARS:
        return f"too_short ({len(content)} chars)"
    if len(content) > MAX_CONTENT_CHARS:
        return f"too_long ({len(content)} chars)"
    dangling_hits = len(_DANGLING_REF_RE.findall(content))
    if dangling_hits >= 2:
        return f"dangling_refs ({dangling_hits} hits)"
    code_id_count = len(_CODE_ARTIFACT_RE.findall(content))
    if code_id_count >= 3:
        return f"code_identifiers ({code_id_count})"
    file_ref_count = len(_FILE_REF_RE.findall(content))
    if file_ref_count >= 2:
        return f"file_refs ({file_ref_count})"
    if _CSS_PATTERN_RE.search(content):
        return "css_layout_pattern"
    if _REFACTORING_RE.search(content):
        return "refactoring_note"
    if _CODE_DESCRIPTION_RE.search(content):
        return "code_description"
    if _CAREER_RE.search(content):
        return "career_narrative"
    return "unknown_reject_reason"


def _wrap_compare_memory(real_fn):
    async def wrapped(candidate, similar):
        decision = await real_fn(candidate, similar)
        TRACE["comparator_decisions"].append({
            "candidate_content": candidate.get("content"),
            "candidate_layer": candidate.get("layer"),
            "similar_count": len(similar),
            "similar": [
                {"id": m.get("id"), "content": m.get("content"),
                 "similarity": m.get("similarity")}
                for m in similar
            ],
            "decision": decision,
        })
        return decision
    return wrapped


# -- main --------------------------------------------------------------

async def run_diagnostic(chat_id_or_substring: str, output_dir: Path) -> int:
    _bootstrap_plugins()

    from app.utils.memory_eval import iter_random_conversations
    from app.utils.memory_extractor import (
        run_post_conversation_extraction,
        _count_salience_hits,
    )
    from app.storage.proposals import ProposalsStore
    from app.storage.memory import MemoryStorage
    from app.utils.paths import get_ziya_home

    # Find the chat
    all_chats = iter_random_conversations(
        sample_size=1_000_000, seed=None, long_quota=0,
    )
    matches = [c for c in all_chats if chat_id_or_substring in c.chat_id]
    if not matches:
        _eprint(f"No chats matching {chat_id_or_substring!r}")
        return 1
    if len(matches) > 1:
        # Same chat_id can appear in multiple project dirs (copies).
        # If all matches share chat_id, take the first; only error
        # when chat_ids differ.
        unique_ids = {c.chat_id for c in matches}
        if len(unique_ids) == 1:
            _eprint(f"Note: chat_id appears in {len(matches)} project dirs; "
                    f"using first.")
        else:
            _eprint(f"Ambiguous: {len(matches)} chats matched.  First 10:")
            for c in matches[:10]:
                _eprint(f"  {c.chat_id}  ({len(c.messages)} msgs)  {c.title[:60]}")
            return 1
    chat = matches[0]
    _eprint(f"Diagnosing {chat.chat_id} ({len(chat.messages)} msgs): {chat.title[:80]}")

    output_dir.mkdir(parents=True, exist_ok=True)
    TRACE["chat_id"] = chat.chat_id
    TRACE["title"] = chat.title
    TRACE["started_at"] = int(time.time() * 1000)

    messages = [
        {"role": m.get("role"), "content": m.get("content")}
        for m in chat.messages if m.get("content")
    ]
    TRACE["human_turns"] = sum(1 for m in messages
                               if m.get("role") in ("human", "user"))
    TRACE["salience_hits"] = _count_salience_hits(messages)

    # Sandbox active store + proposals so we don't pollute real data.
    # Wipe sandbox at start of each run so cross-run paraphrase counts
    # aren't misleading -- a single conversation should produce the
    # same proposals every time it's diagnosed.
    sandbox_dir = output_dir / "sandbox"
    if sandbox_dir.exists():
        shutil.rmtree(sandbox_dir)
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    real_active = get_ziya_home() / "memory" / "memories.json"
    sandbox_active = sandbox_dir / "memories.json"
    if real_active.exists() and not sandbox_active.exists():
        shutil.copy2(real_active, sandbox_active)
    # Seed the sandbox embedding cache from the real cache so the
    # comparator sees the same active-memory neighborhood it would see
    # in production -- without seeding, dedup against active memories
    # would miss every corroboration signal.  ProposalsStore.add will
    # write *new* proposal embeddings into the sandbox copy, leaving
    # the real cache untouched.  Without this isolation each diagnostic
    # run leaks ~10 prop_* entries into the real cache, and over time
    # those out-rank legitimate active matches in cosine search (this
    # is how the test suite broke earlier in this session).
    real_embeds = get_ziya_home() / "memory" / "embeddings.npz"
    sandbox_embeds = sandbox_dir / "embeddings.npz"
    if real_embeds.exists() and not sandbox_embeds.exists():
        shutil.copy2(real_embeds, sandbox_embeds)
    from app.services.embedding_service import EmbeddingCache
    sandbox_embedding_cache = EmbeddingCache(memory_dir=sandbox_dir)

    sandbox_proposals = ProposalsStore(memory_dir=sandbox_dir)
    sandbox_memory = MemoryStorage(memory_dir=sandbox_dir)

    # Wrap the seams
    from app.services import model_resolver
    from app.utils import memory_extractor
    from app.utils import memory_comparator

    real_call = model_resolver.call_service_model
    real_extract = memory_extractor.extract_memories
    real_quality = memory_extractor.quality_gate
    real_compare = memory_comparator.compare_memory

    t0 = time.time()
    try:
        with patch("app.storage.proposals.get_proposals_store",
                   return_value=sandbox_proposals), \
             patch("app.storage.memory.get_memory_storage",
                   return_value=sandbox_memory), \
             patch("app.services.embedding_service.get_embedding_cache",
                   return_value=sandbox_embedding_cache), \
             patch("app.mcp.builtin_tools.is_builtin_category_enabled",
                   return_value=True), \
             patch("app.services.model_resolver.call_service_model",
                   side_effect=_wrap_call_service_model(real_call)), \
             patch("app.utils.memory_extractor.extract_memories",
                   side_effect=_wrap_extract_memories(real_extract)), \
             patch("app.utils.memory_extractor.quality_gate",
                   side_effect=_wrap_quality_gate(real_quality)), \
             patch("app.utils.memory_comparator.compare_memory",
                   side_effect=_wrap_compare_memory(real_compare)):
            result = await run_post_conversation_extraction(
                messages, conversation_id=chat.chat_id,
                project_path=None,
            )
        TRACE["result"] = result
    finally:
        TRACE["duration_ms"] = int((time.time() - t0) * 1000)

    # Pull the open proposals from sandbox so we can show what landed
    TRACE["sandbox_proposals"] = [
        {"id": p.get("id"),
         "layer": p.get("layer"),
         "content": p.get("content"),
         "tags": p.get("tags"),
         "corroborations": p.get("corroborations", 0)}
        for p in sandbox_proposals.list_open()
    ]

    # Dump trace
    trace_path = output_dir / "trace.json"
    trace_path.write_text(json.dumps(TRACE, indent=2, default=str))
    _eprint(f"Wrote trace: {trace_path}")

    # Render markdown report
    report_path = output_dir / "report.md"
    report_path.write_text(_render_report(TRACE))
    _eprint(f"Wrote report: {report_path}")
    return 0


def _render_report(t: Dict[str, Any]) -> str:
    out: List[str] = []
    a = out.append
    a(f"# Memory Extraction Diagnostic")
    a(f"")
    a(f"- **Chat:** `{t['chat_id']}`")
    a(f"- **Title:** {t['title']}")
    a(f"- **Human turns:** {t['human_turns']}")
    a(f"- **Salience hits:** {t['salience_hits']}")
    a(f"- **Duration:** {t['duration_ms']} ms")
    a(f"- **Result:** `{t['result']}`")
    a(f"")
    a(f"## Pipeline summary")
    a(f"")
    a(f"- Windows extracted: {len(t['windows'])}")
    total_candidates = sum(w["candidates_returned"] for w in t["windows"])
    a(f"- Total candidates from model: {total_candidates}")
    gate_passed = sum(1 for d in t["gate_decisions"] if d["passed"])
    gate_rejected = len(t["gate_decisions"]) - gate_passed
    a(f"- Gate decisions: {gate_passed} passed, {gate_rejected} rejected")
    a(f"- Comparator decisions: {len(t['comparator_decisions'])}")
    a(f"- Sandbox proposals: {len(t.get('sandbox_proposals', []))}")
    a(f"- Model calls total: {len(t['model_calls'])}")
    a(f"")

    # Per-window detail
    a(f"## Windows")
    for w in t["windows"]:
        a(f"")
        a(f"### Window {w['window_index']}")
        a(f"")
        a(f"- Stripped conversation: **{w['stripped_chars']} chars**")
        a(f"- Existing-memory dedup context: {w['existing_memories_in_dedup_context']} memories")
        a(f"- Candidates returned: **{w['candidates_returned']}**")
        if w["candidates"]:
            a(f"")
            a(f"**Candidates:**")
            a(f"")
            for c in w["candidates"]:
                a(f"- [{c.get('layer', '?')}] {c.get('content', '')[:200]}")
        a(f"")
        a(f"<details>")
        a(f"<summary><strong>Stripped conversation seen by model</strong></summary>")
        a(f"")
        a(f"```")
        a(w["stripped_text"])
        a(f"```")
        a(f"")
        a(f"</details>")

    # Gate decisions
    a(f"")
    a(f"## Quality gate decisions")
    a(f"")
    a(f"| Pass | Layer | Reason | Content |")
    a(f"|------|-------|--------|---------|")
    for d in t["gate_decisions"]:
        verdict = "✅" if d["passed"] else "❌"
        layer = d.get("layer", "?")
        reason = d.get("reason") or "—"
        content = (d.get("content") or "").replace("\n", " ").replace("|", "\\|")[:140]
        a(f"| {verdict} | {layer} | {reason} | {content} |")

    # Comparator decisions
    if t["comparator_decisions"]:
        a(f"")
        a(f"## Comparator decisions")
        a(f"")
        for d in t["comparator_decisions"]:
            decision = d.get("decision", {})
            action = decision.get("action") if isinstance(decision, dict) else decision
            a(f"### {action}")
            a(f"")
            a(f"- **New candidate:** [{d.get('candidate_layer', '?')}] "
              f"{d.get('candidate_content', '')[:200]}")
            a(f"- **Similar memories ({d['similar_count']}):**")
            for s in d.get("similar", []):
                a(f"  - `{s.get('id')}` (sim={s.get('similarity')}): "
                  f"{(s.get('content') or '')[:150]}")
            a(f"- **Decision:** `{decision}`")
            a(f"")

    # Final proposals that landed in sandbox
    a(f"")
    a(f"## Sandbox proposals (final state)")
    a(f"")
    proposals = t.get("sandbox_proposals", [])
    if not proposals:
        a(f"_No proposals landed._")
    else:
        # Bucket by layer
        by_layer: Dict[str, List[Any]] = {}
        for p in proposals:
            by_layer.setdefault(p.get("layer", "?"), []).append(p)
        for layer in sorted(by_layer):
            entries = by_layer[layer]
            a(f"")
            a(f"### {layer} ({len(entries)})")
            a(f"")
            for p in entries:
                tags = p.get("tags") or []
                tag_str = f" [{','.join(tags[:3])}]" if tags else ""
                a(f"- `{p['id']}` corr={p.get('corroborations', 0)}{tag_str}  "
                  f"{p.get('content', '')[:200]}")

    # Model calls
    a(f"")
    a(f"## Model calls")
    a(f"")
    # Surface the resolved model so we can tell at a glance which tier
    # produced the observations.  When the tier varies between calls,
    # the per-row column shows it; when it's uniform a header line
    # makes the run-wide identity obvious.
    resolved_set = {
        (c.get("resolved_model") or {}).get("model_id") or "?"
        for c in t["model_calls"]
    }
    if len(resolved_set) == 1:
        only_model = next(iter(resolved_set))
        a(f"_All calls routed to:_ `{only_model}`")
        a(f"")
    a(f"| # | Category | Model | Duration | Sys chars | User chars | Resp chars |")
    a(f"|---|----------|-------|----------|-----------|------------|------------|")
    for i, call in enumerate(t["model_calls"]):
        rm = call.get("resolved_model") or {}
        model_id = rm.get("model_id") or "?"
        # Trim long bedrock IDs so the table stays readable
        short = model_id.split(":")[0].split(".")[-1] if "." in model_id else model_id
        a(f"| {i} | {call['category']} | {short} | {call['duration_ms']}ms | "
          f"{call['system_prompt_chars']} | {call['user_message_chars']} | "
          f"{len(call['response'])} |")

    a(f"")
    a(f"<details>")
    a(f"<summary><strong>Full model call traces (system + user + response)</strong></summary>")
    a(f"")
    for i, call in enumerate(t["model_calls"]):
        a(f"")
        a(f"### Call {i}: {call['category']}")
        a(f"")
        a(f"**System prompt** ({call['system_prompt_chars']} chars):")
        a(f"")
        a(f"```")
        a(call["system_prompt"])
        a(f"```")
        a(f"")
        a(f"**User message** ({call['user_message_chars']} chars):")
        a(f"")
        a(f"```")
        a(call["user_message"])
        a(f"```")
        a(f"")
        a(f"**Response** ({len(call['response'])} chars):")
        a(f"")
        a(f"```")
        a(call["response"])
        a(f"```")
    a(f"")
    a(f"</details>")

    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("chat_id", help="Chat ID or unique substring")
    p.add_argument("--output", default=None,
                   help="Output dir (default ~/.ziya/memory-diagnostic/<chat_id>)")
    args = p.parse_args()

    if args.output:
        output_dir = Path(args.output).expanduser()
    else:
        from app.utils.paths import get_ziya_home
        output_dir = get_ziya_home() / "memory-diagnostic" / args.chat_id.replace("/", "_")
    return asyncio.run(run_diagnostic(args.chat_id, output_dir))


if __name__ == "__main__":
    sys.exit(main())
