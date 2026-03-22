"""
Compaction Engine — converts completed delegate conversations into
Memory Crystals.

A MemoryCrystal is a structured summary (300-500 tokens) that replaces
the full conversation history (10,000-30,000 tokens) while retaining
semantic fidelity for downstream delegates.

Three phases:
  Phase A — Deterministic extraction (zero LLM cost)
  Phase B — LLM summary (one cheap call, ≤200 token output)
  Phase C — Storage and notification

Triggered automatically when a delegate's stream_with_tools exhausts.
Can also be triggered manually via POST /api/v1/.../chats/:id/crystal.

Cross-references:
  - design/conversation-graph-tracker.md §Phase 2: Compaction Engine
  - design/newux-context.md §MemoryCrystal schema
"""

import json
import re
import time
from typing import List, Dict, Any, Optional, Tuple

from app.models.delegate import MemoryCrystal, FileChange
from app.utils.logging_utils import logger

# Minimum conversation token count below which compaction is skipped.
# Trivial conversations (single file_write, <2000 tokens) are retained
# as-is because the crystal overhead exceeds the savings.
MIN_COMPACTION_TOKENS = 2000

# Decision-marker patterns used to extract key decisions from assistant
# messages.  These are sentence-level patterns — the extractor finds
# the sentence containing the match.
_DECISION_PATTERNS = [
    re.compile(r"\bI (?:decided|chose|went with|opted for)\b", re.IGNORECASE),
    re.compile(r"\bUsing\b.*\binstead of\b", re.IGNORECASE),
    re.compile(r"\bThe approach is\b", re.IGNORECASE),
    re.compile(r"\bWe(?:'ll| will) use\b", re.IGNORECASE),
    re.compile(r"\bSelected\b.*\bover\b", re.IGNORECASE),
    re.compile(r"\bThis (?:ensures|avoids|allows)\b", re.IGNORECASE),
]


