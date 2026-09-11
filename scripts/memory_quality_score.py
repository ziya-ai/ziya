#!/usr/bin/env python3
"""Golden-set memory quality scorer (rubric v1).

The single command every improvement iteration runs to get a scorecard.

What it does
------------
1. Initialises plugins exactly as ``scripts/run_memory_eval.py`` does (so the
   ALE encryption pipeline is wired up and org URI patterns register).
2. Runs the REAL production pipeline
   (``app.memory.extractor.run_post_conversation_extraction`` — the same entry
   ``app/server.py`` fires after a stream completes) against every golden
   conversation, but against SANDBOX stores using the monkey-patch pattern from
   ``scripts/run_memory_priming.py`` / ``run_memory_diagnostic.py``.  Nothing
   under ``~/.ziya/memory/`` is written: the four store files' sha256 are
   captured before and after and any change aborts the run loudly.
   ``--with-lifecycle`` (default on) also runs lifecycle promotion in the
   sandbox so the score reflects what would actually survive to active memory.
3. Scores each conversation's produced memories against its golden ideal set
   (``.ziya/memory-improvement/golden/<chat_id>.json``) using the rubric-v1
   dimensions in ``state.json``.  COVERAGE / PRECISION / GRANULARITY /
   SELF_CONTAINMENT use a semantic LLM judge (Opus, via
   ``app.services.model_resolver.call_service_model`` category ``memory_eval``,
   the same seam ``app/memory/eval.py`` uses).  RETRIEVABILITY runs the REAL
   search path (``app.storage.memory.MemoryStorage.search``) over a sandbox
   store seeded with the produced memories, using each golden fact's queries.
4. Prints a JSON scorecard to stdout and writes it to
   ``.ziya/memory-improvement/scorecards/<label>.json``.

Usage
-----
    python3 scripts/memory_quality_score.py --label baseline
    python3 scripts/memory_quality_score.py --label smoke --limit 4
    python3 scripts/memory_quality_score.py --label baseline --resume
    python3 scripts/memory_quality_score.py --label nolc --no-with-lifecycle

The scoring MATH (``compute_conversation_dims`` / ``aggregate_scorecard``) is
pure and dependency-free so ``tests/test_memory_quality_score.py`` can exercise
it with synthetic fixtures without any LLM call or real store access.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import itertools
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make ``app.*`` / ``scripts.*`` resolve to the working tree.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

MEM_IMPROVE_DIR = _PROJECT_ROOT / ".ziya" / "memory-improvement"
GOLDEN_DIR = MEM_IMPROVE_DIR / "golden"
GOLDEN_RAW_DIR = GOLDEN_DIR / "raw"
SCORECARD_DIR = MEM_IMPROVE_DIR / "scorecards"
STATE_FILE = MEM_IMPROVE_DIR / "state.json"
# Search-only A/B cache (backlog H15): per-chat produced-memory set + covering
# map, extracted ONCE, so a SEARCH change can be A/B-scored on retrievability
# over a FIXED produced set — isolating the search delta from the
# nondeterministic extraction model's run-to-run noise (~+/-0.03 composite).
RETR_CACHE_DIR = MEM_IMPROVE_DIR / "retr-cache"

# The four protected files under ~/.ziya/memory/ that must NEVER change.
_PROTECTED_STORE_FILES = (
    "memories.json",
    "probationary.jsonl",
    "mindmap.json",
    "activity_counter.json",
)

# rubric-v1 weights (mirror of state.json "rubric".weights).
WEIGHTS: Dict[str, float] = {
    "coverage": 0.25,
    "precision": 0.25,
    "granularity": 0.20,
    "self_containment": 0.15,
    "retrievability": 0.15,
}
DIMS: Tuple[str, ...] = (
    "coverage", "precision", "granularity", "self_containment", "retrievability",
)


# ==========================================================================
# PURE SCORING MATH  (unit-tested; no LLM, no store, no app imports)
# ==========================================================================

def _frac(bools: List[bool]) -> Optional[float]:
    """Fraction of True in a list of bools; None for an empty list."""
    if not bools:
        return None
    return sum(1 for b in bools if b) / len(bools)


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def compute_conversation_dims(
    ideal: List[Dict[str, Any]],
    produced: List[Dict[str, Any]],
    judgment: Dict[str, List[bool]],
) -> Dict[str, Optional[float]]:
    """Compute the five rubric-v1 dimensions for ONE conversation.

    ``ideal`` is the golden ideal-memory list (G), ``produced`` is what the
    pipeline yielded (P).  ``judgment`` carries the per-item boolean verdicts:

      - ``covered``       : list[bool], len == len(ideal)    (per golden fact)
      - ``useful``        : list[bool], len == len(produced)
      - ``atomic``        : list[bool], len == len(produced)
      - ``self_contained``: list[bool], len == len(produced)
      - ``retrievable``   : list[bool], len == len(ideal)

    A dimension that is not defined for this conversation (per the rubric's
    empty-set conventions) is returned as ``None`` so the aggregator can
    exclude it from that dimension's mean.
    """
    g = len(ideal)
    p = len(produced)

    covered = list(judgment.get("covered") or [])
    useful = list(judgment.get("useful") or [])
    atomic = list(judgment.get("atomic") or [])
    self_contained = list(judgment.get("self_contained") or [])
    retrievable = list(judgment.get("retrievable") or [])

    # COVERAGE: excluded when |G| == 0 (can't cover nothing).
    coverage = _frac(covered) if g > 0 else None

    # PRECISION: |P|==0 & |G|==0 -> 1.0 (correctly kept nothing);
    #            |P|==0 & |G|>0  -> excluded (miss punished by coverage);
    #            else fraction of produced that are durably useful.
    if p == 0:
        precision = 1.0 if g == 0 else None
    else:
        precision = _frac(useful)

    # GRANULARITY / SELF_CONTAINMENT: excluded when |P| == 0.
    granularity = _frac(atomic) if p > 0 else None
    self_cont = _frac(self_contained) if p > 0 else None

    # RETRIEVABILITY: excluded when |G| == 0.
    retriev = _frac(retrievable) if g > 0 else None

    return {
        "coverage": coverage,
        "precision": precision,
        "granularity": granularity,
        "self_containment": self_cont,
        "retrievability": retriev,
    }


def aggregate_scorecard(
    per_conv_dims: List[Dict[str, Optional[float]]],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Average each dimension over the conversations where it is defined, then
    form the weighted composite over the defined dimension means."""
    w = weights or WEIGHTS
    dim_means: Dict[str, Optional[float]] = {}
    for d in DIMS:
        vals = [c[d] for c in per_conv_dims if c.get(d) is not None]
        dim_means[d] = _mean(vals)

    composite = 0.0
    for d in DIMS:
        if dim_means[d] is not None:
            composite += w[d] * dim_means[d]
    return {"composite": composite, "dims": dim_means}


