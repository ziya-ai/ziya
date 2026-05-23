#!/usr/bin/env python3
"""
Memory extraction evaluation runner.

Samples conversations from ~/.ziya/projects/, runs the full extraction
pipeline against each, and asks Opus to judge:
  - whether the conversation contains durable knowledge (salience)
  - whether each extracted candidate is worth keeping (1-5 + gate)
  - what durable knowledge the pipeline failed to extract (missed)

Verdicts are cached at ~/.ziya/memory-eval/verdicts.jsonl so re-running
after heuristic-tuning costs only the heuristic re-execution; Opus
verdicts are reused when the chat_id is already in cache.

Run from project root:

    python -m scripts.run_memory_eval --sample 30 --long 8

The companion ``scripts/render_memory_eval_report.py`` (Diff 5c-3) emits
a markdown report from the cached verdicts.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

# Make ``app.*`` resolve to the working tree, not site-packages.  Same
# fix-up the smoke tests use -- ``python -m`` puts CWD on sys.path,
# but we want this to also work when invoked as ``python scripts/run_memory_eval.py``.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _quiet_startup_logs() -> None:
    """Suppress the wall of INFO from plugin/auth init.

    Memory eval needs the encryption plugin, which means we have to call
    ``initialize()`` -- which logs ~50 lines of provider/auth setup.  The
    first time you run eval those messages are useful, but on the 5th
    iteration they drown out the actual eval output.  Set ZIYA_EVAL_VERBOSE=1
    to keep them.
    """
    if os.environ.get("ZIYA_EVAL_VERBOSE") == "1":
        return
    for name in ("ZIYA", "boto3", "botocore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _bootstrap() -> None:
    """Initialize plugins (for ALE) before importing eval modules."""
    os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")
    from app.plugins import initialize as init_plugins
    init_plugins()
    _quiet_startup_logs()


def _make_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Evaluate memory extraction quality against your conversation history.",
    )
    p.add_argument("--sample", type=int, default=20,
                   help="Total number of conversations to evaluate (default: 20)")
    p.add_argument("--long", type=int, default=5, dest="long_quota",
                   help="How many of the sample MUST be long conversations "
                        "(>=50 messages).  Stratified sampling target. (default: 5)")
    p.add_argument("--seed", type=int, default=None,
                   help="Random seed for sample selection (default: time-based)")
    p.add_argument("--no-cache", action="store_true",
                   help="Re-evaluate conversations already in the verdict cache")
    p.add_argument("--no-salience", action="store_true",
                   help="Skip the salience-judgment Opus call")
    p.add_argument("--no-extraction", action="store_true",
                   help="Skip the actual extraction (no candidate grading either)")
    p.add_argument("--no-grading", action="store_true",
                   help="Run extraction but skip Opus candidate-grading")
    p.add_argument("--no-missed", action="store_true",
                   help="Skip the missed-extraction Opus call")
    p.add_argument("--min-size", type=int, default=5_000,
                   help="Minimum chat-file bytes to consider (default: 5000)")
    return p


def _format_progress_line(idx: int, total: int, chat_id: str, msgs: int,
                           record) -> str:
    """Compact one-line summary for progress reporting."""
    s = record.salience
    sal = (f"{s.agreement[:6]:6s}" if s and s.parse_ok
           else "??????" if s else "(skip)")
    parse = "" if (not s or s.parse_ok) else " [PARSE FAIL]"
    return (
        f"[{idx:>3}/{total}] {chat_id[:8]} ({msgs:>3}m) "
        f"sal={sal} ext={record.pipeline_extracted_count:>2} "
        f"prop={record.pipeline_proposed_count:>2} "
        f"grade={len(record.candidates):>2} miss={len(record.missed):>2}"
        f"{parse}"
    )


async def main() -> int:
    args = _make_arg_parser().parse_args()
    _bootstrap()

    # Imports are deferred until after _bootstrap() so encryption is wired up
    # before any code path tries to read encrypted chat files.
    from app.utils.memory_eval import (
        iter_random_conversations, evaluate_conversation,
        load_cached_verdicts, append_verdict, VERDICT_CACHE_FILE,
    )

    cache = {} if args.no_cache else load_cached_verdicts()
    if cache:
        print(f"Loaded {len(cache)} cached verdicts from {VERDICT_CACHE_FILE}",
              file=sys.stderr)

    # Sample first so we can deduplicate against the cache before any LLM call.
    records = iter_random_conversations(
        args.sample,
        seed=args.seed,
        min_size_bytes=args.min_size,
        long_quota=args.long_quota,
    )

    if not records:
        print("No conversations matched -- check ~/.ziya/projects/ exists "
              "and contains chats >= --min-size bytes.", file=sys.stderr)
        return 1

    if not args.no_cache:
        new_only = [r for r in records if r.chat_id not in cache]
        skipped = len(records) - len(new_only)
        if skipped:
            print(f"Skipping {skipped} already-cached chats; evaluating {len(new_only)} new",
                  file=sys.stderr)
        records = new_only

    if not records:
        print("Nothing to do -- all sampled chats are already in cache. "
              "Use --no-cache to re-evaluate, or --seed N for a different sample.",
              file=sys.stderr)
        return 0

    print(f"Evaluating {len(records)} conversation(s).  This will take "
          f"~{len(records) * 6 // 60 + 1} minute(s) "
          f"and cost roughly ${0.30 * len(records):.2f}.",
          file=sys.stderr)
    print("---", file=sys.stderr)

    t_start = time.time()
    failures = 0
    for i, r in enumerate(records, 1):
        try:
            rec = await evaluate_conversation(
                r,
                do_salience=not args.no_salience,
                do_extraction=not args.no_extraction,
                do_candidate_grading=not args.no_grading,
                do_missed=not args.no_missed,
            )
            append_verdict(rec)
            print(_format_progress_line(i, len(records), r.chat_id,
                                         len(r.messages), rec),
                  file=sys.stderr, flush=True)
        except KeyboardInterrupt:
            print(f"\nInterrupted after {i-1} of {len(records)} -- "
                  f"verdicts so far are saved in {VERDICT_CACHE_FILE}",
                  file=sys.stderr)
            return 130
        except Exception as e:
            failures += 1
            print(f"[{i:>3}/{len(records)}] {r.chat_id[:8]} FAILED: "
                  f"{type(e).__name__}: {str(e)[:120]}",
                  file=sys.stderr, flush=True)

    elapsed = time.time() - t_start
    print("---", file=sys.stderr)
    print(f"Done in {elapsed:.0f}s ({elapsed/max(len(records),1):.1f}s/chat). "
          f"{failures} failure(s).  "
          f"Cache: {VERDICT_CACHE_FILE}",
          file=sys.stderr)
    if failures:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
