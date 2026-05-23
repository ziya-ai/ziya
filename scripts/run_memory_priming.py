"""Retroactive memory priming.

Walks historical conversations through the now-functional extraction
pipeline and parks proposals in a sandbox probationary store, isolated
from the real ~/.ziya/memory/probationary.jsonl.  Lets the user inspect
what production extraction *would* have produced over their conversation
history before committing anything to the durable store.

Background: production extraction was non-functional for an extended
period due to a windowed-loop accumulation bug (see handoff doc).  The
fix landed, but the user has 600+ historical conversations that never
went through working extraction.  This script walks them all, in
isolation, so the user can vet output quality before letting it touch
the real store.

Subcommands:
    prime    -- walk conversations into the sandbox
    inspect  -- print a summary of sandbox contents
    commit   -- copy sandbox proposals into the real probationary store
    clear    -- drop sandbox state (jsonl + orphaned embeddings)

Read/write asymmetry during prime:
    Reads from real active memory store (so dedup-vs-existing-knowledge
    is honest -- we don't want sandbox to re-extract things already in
    active memory).
    Writes to sandbox proposals store via monkey-patch of
    ``app.storage.proposals.get_proposals_store``.
    Embedding cache is global by design -- proposal embeddings land in
    the real ~/.ziya/memory/embeddings.npz with prop_* IDs.  This is OK
    because IDs are content-hash stable: commit re-uses them; clear
    removes them.

Activity counter:
    Sandbox does NOT touch the real activity counter.  All sandbox
    proposals get activity_count_at_proposal=0 during priming.  On
    commit, each is re-added with the current real counter so decay
    starts fresh from the user's real activity baseline.

Lifecycle:
    Does NOT run during priming.  Pure extraction only.  After commit,
    the next normal stream-completion lifecycle pass picks them up.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

# Bootstrap working-tree imports so this works without an installed wheel
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _eprint(*args, **kwargs):
    """Stderr printf so subcommand stdout stays clean for piping."""
    print(*args, file=sys.stderr, **kwargs)


def _bootstrap_plugins() -> None:
    """Initialize plugins so org-specific URI patterns register.

    Reference-layer detection uses ``ExtractionPatternProvider`` plugins
    (e.g. internal Amazon wikis) registered at startup.  Without this
    call, sandbox extraction sees only the built-in URL/PDF patterns,
    which differs from production behavior.
    """
    import os
    os.environ.setdefault("ZIYA_LOAD_INTERNAL_PLUGINS", "1")
    try:
        from app.plugins import initialize as init_plugins
        init_plugins()
    except Exception as e:
        _eprint(f"Plugin init failed (continuing with built-ins only): {e}")


# -- prime ------------------------------------------------------------------

async def cmd_prime(args) -> int:
    """Run the production extraction pipeline against historical
    conversations, writing proposals to a sandbox store."""
    _bootstrap_plugins()

    from app.utils.memory_eval import iter_random_conversations
    from app.storage.proposals import ProposalsStore
    from app.storage.memory import MemoryStorage
    from app.utils.memory_extractor import run_post_conversation_extraction

    sandbox_dir = Path(args.source).expanduser()
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    sandbox = ProposalsStore(memory_dir=sandbox_dir)

    # Sandbox the active memory store too.  The extraction pipeline
    # mutates active memories: corroboration bumps on similar matches,
    # full UPDATE rewrites when the comparator chooses that path, and
    # last_accessed timestamps.  Without redirecting MemoryStorage, a
    # priming run silently writes those mutations into the real
    # ~/.ziya/memory/memories.json -- the active store the user sees in
    # production.
    #
    # Copy the real memories.json into the sandbox dir on first run so
    # dedup-against-existing-knowledge still works correctly.  Mutations
    # land in the sandbox copy.  On commit, only the proposals migrate;
    # mutated active records stay in the sandbox (they're already
    # represented in the real store, just without the priming-induced
    # bumps -- which is what we want, those bumps shouldn't persist
    # without going through the normal lifecycle).
    from app.utils.paths import get_ziya_home
    sandbox_active = sandbox_dir / "memories.json"
    real_active = get_ziya_home() / "memory" / "memories.json"
    if real_active.exists() and not sandbox_active.exists():
        import shutil
        shutil.copy2(real_active, sandbox_active)
        _eprint(f"Copied {real_active.name} into sandbox for read-only dedup baseline.")
    sandbox_memory = MemoryStorage(memory_dir=sandbox_dir)

    # iter_random_conversations expects sample_size > 0; pass a huge
    # number when --max isn't set so we walk every available chat.
    sample_size = args.max if args.max is not None else 1_000_000
    records = iter_random_conversations(
        sample_size=sample_size,
        seed=args.seed,
        long_quota=0,  # No stratification -- priming wants the full population
    )

    if not records:
        _eprint("No conversations found to prime.")
        return 0

    _eprint(f"Processing {len(records)} conversations into sandbox at {sandbox_dir}")

    success = 0
    failures = 0
    extracted_total = 0
    proposed_total = 0

    # Reasons we observed for short-circuit returns -- surfaced at the
    # end so a 0-proposal run is diagnosable instead of mysterious.  The
    # default {} per-result path means an unrecognized return shape
    # increments the "unknown" bucket.
    skip_reasons: dict = {}

    for i, chat in enumerate(records, 1):
        try:
            messages = [
                {"role": m.get("role"), "content": m.get("content")}
                for m in chat.messages
                if m.get("content")
            ]
            # Patch the proposals-store factory AND the memory-enabled
            # toggle.  The user has explicitly opted into priming by
            # running this script -- gating it on the same runtime flag
            # that controls live extraction defeats the purpose, and the
            # original 20-conversation run silently skipped every entry
            # because the flag was off.  Active store stays real so
            # dedup-vs-existing-knowledge works.  Embedding cache is
            # global on purpose -- see module docstring.
            with patch("app.storage.proposals.get_proposals_store",
                       return_value=sandbox), \
                 patch("app.mcp.builtin_tools.is_builtin_category_enabled",
                       return_value=True), \
                 patch("app.storage.memory.get_memory_storage",
                       return_value=sandbox_memory):
                result = await run_post_conversation_extraction(
                    messages,
                    conversation_id=chat.chat_id,
                    project_path=None,  # Don't tag sandbox with original project
                )
            if isinstance(result, dict) and result.get("skipped"):
                reason = result.get("reason", "unknown")
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            extracted = result.get("extracted", 0) if isinstance(result, dict) else 0
            proposed = result.get("proposed", 0) if isinstance(result, dict) else 0
            references = result.get("references", 0) if isinstance(result, dict) else 0
            extracted_total += extracted
            proposed_total += proposed
            skip_tag = ""
            if isinstance(result, dict) and result.get("skipped"):
                skip_tag = f"  SKIPPED ({result.get('reason', 'unknown')})"
            ref_tag = f" refs={references}" if references else ""
            _eprint(f"[{i}/{len(records)}] {chat.chat_id[:12]}  "
                    f"extracted={extracted} proposed={proposed}{ref_tag}{skip_tag}")
            success += 1
        except Exception as e:
            _eprint(f"[{i}/{len(records)}] {chat.chat_id[:12]} FAILED: {e}")
            failures += 1

    opens = sandbox.list_open()
    _eprint(
        f"\nDone. {success} succeeded, {failures} failed.  "
        f"Extracted {extracted_total} candidates, proposed {proposed_total}.  "
        f"Sandbox has {len(opens)} open proposals."
    )
    if skip_reasons:
        _eprint("Skip reasons:")
        for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1]):
            _eprint(f"  {count:>4}  {reason}")
    return 0


# -- inspect ----------------------------------------------------------------

def cmd_inspect(args) -> int:
    """Print a human-readable summary of sandbox contents.  Read-only."""
    # Bootstrap plugins so the encryption policy resolves correctly --
    # without this, get_encryptor() returns a degenerate encryptor with
    # _keyring=None and ProposalsStore._read_lines crashes on decrypt
    # with "'NoneType' object has no attribute 'get_dek'".
    _bootstrap_plugins()
    from app.storage.proposals import ProposalsStore

    sandbox_dir = Path(args.source).expanduser()
    if not (sandbox_dir / "probationary.jsonl").exists():
        _eprint(f"No sandbox at {sandbox_dir}")
        return 1

    sandbox = ProposalsStore(memory_dir=sandbox_dir)
    opens = sandbox.list_open()
    if not opens:
        _eprint("Sandbox is empty (no open proposals).")
        return 0

    # Bucket by layer for readability
    by_layer: dict = {}
    for p in opens:
        by_layer.setdefault(p.get("layer", "?"), []).append(p)

    _eprint(f"Sandbox at {sandbox_dir}: {len(opens)} open proposals\n")
    for layer in sorted(by_layer):
        entries = by_layer[layer]
        _eprint(f"  [{layer}]  ({len(entries)})")
        if args.layer and layer != args.layer:
            continue
        # Sort by corroborations desc so most-attested rises to top
        entries.sort(key=lambda r: r.get("corroborations", 0), reverse=True)
        for entry in entries[:args.limit]:
            corr = entry.get("corroborations", 0)
            tags = entry.get("tags", []) or []
            content = (entry.get("content") or "").replace("\n", " ")
            tag_str = f"  [{','.join(tags[:3])}]" if tags else ""
            _eprint(f"    corr={corr}{tag_str}  {content[:140]}")
    return 0


# -- commit -----------------------------------------------------------------

async def cmd_commit(args) -> int:
    """Copy sandbox proposals into the real probationary store.

    Each sandbox proposal is re-added to the real store as a fresh
    ``record`` event with activity_count_at_proposal stamped to the
    current real counter.  IDs are content-hash stable so duplicates
    against the real store automatically become corroboration events.
    """
    _bootstrap_plugins()  # for any reference-layer machinery

    from app.storage.proposals import ProposalsStore, get_proposals_store
    from app.models.memory import MemoryProposal, MemoryReference
    from app.utils.memory_extractor import _next_activity_count

    sandbox_dir = Path(args.source).expanduser()
    if not (sandbox_dir / "probationary.jsonl").exists():
        _eprint(f"No sandbox at {sandbox_dir}")
        return 1

    sandbox = ProposalsStore(memory_dir=sandbox_dir)
    real = get_proposals_store()

    sandbox_opens = sandbox.list_open()
    if not sandbox_opens:
        _eprint("Sandbox has no open proposals; nothing to commit.")
        return 0

    if not args.yes:
        _eprint(f"About to commit {len(sandbox_opens)} proposals to real store at "
                f"~/.ziya/memory/probationary.jsonl.")
        _eprint("Re-run with --yes to actually commit.")
        return 1

    # Bump the real activity counter once for the whole commit batch.  All
    # promoted proposals share the same activity_count, which is fine --
    # they all "arrived" at the same moment from the user's perspective.
    activity = _next_activity_count()

    committed = 0
    failed = 0
    for s in sandbox_opens:
        try:
            kwargs = dict(
                content=s.get("content", ""),
                layer=s.get("layer", "domain_context"),
                tags=s.get("tags", []) or [],
                # Distinguishable provenance so the user can audit later
                learned_from="primed_from_sandbox",
                conversation_id=s.get("conversation_id"),
            )
            proposal = MemoryProposal(**kwargs)
            scope = s.get("scope") or {}
            paths = scope.get("project_paths") if isinstance(scope, dict) else None
            if paths:
                proposal.scope.project_paths = paths
            ref = s.get("reference")
            if ref:
                proposal.reference = MemoryReference(**ref)
            real.add(proposal, activity_count=activity)
            committed += 1
        except Exception as e:
            _eprint(f"Failed to commit {s.get('id', '?')}: {e}")
            failed += 1

    _eprint(f"Committed {committed} proposals to real store.  {failed} failed.")
    if failed == 0 and committed > 0:
        _eprint(f"Run 'python {Path(__file__).name} clear --source {sandbox_dir} --yes' "
                f"to drop the sandbox now that it's been committed.")
    return 0 if failed == 0 else 2


# -- clear ------------------------------------------------------------------

def cmd_clear(args) -> int:
    """Drop sandbox jsonl AND orphaned prop_* embeddings from the global cache.

    The embedding cache is shared between sandbox and real, so we have
    to pull out the prop_* IDs that exist in the sandbox but not in the
    real probationary store.  This is conservative: we only remove
    embeddings whose ID appears in the sandbox event log and does NOT
    appear in the real store -- so committed proposals keep their
    embeddings.
    """
    _bootstrap_plugins()  # Same encryption-policy reason as cmd_inspect
    from app.storage.proposals import ProposalsStore, get_proposals_store

    sandbox_dir = Path(args.source).expanduser()
    sandbox_path = sandbox_dir / "probationary.jsonl"
    if not sandbox_path.exists():
        _eprint(f"No sandbox at {sandbox_dir}")
        return 0

    sandbox = ProposalsStore(memory_dir=sandbox_dir)
    real = get_proposals_store()

    sandbox_ids = {r.get("id") for r in sandbox.list_all() if r.get("id")}
    real_ids = {r.get("id") for r in real.list_all() if r.get("id")}
    orphan_ids = {pid for pid in sandbox_ids
                  if pid and pid.startswith("prop_") and pid not in real_ids}

    if not args.yes:
        _eprint(f"Sandbox at {sandbox_dir}: {len(sandbox_ids)} proposals, "
                f"{len(orphan_ids)} orphan embeddings to remove.")
        _eprint("Re-run with --yes to actually clear.")
        return 1

    # Remove orphan embeddings from the global cache
    removed_embeds = 0
    try:
        from app.services.embedding_service import get_embedding_cache
        cache = get_embedding_cache()
        for pid in orphan_ids:
            try:
                cache.remove(pid)
                removed_embeds += 1
            except Exception as e:
                _eprint(f"  embedding remove failed for {pid}: {e}")
        cache.flush()
    except Exception as e:
        _eprint(f"Embedding cleanup skipped (non-fatal): {e}")

    # Drop sandbox file(s).  Use unlink rather than rmtree so we don't
    # blow away anything else the user may have placed in the dir.
    try:
        sandbox_path.unlink()
    except FileNotFoundError:
        pass
    embed_file = sandbox_dir / "embeddings.npz"
    if embed_file.exists():
        embed_file.unlink()
    counter_file = sandbox_dir / "activity_counter.json"
    if counter_file.exists():
        counter_file.unlink()

    _eprint(f"Cleared sandbox.  Removed {removed_embeds} orphan embeddings.")
    return 0


# -- main -------------------------------------------------------------------

DEFAULT_SANDBOX = "~/.ziya/memory/priming-sandbox"


def main() -> int:
    p = argparse.ArgumentParser(description="Retroactive memory priming sandbox.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_prime = sub.add_parser("prime", help="Walk historical conversations into sandbox")
    p_prime.add_argument("--max", type=int, default=None,
                         help="Process at most this many conversations")
    p_prime.add_argument("--seed", type=int, default=None,
                         help="Random seed for sampling (only matters with --max)")
    p_prime.add_argument("--source", default=DEFAULT_SANDBOX,
                         help=f"Sandbox path (default: {DEFAULT_SANDBOX})")

    p_inspect = sub.add_parser("inspect", help="Print summary of sandbox proposals")
    p_inspect.add_argument("--source", default=DEFAULT_SANDBOX)
    p_inspect.add_argument("--limit", type=int, default=20,
                           help="Max entries to print per layer (default 20)")
    p_inspect.add_argument("--layer", default=None,
                           help="Show only this layer in detail")

    p_commit = sub.add_parser("commit", help="Copy sandbox proposals into real store")
    p_commit.add_argument("--source", default=DEFAULT_SANDBOX)
    p_commit.add_argument("--yes", action="store_true",
                          help="Confirm the commit (otherwise dry-run)")

    p_clear = sub.add_parser("clear", help="Drop sandbox state")
    p_clear.add_argument("--source", default=DEFAULT_SANDBOX)
    p_clear.add_argument("--yes", action="store_true",
                         help="Confirm the clear (otherwise dry-run)")

    args = p.parse_args()

    if args.cmd == "prime":
        return asyncio.run(cmd_prime(args))
    if args.cmd == "inspect":
        return cmd_inspect(args)
    if args.cmd == "commit":
        return asyncio.run(cmd_commit(args))
    if args.cmd == "clear":
        return cmd_clear(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