# ==========================================================================
# STORE-SAFETY HELPERS
# ==========================================================================

def _memory_home() -> Path:
    from app.utils.paths import get_ziya_home
    return get_ziya_home() / "memory"


def _sha256(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def capture_store_hashes() -> Dict[str, Optional[str]]:
    home = _memory_home()
    return {name: _sha256(home / name) for name in _PROTECTED_STORE_FILES}


def assert_stores_unchanged(before: Dict[str, Optional[str]],
                            after: Dict[str, Optional[str]]) -> None:
    """Abort loudly if any protected store file changed.

    memories.json and probationary.jsonl are the hard invariants per the task
    rules; mindmap.json and activity_counter.json are checked too and reported.
    """
    changed = [n for n in _PROTECTED_STORE_FILES if before.get(n) != after.get(n)]
    if changed:
        sys.stderr.write(
            "\n*** CRITICAL FAILURE: protected ~/.ziya/memory/ files changed "
            f"during scoring: {changed} ***\n"
        )
        for n in changed:
            sys.stderr.write(f"    {n}: {before.get(n)} -> {after.get(n)}\n")
        raise SystemExit(3)


# ==========================================================================
# GOLDEN + CONVERSATION LOADING  (read-only)
# ==========================================================================

def load_golden() -> List[Dict[str, Any]]:
    """Load the golden set, ordered by chat_id for determinism."""
    index = json.loads((GOLDEN_DIR / "_golden_index.json").read_text())
    band_by_id = {e["chat_id"]: e["band"] for e in index}
    out: List[Dict[str, Any]] = []
    for f in sorted(GOLDEN_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        data = json.loads(f.read_text())
        chat_id = data["chat_id"]
        out.append({
            "chat_id": chat_id,
            "project_id": data.get("project_id"),
            "band": data.get("band") or band_by_id.get(chat_id, "?"),
            "ideal_memories": data.get("ideal_memories") or [],
        })
    return out


def load_stripped_text(chat_id: str) -> str:
    """Read the pre-captured stripped transcript from the golden raw bundle."""
    raw_path = GOLDEN_RAW_DIR / f"{chat_id}.json"
    if raw_path.exists():
        try:
            return json.loads(raw_path.read_text()).get("stripped_text", "") or ""
        except Exception:
            return ""
    return ""


def load_messages(project_id: Optional[str], chat_id: str) -> List[Dict[str, Any]]:
    """Load and decrypt the real conversation's messages (read-only).

    Locates ~/.ziya/projects/<project_id>/chats/<chat_id>.json.  Falls back to
    a glob across all projects when project_id is missing.  Decrypts via the
    same ALE path ``app/memory/eval.iter_random_conversations`` uses.
    """
    from app.utils.encryption import is_encrypted, get_encryptor
    from app.utils.paths import get_ziya_home
    projects_dir = get_ziya_home() / "projects"
    candidates: List[Path] = []
    if project_id:
        p = projects_dir / project_id / "chats" / f"{chat_id}.json"
        if p.exists():
            candidates.append(p)
    if not candidates:
        candidates = list(projects_dir.glob(f"*/chats/{chat_id}.json"))
    if not candidates:
        raise FileNotFoundError(f"No chat file for {chat_id} (project {project_id})")
    raw = candidates[0].read_bytes()
    if is_encrypted(raw):
        raw = get_encryptor().decrypt(raw)
    data = json.loads(raw)
    return [
        {"role": m.get("role", m.get("type", "unknown")), "content": m.get("content", "")}
        for m in (data.get("messages") or [])
        if isinstance(m.get("content"), str) and m.get("content").strip()
    ]


# ==========================================================================
# SANDBOXED PRODUCTION EXTRACTION
# ==========================================================================

def _sandbox_patches(sandbox_dir: Path, with_lifecycle: bool):
    """Build the monkey-patch context list that redirects EVERY store seam the
    production pipeline touches into ``sandbox_dir`` (proposals, active memory,
    embedding cache, activity counter) so ~/.ziya/memory/ is never written."""
    from unittest.mock import patch
    from app.storage.proposals import ProposalsStore
    from app.storage.memory import MemoryStorage
    from app.services.embedding_service import EmbeddingCache

    sandbox_dir.mkdir(parents=True, exist_ok=True)
    sandbox_prop = ProposalsStore(memory_dir=sandbox_dir)
    sandbox_mem = MemoryStorage(memory_dir=sandbox_dir)
    sandbox_cache = EmbeddingCache(memory_dir=sandbox_dir)

    counter = {"n": 0}
    seq = itertools.count(1)

    def _next_count() -> int:
        counter["n"] = next(seq)
        return counter["n"]

    def _cur_count() -> int:
        return counter["n"]

    patches = [
        patch("app.memory.extractor._next_activity_count", side_effect=_next_count),
        patch("app.memory.lifecycle.current_activity_count", side_effect=_cur_count),
        patch("app.storage.proposals.get_proposals_store", return_value=sandbox_prop),
        patch("app.storage.memory.get_memory_storage", return_value=sandbox_mem),
        patch("app.services.embedding_service.get_embedding_cache", return_value=sandbox_cache),
        patch("app.mcp.builtin_tools.is_builtin_category_enabled", return_value=True),
    ]
    if not with_lifecycle:
        async def _noop_lifecycle():
            return {}
        patches.append(
            patch("app.memory.lifecycle.run_lifecycle_pass", side_effect=_noop_lifecycle)
        )
    return patches, sandbox_prop, sandbox_mem


async def run_extraction_sandboxed(
    chat_id: str,
    project_id: Optional[str],
    messages: List[Dict[str, Any]],
    with_lifecycle: bool,
    sandbox_dir: Path,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Run the real production entry point against fresh, empty sandbox stores.

    Each conversation gets an isolated, empty sandbox (no seeding from the real
    active store) so P reflects exactly what THIS conversation alone yields —
    the fixed, reproducible measuring stick.  Returns (pipeline_result, P).

    P = active memories that survived to the sandbox active store (promotions,
    when --with-lifecycle) PLUS proposals still open in the sandbox
    probationary store.  Archived transient junk is correctly absent from P.
    """
    from app.memory.extractor import run_post_conversation_extraction

    patches, sandbox_prop, sandbox_mem = _sandbox_patches(sandbox_dir, with_lifecycle)
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = await run_post_conversation_extraction(
            messages, conversation_id=chat_id, project_path=None,
        )

    produced: List[Dict[str, Any]] = []
    for m in sandbox_mem.list_memories(status="active"):
        produced.append({
            "id": m.id, "content": m.content, "layer": m.layer,
            "tags": list(m.tags or []), "source": "active",
        })
    for pr in sandbox_prop.list_open():
        produced.append({
            "id": pr.get("id"), "content": pr.get("content", ""),
            "layer": pr.get("layer", "domain_context"),
            "tags": list(pr.get("tags") or []), "source": "proposal",
        })
    return (result if isinstance(result, dict) else {}), produced


# ==========================================================================
# SEMANTIC JUDGE  (Opus, via the memory_eval service-model seam)
# ==========================================================================

_FENCE_OPEN_RE = re.compile(r'^\s*[`]{3,}(?:json|JSON)?\s*\n?', re.MULTILINE)
_FENCE_CLOSE_RE = re.compile(r'\n?\s*[`]{3,}\s*$', re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_CLOSE_RE.sub("", _FENCE_OPEN_RE.sub("", (text or "").strip())).strip()


_JUDGE_SYSTEM_PROMPT = """\
You are the authoritative judge for a memory-extraction quality benchmark.

You are given, for ONE conversation:
  - GOLDEN: the ideal durable memories a perfect system would extract (list G).
  - PRODUCED: the memories an automated pipeline actually produced (list P).

Judge SEMANTICALLY (same fact/decision/principle regardless of wording).

Produce two judgments.

(A) COVERAGE — for each GOLDEN memory g, does SOME produced memory p capture the
    same fact/decision/principle? If yes, give the index of the best-covering p.

(B) For each PRODUCED memory p, three independent booleans:
    - useful: would a FUTURE session genuinely benefit from this? True only for
      durable knowledge (roughly a keep-rating of 4-5): NOT a current-task /
      session artifact, NOT transient debugging state, NOT code narration, NOT a
      near-duplicate paraphrase of another produced memory.
    - atomic: is it EXACTLY ONE self-contained fact/decision/principle expressed
      in 1-3 sentences? False if it is a sentence fragment that needs a sibling
      to make sense, OR a blob that bundles several distinct facts, OR runs long
      and verbose.
    - self_contained: does it name the specific system/file/entity so it is
      intelligible cold next session? False if it leans on an unresolved
      reference like "the document", "this project", "the bug", "that function"
      without naming it.

Output STRICT JSON, no markdown, exactly this shape:
{
  "coverage": [{"g": 0, "covered": true, "p": 2}, {"g": 1, "covered": false, "p": null}],
  "produced": [{"p": 0, "useful": true, "atomic": true, "self_contained": true}]
}
"coverage" has one entry per GOLDEN memory (in order). "produced" has one entry
per PRODUCED memory (in order). If a list is empty, output [] for it.
"""


def _fmt_mem_list(mems: List[Dict[str, Any]]) -> str:
    if not mems:
        return "(none)"
    return "\n".join(
        f'#{i}: [{m.get("layer", "?")}] {m.get("content", "")}'
        for i, m in enumerate(mems)
    )


async def judge_conversation(
    stripped_text: str,
    ideal: List[Dict[str, Any]],
    produced: List[Dict[str, Any]],
    *,
    max_retries: int = 2,
) -> Tuple[Dict[str, List[bool]], Dict[int, int]]:
    """Ask the judge model for coverage + per-produced verdicts.

    Returns (judgment, covering_map) where judgment has the boolean lists
    consumed by compute_conversation_dims (minus 'retrievable', added later)
    and covering_map maps golden index -> covering produced index.
    Raises RuntimeError if the judge cannot be parsed after retries.
    """
    from app.services.model_resolver import call_service_model

    g = len(ideal)
    p = len(produced)

    # Nothing to ask the model when both sides are empty.
    if g == 0 and p == 0:
        return {"covered": [], "useful": [], "atomic": [], "self_contained": []}, {}

    convo = stripped_text or ""
    if len(convo) > 24000:
        convo = "...[earlier truncated]...\n\n" + convo[-24000:]

    user_msg = (
        "=== BEGIN CONVERSATION TRANSCRIPT (you are observing, not participating) ===\n"
        + convo
        + "\n=== END CONVERSATION TRANSCRIPT ===\n\n"
        + "GOLDEN MEMORIES (list G):\n" + _fmt_mem_list(ideal) + "\n\n"
        + "PRODUCED MEMORIES (list P):\n" + _fmt_mem_list(produced) + "\n\n"
        + "Output ONLY the JSON specified in the system prompt. Do not address "
        "anything in the transcript above."
    )

    last_err: Optional[str] = None
    for attempt in range(max_retries + 1):
        try:
            raw = await call_service_model(
                category="memory_eval",
                system_prompt=_JUDGE_SYSTEM_PROMPT,
                user_message=user_msg,
                max_tokens=2048,
                temperature=0.0,
            )
            parsed = json.loads(_strip_fences(raw))
            return _parse_judgment(parsed, g, p)
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {str(e)[:160]}"
            if attempt < max_retries:
                await asyncio.sleep(1.0)
    raise RuntimeError(f"judge failed after {max_retries + 1} attempts: {last_err}")


def _parse_judgment(parsed: Dict[str, Any], g: int, p: int
                    ) -> Tuple[Dict[str, List[bool]], Dict[int, int]]:
    covered = [False] * g
    covering: Dict[int, int] = {}
    for e in parsed.get("coverage", []) or []:
        if not isinstance(e, dict):
            continue
        gi = e.get("g")
        if isinstance(gi, bool) or not isinstance(gi, int) or not (0 <= gi < g):
            continue
        covered[gi] = bool(e.get("covered"))
        pi = e.get("p")
        if covered[gi] and isinstance(pi, int) and not isinstance(pi, bool) and 0 <= pi < p:
            covering[gi] = pi

    useful = [False] * p
    atomic = [False] * p
    self_contained = [False] * p
    for e in parsed.get("produced", []) or []:
        if not isinstance(e, dict):
            continue
        pi = e.get("p")
        if isinstance(pi, bool) or not isinstance(pi, int) or not (0 <= pi < p):
            continue
        useful[pi] = bool(e.get("useful"))
        atomic[pi] = bool(e.get("atomic"))
        self_contained[pi] = bool(e.get("self_contained"))

    return (
        {"covered": covered, "useful": useful, "atomic": atomic,
         "self_contained": self_contained},
        covering,
    )


# ==========================================================================
# RETRIEVABILITY  (real search path over a sandbox store seeded with P)
# ==========================================================================

def score_retrievability(
    ideal: List[Dict[str, Any]],
    produced: List[Dict[str, Any]],
    covering_map: Dict[int, int],
    sandbox_dir: Path,
) -> List[bool]:
    """For each golden fact, run its queries through the REAL search path over a
    sandbox active store seeded with the produced memories; a fact is
    retrievable when the memory that covers it appears in the top-3 for at least
    one of its queries."""
    from unittest.mock import patch
    from app.storage.memory import MemoryStorage
    from app.services.embedding_service import EmbeddingCache
    from app.models.memory import Memory

    out = [False] * len(ideal)
    if not ideal:
        return out
    if not produced:
        return out  # nothing produced -> nothing retrievable

    sandbox_dir.mkdir(parents=True, exist_ok=True)
    store = MemoryStorage(memory_dir=sandbox_dir)
    cache = EmbeddingCache(memory_dir=sandbox_dir)

    id_map: Dict[str, str] = {}   # produced id -> seeded active Memory id
    mems: List[Memory] = []
    for pr in produced:
        mem = Memory(
            content=pr.get("content", ""),
            layer=pr.get("layer", "domain_context"),
            tags=list(pr.get("tags") or []),
        )
        mems.append(mem)
        id_map[pr.get("id")] = mem.id

    with patch("app.services.embedding_service.get_embedding_cache", return_value=cache):
        store.save_many(mems)

        for gi, g in enumerate(ideal):
            pi = covering_map.get(gi)
            if pi is None or not (0 <= pi < len(produced)):
                continue  # uncovered facts are, by definition, not retrievable
            target_id = id_map.get(produced[pi].get("id"))
            queries = (g.get("queries") or [])[:3]
            for q in queries:
                if not q:
                    continue
                results = store.search(q, limit=3)
                top_ids = [r.id for r in results[:3]]
                if target_id in top_ids:
                    out[gi] = True
                    break
    return out


# ==========================================================================
# SEARCH-ONLY A/B HARNESS  (backlog H15)
#
# The whole-pipeline scorer re-runs the nondeterministic extraction model on
# every invocation, so the SAME 24 golden chats yield precision/granularity
# swings of ~0.03-0.08 run to run.  A SEARCH change (e.g. MemoryStorage.search
# ranking, backlog H13/H14) only affects RETRIEVABILITY and deterministically,
# but its true effect (~+0.001-0.002 composite) is an order of magnitude below
# that extraction-noise floor, so it cannot be validated by a fresh full sweep.
#
# This harness breaks the dependency: extract the produced-memory set + the
# Opus covering map ONCE (``--build-retr-cache`` during a normal run), then
# score RETRIEVABILITY over that FROZEN set with whatever search code is on
# disk (``--retr-ab``).  Run --retr-ab against the backup search code and again
# against the change: the produced set is byte-identical, so the retrievability
# delta is 100% search-attributable and reproducible at sub-0.001 resolution.
# ==========================================================================

def build_retr_cache_record(
    chat: Dict[str, Any],
    ideal: List[Dict[str, Any]],
    produced: List[Dict[str, Any]],
    covering_map: Dict[int, int],
) -> Dict[str, Any]:
    """Freeze exactly the inputs ``score_retrievability`` consumes for ONE chat.

    Stores ``ideal`` (golden facts + their queries), the ``produced`` memory
    set, and the Opus ``covering_map`` (golden-index -> produced-index).  This
    is the expensive, nondeterministic output; caching it lets a search change
    be A/B-scored over a fixed set.  Keys are kept as ints in memory; JSON
    stringifies them and ``load_retr_cache`` coerces them back.
    """
    return {
        "chat_id": chat.get("chat_id"),
        "band": chat.get("band"),
        "ideal": ideal,
        "produced": produced,
        "covering_map": {int(k): int(v) for k, v in dict(covering_map).items()},
    }


def save_retr_cache(path: Path, records: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"rubric_version": "v1_5dim", "records": records},
        indent=2, ensure_ascii=False, default=str,
    ))


def load_retr_cache(path: Path) -> List[Dict[str, Any]]:
    """Load a retr cache, coercing JSON string covering_map keys back to int."""
    blob = json.loads(Path(path).read_text())
    records = blob.get("records", blob) if isinstance(blob, dict) else blob
    out: List[Dict[str, Any]] = []
    for rec in records:
        cm = rec.get("covering_map") or {}
        rec = dict(rec)
        rec["covering_map"] = {int(k): int(v) for k, v in cm.items()}
        out.append(rec)
    return out


def score_retrievability_from_cache(
    records: List[Dict[str, Any]],
    sandbox_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """DETERMINISTIC search-only retrievability score over a frozen fact set.

    For each cached chat, re-run ``score_retrievability`` (the exact rubric-v1
    retrievability path: seed a sandbox MemoryStorage with the produced set and
    check whether each golden fact's covering memory ranks top-3 for its
    queries) using WHATEVER search code is currently on disk.  No extraction, no
    Opus judge, no ~/.ziya/memory write — so two calls with the same code give
    an identical number, and two calls with DIFFERENT search code isolate the
    search delta.  Aggregation mirrors the full scorer: per-chat retrievability
    is the fraction of golden facts retrievable (None when |G|==0), and the
    reported dim is the mean over chats where it is defined.
    """
    if sandbox_root is None:
        import tempfile
        sandbox_root = Path(tempfile.mkdtemp(prefix="retr-ab-"))
    else:
        sandbox_root = Path(sandbox_root)
        sandbox_root.mkdir(parents=True, exist_ok=True)

    per_chat: List[Dict[str, Any]] = []
    fracs: List[float] = []
    for rec in records:
        ideal = rec.get("ideal") or []
        produced = rec.get("produced") or []
        cm = {int(k): int(v) for k, v in (rec.get("covering_map") or {}).items()}
        cid = rec.get("chat_id") or "unknown"
        retr_dir = sandbox_root / f"retr-{cid}"
        bools = score_retrievability(ideal, produced, cm, retr_dir)
        frac = _frac(bools)
        per_chat.append({
            "chat_id": cid,
            "band": rec.get("band"),
            "n_ideal": len(ideal),
            "retrievable_facts": sum(1 for b in bools if b),
            "retrievability": frac,
        })
        if frac is not None:
            fracs.append(frac)

    return {
        "retrievability": _mean(fracs),
        "n_chats_total": len(records),
        "n_chats_scored": len(fracs),
        "n_facts": sum(p["n_ideal"] for p in per_chat),
        "n_facts_retrievable": sum(p["retrievable_facts"] for p in per_chat),
        "per_chat": per_chat,
    }


# ==========================================================================
# PER-CONVERSATION SCORING + ORCHESTRATION
# ==========================================================================

async def score_one(chat: Dict[str, Any], with_lifecycle: bool,
                    run_sandbox_root: Path,
                    capture_retr: bool = False) -> Dict[str, Any]:
    chat_id = chat["chat_id"]
    ideal = chat["ideal_memories"]
    sandbox_dir = run_sandbox_root / f"extract-{chat_id}"
    retr_dir = run_sandbox_root / f"retrieve-{chat_id}"

    messages = load_messages(chat.get("project_id"), chat_id)
    stripped = load_stripped_text(chat_id)

    pipe_result, produced = await run_extraction_sandboxed(
        chat_id, chat.get("project_id"), messages, with_lifecycle, sandbox_dir,
    )

    judgment, covering_map = await judge_conversation(stripped, ideal, produced)
    judgment["retrievable"] = score_retrievability(ideal, produced, covering_map, retr_dir)

    dims = compute_conversation_dims(ideal, produced, judgment)
    rec = {
        "chat_id": chat_id,
        "band": chat["band"],
        "n_ideal": len(ideal),
        "n_produced": len(produced),
        "produced_active": sum(1 for p in produced if p.get("source") == "active"),
        "produced_proposals": sum(1 for p in produced if p.get("source") == "proposal"),
        "dims": dims,
        "pipeline_result": pipe_result,
    }
    if capture_retr:
        # Freeze exactly what the retrievability scorer needs so a later
        # search-only A/B run reproduces this fact set without re-extracting
        # (backlog H15). ``ideal`` carries the golden queries already.
        rec["_retr"] = build_retr_cache_record(chat, ideal, produced, covering_map)
    return rec


def _load_partial(partial_path: Path) -> Dict[str, Dict[str, Any]]:
    done: Dict[str, Dict[str, Any]] = {}
    if partial_path.exists():
        for line in partial_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done[rec["chat_id"]] = rec
            except Exception:
                continue
    return done


def _bootstrap() -> None:
    os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")
    from app.plugins import initialize as init_plugins
    init_plugins()
    if os.environ.get("ZIYA_EVAL_VERBOSE") != "1":
        import logging
        for name in ("ZIYA", "boto3", "botocore", "urllib3"):
            logging.getLogger(name).setLevel(logging.WARNING)


def _embeddings_enabled() -> bool:
    try:
        from app.services.embedding_service import get_embedding_provider
        from app.services.embedding_service import NoopProvider
        return not isinstance(get_embedding_provider(), NoopProvider)
    except Exception:
        return False


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True,
                    help="Scorecard label; writes scorecards/<label>.json")
    ap.add_argument("--limit", type=int, default=None,
                    help="Score only the first N golden chats (fast smoke subset)")
    ap.add_argument("--resume", action="store_true",
                    help="Skip chats already scored in scorecards/<label>.partial.jsonl")
    ap.add_argument("--with-lifecycle", dest="with_lifecycle",
                    action="store_true", default=True,
                    help="Run lifecycle promotion in the sandbox (default on)")
    ap.add_argument("--no-with-lifecycle", dest="with_lifecycle",
                    action="store_false",
                    help="Score raw proposals without lifecycle promotion")
    ap.add_argument("--build-retr-cache", action="store_true",
                    help="During a normal run, also freeze each chat's produced "
                         "set + covering map to retr-cache/<label>.json for later "
                         "search-only A/B scoring (backlog H15)")
    ap.add_argument("--retr-ab", default=None, metavar="CACHE",
                    help="Search-only A/B mode: score RETRIEVABILITY over the "
                         "frozen produced set in this retr cache (path, or a "
                         "label resolved to retr-cache/<label>.json) using the "
                         "search code on disk. No extraction/judge; deterministic")
    args = ap.parse_args()

    _bootstrap()

    # --- Search-only A/B mode (backlog H15) -------------------------------
    # Score retrievability over a FROZEN produced set with whatever search code
    # is on disk. Isolates a search change's delta from extraction noise: run
    # against the backup search code and again against the change over the SAME
    # cache. No extraction, no Opus judge, no ~/.ziya/memory write.
    if args.retr_ab:
        cache_path = Path(args.retr_ab)
        if not cache_path.exists():
            cand = RETR_CACHE_DIR / f"{args.retr_ab}.json"
            if cand.exists():
                cache_path = cand
        if not cache_path.exists():
            sys.stderr.write(f"retr cache not found: {args.retr_ab}\n")
            return 2
        hashes_before = capture_store_hashes()
        records = load_retr_cache(cache_path)
        result = score_retrievability_from_cache(records)
        hashes_after = capture_store_hashes()
        assert_stores_unchanged(hashes_before, hashes_after)
        out = {
            "label": args.label,
            "mode": "retr_ab",
            "retr_cache": str(cache_path),
            "retrievability": (round(result["retrievability"], 4)
                               if result["retrievability"] is not None else None),
            "n_chats_total": result["n_chats_total"],
            "n_chats_scored": result["n_chats_scored"],
            "n_facts": result["n_facts"],
            "n_facts_retrievable": result["n_facts_retrievable"],
            "per_chat": result["per_chat"],
            "store_hashes_unchanged": hashes_before == hashes_after,
            "search_mode": ("semantic+keyword" if _embeddings_enabled()
                            else "keyword_only"),
            "scored_at": int(time.time()),
        }
        SCORECARD_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SCORECARD_DIR / f"{args.label}.retr.json"
        out_path.write_text(json.dumps(out, indent=2, default=str))
        print(json.dumps({k: out[k] for k in (
            "retrievability", "n_chats_scored", "n_facts", "n_facts_retrievable",
            "store_hashes_unchanged", "search_mode",
        )}, indent=2))
        sys.stderr.write(f"\nWrote {out_path}\n")
        return 0

    SCORECARD_DIR.mkdir(parents=True, exist_ok=True)
    run_sandbox_root = SCORECARD_DIR / f"_sandbox-{args.label}"
    run_sandbox_root.mkdir(parents=True, exist_ok=True)
    partial_path = SCORECARD_DIR / f"{args.label}.partial.jsonl"

    golden = load_golden()
    if args.limit is not None:
        golden = golden[:args.limit]

    done = _load_partial(partial_path) if args.resume else {}
    if done:
        sys.stderr.write(f"Resuming: {len(done)} chats already scored.\n")

    hashes_before = capture_store_hashes()
    embeddings_on = _embeddings_enabled()
    sys.stderr.write(
        f"Scoring {len(golden)} golden chats | with_lifecycle={args.with_lifecycle} | "
        f"search={'semantic+keyword' if embeddings_on else 'keyword-only (embeddings disabled)'}\n"
    )

    t_start = time.time()
    per_chat: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []

    for i, chat in enumerate(golden, 1):
        cid = chat["chat_id"]
        if cid in done:
            per_chat.append(done[cid])
            sys.stderr.write(f"[{i}/{len(golden)}] {cid[:8]} (cached)\n")
            continue
        try:
            rec = await score_one(chat, args.with_lifecycle, run_sandbox_root,
                                  capture_retr=args.build_retr_cache)
            per_chat.append(rec)
            with open(partial_path, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            d = rec["dims"]
            sys.stderr.write(
                f"[{i}/{len(golden)}] {cid[:8]} ({rec['band']}) "
                f"ideal={rec['n_ideal']} prod={rec['n_produced']} "
                f"cov={_fmt(d['coverage'])} prec={_fmt(d['precision'])} "
                f"gran={_fmt(d['granularity'])} sc={_fmt(d['self_containment'])} "
                f"retr={_fmt(d['retrievability'])}\n"
            )
        except Exception as e:  # noqa: BLE001
            failures.append({"chat_id": cid, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            sys.stderr.write(f"[{i}/{len(golden)}] {cid[:8]} FAILED: {type(e).__name__}: {str(e)[:200]}\n")

        # Re-check the protected stores after every conversation so a leak is
        # caught immediately, not only at the end.
        assert_stores_unchanged(hashes_before, capture_store_hashes())

    hashes_after = capture_store_hashes()
    assert_stores_unchanged(hashes_before, hashes_after)

    agg = aggregate_scorecard([r["dims"] for r in per_chat])
    runtime_s = round(time.time() - t_start, 1)

    scorecard = {
        "label": args.label,
        "composite": round(agg["composite"], 4),
        "dims": {k: (round(v, 4) if v is not None else None)
                 for k, v in agg["dims"].items()},
        "per_chat": per_chat,
        "extracted_count": sum(r["n_produced"] for r in per_chat),
        "ideal_count": sum(r["n_ideal"] for r in per_chat),
        "runtime_s": runtime_s,
        "with_lifecycle": args.with_lifecycle,
        "search_mode": "semantic+keyword" if embeddings_on else "keyword_only",
        "n_scored": len(per_chat),
        "n_failed": len(failures),
        "failures": failures,
        "store_hashes_before": hashes_before,
        "store_hashes_after": hashes_after,
        "store_hashes_unchanged": hashes_before == hashes_after,
        "weights": WEIGHTS,
        "rubric_version": "v1_5dim",
        "scored_at": int(time.time()),
    }

    out_path = SCORECARD_DIR / f"{args.label}.json"
    out_path.write_text(json.dumps(scorecard, indent=2, default=str))

    # Machine-readable scorecard to stdout (the caller pipes/greps this).
    print(json.dumps({k: scorecard[k] for k in (
        "composite", "dims", "extracted_count", "ideal_count", "runtime_s",
        "n_scored", "n_failed", "with_lifecycle", "search_mode",
        "store_hashes_unchanged",
    )}, indent=2))
    sys.stderr.write(f"\nWrote {out_path}\n")

    if args.build_retr_cache:
        records = [r["_retr"] for r in per_chat
                   if isinstance(r, dict) and r.get("_retr")]
        cache_path = RETR_CACHE_DIR / f"{args.label}.json"
        save_retr_cache(cache_path, records)
        sys.stderr.write(
            f"Wrote retr cache {cache_path} ({len(records)} chats) for --retr-ab\n")

    if failures:
        sys.stderr.write(f"WARNING: {len(failures)} chat(s) failed and were excluded.\n")
    return 0


def _fmt(v: Optional[float]) -> str:
    return "  n/a" if v is None else f"{v:.2f}"


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