class CompactionEngine:
    """
    Compresses completed delegate conversations into MemoryCrystals.

    Usage::

        engine = get_compaction_engine()
        crystal = await engine.compact(messages, delegate_id, task_name)
    """

    async def compact(
        self,
        messages: List[Dict[str, Any]],
        delegate_id: str,
        task_name: str,
        *,
        force: bool = False,
    ) -> Optional[MemoryCrystal]:
        """
        Compact a completed delegate conversation into a MemoryCrystal.

        Returns None if the conversation is below MIN_COMPACTION_TOKENS
        and *force* is False.
        """
        original_tokens = self._estimate_tokens(messages)

        if original_tokens < MIN_COMPACTION_TOKENS and not force:
            logger.info(
                f"💎 Skipping compaction for {delegate_id}: "
                f"{original_tokens} tokens < {MIN_COMPACTION_TOKENS} minimum"
            )
            return None

        logger.info(
            f"💎 Compacting {delegate_id} ({task_name}): "
            f"{original_tokens} tokens, {len(messages)} messages"
        )

        # Phase A: Deterministic extraction (zero LLM cost)
        files_changed = self._extract_file_changes(messages)
        tool_stats = self._extract_tool_stats(messages)
        decisions = self._extract_decisions(messages)
        exports = self._extract_exports(messages, files_changed)

        logger.info(
            f"💎 Phase A complete: {len(files_changed)} files, "
            f"{len(decisions)} decisions, {sum(tool_stats.values())} tool calls"
        )

        # Phase B: LLM summary
        summary = await self._generate_summary(
            messages, files_changed, decisions, task_name
        )

        crystal = MemoryCrystal(
            delegate_id=delegate_id,
            task=task_name,
            summary=summary,
            files_changed=files_changed,
            decisions=decisions,
            exports=exports,
            tool_stats=tool_stats,
            original_tokens=original_tokens,
            crystal_tokens=0,  # calculated after serialization
            created_at=time.time(),
        )

        # Calculate crystal token count from the serialized form
        crystal.crystal_tokens = self._estimate_tokens_from_text(
            json.dumps(crystal.model_dump(), default=str)
        )

        compression = (
            (1 - crystal.crystal_tokens / original_tokens) * 100
            if original_tokens > 0 else 0
        )
        logger.info(
            f"💎 Crystal ready: {delegate_id} — "
            f"{original_tokens:,} → {crystal.crystal_tokens:,} tokens "
            f"({compression:.0f}% compaction)"
        )

        return crystal

    # ------------------------------------------------------------------
    # T39: Retroactive crystal review
    # ------------------------------------------------------------------

    async def retroactive_review(
        self,
        late_crystal: MemoryCrystal,
        downstream_crystals: List[MemoryCrystal],
    ) -> str:
        """
        Evaluate a late-arriving crystal against downstream work.

        When delegate A was a dependency of delegate B, but B started
        before A's crystal was ready (e.g. stub promotion), this method
        checks whether A's final crystal conflicts with B's output.

        Returns:
            'preserved'  — No file overlap; downstream unaffected.
            'extended'   — Overlap exists but changes are additive.
            'discarded'  — Conflicting modifications to same files.
        """
        if not downstream_crystals:
            return "preserved"

        late_files = {fc.path for fc in late_crystal.files_changed}
        if not late_files:
            return "preserved"

        downstream_files: Dict[str, set] = {}
        for dc in downstream_crystals:
            for fc in dc.files_changed:
                downstream_files.setdefault(fc.path, set()).add(fc.action)

        overlapping = late_files & set(downstream_files.keys())

        if not overlapping:
            logger.info(
                f"💎 Retroactive review: {late_crystal.delegate_id} — "
                f"no file overlap → preserved"
            )
            return "preserved"

        late_actions = {
            fc.path: fc.action for fc in late_crystal.files_changed
        }

        for path in overlapping:
            late_action = late_actions.get(path, "modified")
            ds_actions = downstream_files.get(path, set())

            if late_action == "deleted" or "deleted" in ds_actions:
                logger.warning(
                    f"💎 Retroactive review: {late_crystal.delegate_id} — "
                    f"conflict on {path} (delete) → discarded"
                )
                return "discarded"

            if late_action == "modified" and "modified" in ds_actions:
                logger.warning(
                    f"💎 Retroactive review: {late_crystal.delegate_id} — "
                    f"conflict on {path} (both modified) → discarded"
                )
                return "discarded"

        logger.info(
            f"💎 Retroactive review: {late_crystal.delegate_id} — "
            f"additive overlap on {overlapping} → extended"
        )
        return "extended"

    # ------------------------------------------------------------------
    # Phase A: Deterministic extraction
    # ------------------------------------------------------------------

    def _extract_file_changes(
        self, messages: List[Dict[str, Any]]
    ) -> List[FileChange]:
        """Scan for file_write tool results to build the changes list."""
        changes: Dict[str, FileChange] = {}  # path -> latest change

        for msg in messages:
            tool_blocks = self._extract_tool_blocks(msg)
            for tool_name, tool_input, tool_output in tool_blocks:
                base_name = tool_name.replace("mcp_", "")

                if base_name in ("file_write", "write_file"):
                    path = (
                        tool_input.get("path", "")
                        or tool_input.get("file_path", "")
                    )
                    if not path:
                        continue

                    content = tool_input.get("content", "")
                    is_patch = bool(tool_input.get("patch"))
                    lines = content.count("\n") + 1 if content else 0

                    if path in changes:
                        action = "modified"
                        line_delta = f"(modified, ~{lines} lines)"
                    elif is_patch:
                        action = "modified"
                        line_delta = "(patched)"
                    elif tool_input.get("create_only"):
                        action = "created"
                        line_delta = f"(new, {lines} lines)"
                    else:
                        action = "modified"
                        line_delta = f"(~{lines} lines)"

                    changes[path] = FileChange(
                        path=path, action=action, line_delta=line_delta
                    )

        return list(changes.values())

    def _extract_tool_stats(
        self, messages: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """Count tool invocations by type."""
        stats: Dict[str, int] = {}
        for msg in messages:
            tool_blocks = self._extract_tool_blocks(msg)
            for tool_name, _, _ in tool_blocks:
                base = tool_name.replace("mcp_", "")
                stats[base] = stats.get(base, 0) + 1
        return stats

    def _extract_decisions(
        self, messages: List[Dict[str, Any]]
    ) -> List[str]:
        """Find sentences with decision-marker language in assistant messages."""
        decisions: List[str] = []
        seen: set = set()

        for msg in messages:
            role = msg.get("role", "")
            if role not in ("assistant", "ai"):
                continue
            content = msg.get("content", "")
            if not content or not isinstance(content, str):
                continue

            sentences = re.split(r"(?<=[.!?])\s+", content)
            for sentence in sentences:
                sentence = sentence.strip()
                if len(sentence) < 15 or len(sentence) > 300:
                    continue
                for pat in _DECISION_PATTERNS:
                    if pat.search(sentence):
                        normalized = sentence[:120]
                        if normalized not in seen:
                            seen.add(normalized)
                            decisions.append(sentence[:200])
                        break

        return decisions[:10]  # cap at 10 decisions

    def _extract_exports(
        self,
        messages: List[Dict[str, Any]],
        file_changes: List[FileChange],
    ) -> Dict[str, str]:
        """
        Extract exported symbols from created/modified files.

        Looks for class and function definitions in file_write content.
        """
        exports: Dict[str, str] = {}

        for msg in messages:
            tool_blocks = self._extract_tool_blocks(msg)
            for tool_name, tool_input, _ in tool_blocks:
                base_name = tool_name.replace("mcp_", "")
                if base_name not in ("file_write", "write_file"):
                    continue

                path = tool_input.get("path", "") or tool_input.get("file_path", "")
                content = tool_input.get("content", "")
                if not path or not content:
                    continue

                # Extract top-level class and function names
                for m in re.finditer(
                    r"^(?:class|def)\s+(\w+)", content, re.MULTILINE
                ):
                    symbol = m.group(1)
                    if not symbol.startswith("_"):
                        module = path.replace("/", ".").replace(".py", "")
                        exports[symbol] = f"{module}.{symbol}"

        return exports

    def _read_research_artifacts(self, paths: List[str], max_total: int = 8000) -> str:
        """Read .md files written by research delegates to extract findings.

        Research delegates typically write their real findings to
        .ziya/tasks/<id>/<topic>/findings.md files. The actual findings
        content in those files is far more useful as a crystal summary
        source than the delegate's process narration in its last message.
        """
        import os
        project_root = os.environ.get("ZIYA_USER_CODEBASE_DIR", os.getcwd())
        combined = []
        total_len = 0

        for path in paths:
            full_path = os.path.join(project_root, path) if not os.path.isabs(path) else path
            try:
                if os.path.isfile(full_path):
                    with open(full_path, 'r', errors='replace') as f:
                        content = f.read()
                    if content.strip():
                        remaining = max_total - total_len
                        if remaining <= 0:
                            break
                        chunk = content[:remaining]
                        combined.append(chunk)
                        total_len += len(chunk)
            except Exception as exc:
                logger.debug(f"Could not read research artifact {path}: {exc}")
                continue

        return "\n\n---\n\n".join(combined) if combined else ""

    # ------------------------------------------------------------------
    # Phase B: LLM summary
    # ------------------------------------------------------------------

    async def _generate_summary(
        self,
        messages: List[Dict[str, Any]],
        files_changed: List[FileChange],
        decisions: List[str],
        task_name: str,
    ) -> str:
        """
        Generate a 2-3 sentence summary using a cheap LLM call.

        Falls back to a deterministic summary if the LLM call fails.
        """
        last_assistant = ""
        for msg in reversed(messages):
            if msg.get("role") in ("assistant", "ai"):
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > 50:
                    last_assistant = content[:1500]
                    break

        file_list = ", ".join(fc.path for fc in files_changed[:8])
        decision_list = "; ".join(decisions[:5])

        # Detect research/analysis delegates that produce findings artifacts
        # rather than source code changes. These need richer summaries because
        # their findings ARE the deliverable.
        research_artifact_paths = [
            fc.path for fc in files_changed
            if '.ziya/tasks/' in fc.path and fc.path.endswith('.md')
        ]
        source_code_changes = [
            fc for fc in files_changed
            if '.ziya/tasks/' not in fc.path
        ]
        is_analysis = len(source_code_changes) == 0

        # For research delegates: read their written findings files to use
        # as summary source instead of the (often narration-heavy) last message
        if is_analysis and research_artifact_paths:
            artifact_content = self._read_research_artifacts(research_artifact_paths)
            if artifact_content:
                last_assistant = artifact_content

        if is_analysis:
            token_limit = "1500-2000"
            sentence_limit = (
                "a comprehensive summary preserving all specific findings, "
                "rankings, file locations, and line numbers. This summary "
                "IS the deliverable — do not refer to external files"
            )
            last_assistant = last_assistant[:6000] if last_assistant else ""
        else:
            token_limit = "200"
            sentence_limit = "2-3 sentences"

        try:
            summary = await self._call_summary_model(
                task_name, file_list, decision_list, last_assistant,
                token_limit=token_limit, sentence_limit=sentence_limit,
            )
            if summary and len(summary) > 20:
                return summary
        except Exception as exc:
            logger.warning(f"💎 LLM summary failed, using fallback: {exc}")

        return self._build_fallback_summary(
            task_name, files_changed, decisions
        )

    async def _call_summary_model(
        self, task: str, files: str, decisions: str, last_msg: str,
        token_limit: str = "200", sentence_limit: str = "2-3 sentences",
    ) -> str:
        """
        Call the current model for a constrained summary.

        Uses ainvoke on the raw model (not the RetryingChatBedrock wrapper)
        because the wrapper expects a different message format.  Falls back
        to the wrapper if the raw model is unavailable.
        """
        from app.agents.agent import model as lazy_model
        from langchain_core.messages import HumanMessage, SystemMessage

        # Scale the prompt window to the token budget — research summaries
        # need far more source material than 2-3 sentence code summaries
        prompt_source_limit = 4000 if int(token_limit.split("-")[0]) > 200 else 800
        prompt = (
            f"Summarize what was accomplished in this delegate task in "
            f"{sentence_limit} (max {token_limit} tokens).\n\n"
            f"Task: {task}\n"
            f"Files changed: {files}\n"
            f"Key decisions: {decisions}\n"
            f"Final work:\n{last_msg[:prompt_source_limit]}\n\n"
            f"Summary:"
        )

        wrapper = lazy_model.get_model()
        if wrapper is None:
            raise RuntimeError("No model available for summary generation")

        # Unwrap RetryingChatBedrock to get the raw LangChain model.
        # The wrapper's invoke path coerces messages through a pipeline
        # that can fail on simple HumanMessage lists.
        raw_model = getattr(wrapper, 'model', wrapper)
        # Some wrappers nest further (e.g. ZiyaBedrock)
        if hasattr(raw_model, 'model') and raw_model is not wrapper:
            raw_model = getattr(raw_model, 'model', raw_model)

        response = await raw_model.ainvoke([HumanMessage(content=prompt)])
        text = response.content if hasattr(response, "content") else str(response)
        max_chars = 3000 if int(token_limit.split("-")[0]) > 200 else 500
        return text.strip()[:max_chars]

    @staticmethod
    def _build_fallback_summary(
        task_name: str,
        files_changed: List[FileChange],
        decisions: List[str],
    ) -> str:
        """Deterministic summary when LLM is unavailable."""
        parts = [f"Completed: {task_name}."]
        if files_changed:
            created = [f.path for f in files_changed if f.action == "created"]
            modified = [f.path for f in files_changed if f.action == "modified"]
            if created:
                parts.append(f"Created {', '.join(created[:3])}.")
            if modified:
                parts.append(f"Modified {', '.join(modified[:3])}.")
        if decisions:
            parts.append(f"Key decision: {decisions[0]}")
        return " ".join(parts)[:500]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tool_blocks(
        msg: Dict[str, Any],
    ) -> List[Tuple[str, Dict[str, Any], str]]:
        """
        Extract (tool_name, input_dict, output_text) from a message.

        Handles both the native Bedrock tool_use/tool_result format and
        the fenced tool blocks used in Ziya's streaming format.
        """
        results: List[Tuple[str, Dict[str, Any], str]] = []
        content = msg.get("content", "")

        # Format 1: Bedrock native tool_use content blocks
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    results.append((
                        block.get("name", ""),
                        block.get("input", {}),
                        "",
                    ))
            return results

        if not isinstance(content, str):
            return results

        # Format 2: fenced blocks in serialized messages
        for m in re.finditer(
            r"`{3,4}tool:(\S+?)(?:\|[^\n]*)?\n(.*?)`{3,4}",
            content,
            re.DOTALL,
        ):
            tool_name = m.group(1)
            tool_body = m.group(2).strip()
            try:
                parsed = json.loads(tool_body)
                if isinstance(parsed, dict):
                    results.append((tool_name, parsed, tool_body))
                    continue
            except (json.JSONDecodeError, ValueError):
                pass
            results.append((tool_name, {}, tool_body))

        return results

    @staticmethod
    def _estimate_tokens(messages: List[Dict[str, Any]]) -> int:
        """Rough token estimate from message list."""
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(str(block.get("text", "")))
        return total_chars // 4

    @staticmethod
    def _estimate_tokens_from_text(text: str) -> int:
        return len(text) // 4


_instance: Optional[CompactionEngine] = None


def get_compaction_engine() -> CompactionEngine:
    global _instance
    if _instance is None:
        _instance = CompactionEngine()
    return _instance
