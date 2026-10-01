"""Sandbox: redirect every memory-subsystem seam into a per-run directory.

Every place the memory subsystem reaches ``~/.ziya`` (listed in
``HOME_SEAMS`` and recorded in state.json facts.home_seams) is redirected or
neutralised here, in three layers:

 1. **Caller-scoped home redirect.**  ``app.utils.paths.get_ziya_home`` (and
    every module-level binding of it in a memory module, e.g.
    ``app.agents.system_jobs``) is replaced by a function that returns the
    sandbox home when the *calling module* is a memory module, and the real
    home otherwise.  So the activity counter, feedback stats, cleanup state,
    organize history, domain clocks, store constructors, embedding cache and
    system-job state all land in the sandbox -- including seams nobody listed
    and seams in the baseline tree -- while chat records, the keyring and
    plugin config are still read from the real home.
 2. **Explicit instance / path swaps.**  The singletons
    (``app.storage.memory._instance``, ``app.storage.proposals._instance``,
    ``app.services.embedding_service._cache``), ``memory_clock._default_path``
    and ``tool_audit_log._LOG_DIR`` are set to sandbox objects, and the audit
    writers (``log_security_event``, ``log_tool_execution``) are replaced by
    counters that keep only event names.
 3. **Write guard.**  A ``sys.addaudithook`` hook refuses (PermissionError)
    and records any open-for-write / rename / replace / remove / mkdir under
    the real ``~/.ziya`` while the sandbox is active.  Memory code swallows
    exceptions, so the refusal alone would be silent: ``check()`` raises
    ``SandboxViolation`` if anything was refused, and the engine calls it
    after every event.

On exit the sha256 of EVERY file under the real ``~/.ziya/memory`` is
compared with the value at entry; any change raises ``SandboxViolation``.
"""
from __future__ import annotations

import hashlib
import importlib
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# (seam, file or attribute, how it is handled).  Recorded in state.json.
HOME_SEAMS = [
    ("app/storage/memory.py MemoryStorage.__init__", "memory/memories.json, mindmap.json, proposals.json, profile.json",
     "caller-scoped get_ziya_home + _instance swapped to sandbox MemoryStorage"),
    ("app/storage/proposals.py ProposalsStore.__init__", "memory/probationary.jsonl",
     "caller-scoped get_ziya_home + _instance swapped to sandbox ProposalsStore"),
    ("app/services/embedding_service.py get_embedding_cache", "memory/embeddings.npz",
     "caller-scoped get_ziya_home + _cache swapped; _provider wrapped by llmcache"),
    ("app/memory/extractor.py _next_activity_count", "memory/activity_counter.json", "caller-scoped get_ziya_home"),
    ("app/memory/lifecycle.py current_activity_count", "memory/activity_counter.json", "caller-scoped get_ziya_home"),
    ("app/memory/feedback.py _stats_file", "memory/feedback_stats.jsonl", "caller-scoped get_ziya_home"),
    ("app/memory/maintenance.py _get_cleanup_state_file", "memory/.cleanup_state.json", "caller-scoped get_ziya_home"),
    ("app/memory/organize_history.py _history_file", "memory/organize_history.json", "caller-scoped get_ziya_home"),
    ("app/utils/memory_clock.py _path", "memory/domain_clocks.json",
     "_default_path set to sandbox (current arm only; absent in baseline)"),
    ("app/agents/system_jobs.py _state_path (module-level get_ziya_home import)", "system_jobs.json",
     "module binding replaced by caller-scoped get_ziya_home"),
    ("app/utils/tool_audit_log.py _ensure_log_dir / log_security_event / log_tool_execution", "audit/*.jsonl",
     "_LOG_DIR -> sandbox/audit; writers replaced by name-only counters"),
    ("app/memory/prompt.py _audit_memory_injection", "audit/ via log_security_event", "neutralised (writer patched)"),
    ("app/storage/memory.py save (NF-010 memory_created)", "audit/ via log_security_event", "neutralised (writer patched)"),
    ("app/mcp/encoding_scanner.py scan_and_log", "audit/ via log_security_event", "neutralised (writer patched)"),
    ("app/memory/eval.py VERDICT_CACHE_DIR / projects_dir (Path.home())", "memory-eval/, projects/",
     "not reached by the replay (eval-only); write guard would refuse"),
    ("any other write under ~/.ziya", "*", "sys.addaudithook write guard refuses and records -> run aborts"),
]

# Modules whose get_ziya_home() calls are redirected to the sandbox home.
REDIRECT_MODULES = (
    "app.memory", "app.storage.memory", "app.storage.proposals",
    "app.utils.memory_clock", "app.utils.memory_collections",
    "app.services.embedding_service", "app.agents.system_jobs",
    "app.utils.tool_audit_log", "app.mcp.tools.memory_tools",
    "app.models.memory", "app.mcp.encoding_scanner",
)


class SandboxViolation(BaseException):
    """A write reached the real ~/.ziya, or its memory hashes changed.
    BaseException so memory code's ``except Exception`` cannot swallow it."""


class ExternalStoreChange(BaseException):
    """The real ~/.ziya/memory changed while the replay ran, but not by this
    process.  Still ends the run non-zero (after its checkpoint)."""


def _redirected(modname: str) -> bool:
    return any(modname == p or modname.startswith(p + ".") for p in REDIRECT_MODULES)


def hash_tree(root: Path) -> Dict[str, str]:
    """sha256 of every file under ``root`` (relative path -> hex)."""
    out: Dict[str, str] = {}
    root = Path(root)
    if not root.exists():
        return out
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for f in sorted(fn):
            p = Path(dp) / f
            try:
                out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError as e:
                out[str(p.relative_to(root))] = f"unreadable:{e.errno}"
    return out


# -- write guard ------------------------------------------------------------

class _Guard:
    active = False
    real_home: Optional[str] = None
    allow: Tuple[str, ...] = ()
    violations: List[Tuple[str, str]] = []
    installed = False
    lock = threading.Lock()


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _under_home(p: Any) -> Optional[str]:
    if _Guard.real_home is None or isinstance(p, int) or p is None:
        return None
    try:
        s = os.fsdecode(p)
    except TypeError:
        return None
    try:
        r = os.path.realpath(os.path.abspath(s))
    except Exception:
        return None
    if r == _Guard.real_home or r.startswith(_Guard.real_home + os.sep):
        if any(r == a or r.startswith(a + os.sep) for a in _Guard.allow):
            return None
        return r
    return None


def _audit_hook(event: str, args: tuple) -> None:
    if not _Guard.active:
        return
    hit = None
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                  (isinstance(flags, int) and flags & _WRITE_FLAGS)
        if writing:
            hit = _under_home(path)
    elif event in ("os.rename", "os.replace", "os.link", "os.symlink"):
        hit = _under_home(args[0]) or _under_home(args[1])
    elif event == "os.mkdir":
        # mkdir(exist_ok=True) of an existing directory changes nothing.
        hit = _under_home(args[0])
        if hit and os.path.isdir(hit):
            hit = None
    elif event == "os.chmod":
        # chmod to the mode a path already has changes nothing.
        hit = _under_home(args[0])
        try:
            if hit and (os.stat(hit).st_mode & 0o7777) == (int(args[1]) & 0o7777):
                hit = None
        except (OSError, TypeError, ValueError):
            pass
    elif event in ("os.remove", "os.rmdir", "os.truncate",
                   "os.chown", "os.utime", "shutil.rmtree", "shutil.copyfile", "shutil.move"):
        hit = _under_home(args[0]) if event not in ("shutil.copyfile", "shutil.move") \
            else _under_home(args[1])
    if hit:
        with _Guard.lock:
            _Guard.violations.append((event, hit))
        raise PermissionError(f"memory-replay sandbox: write under real ~/.ziya refused: {event} {hit}")


def _install_hook() -> None:
    if not _Guard.installed:
        sys.addaudithook(_audit_hook)
        _Guard.installed = True


# -- sandbox ----------------------------------------------------------------

class Sandbox:
    """Context manager.  ``home`` is the sandbox home (its ``memory/`` is the
    sandbox store); ``real_home`` defaults to the real Ziya home."""

    def __init__(self, home: Path, real_home: Optional[Path] = None,
                 guard: bool = True) -> None:
        self.home = Path(home)
        self.memory_dir = self.home / "memory"
        if real_home is None:
            from app.utils.paths import get_ziya_home
            real_home = get_ziya_home()
        self.real_home = Path(os.path.realpath(real_home))
        self.guard = guard
        self.audit_counts: Dict[str, int] = {}
        self._undo: List[Tuple[Any, str, Any]] = []
        self.hashes_before: Dict[str, str] = {}
        self.stores: Dict[str, Any] = {}

    # attribute swap with undo
    def _set(self, obj: Any, attr: str, val: Any) -> None:
        self._undo.append((obj, attr, getattr(obj, attr, _MISSING)))
        setattr(obj, attr, val)

    def check(self) -> None:
        if _Guard.violations:
            v = list(_Guard.violations)
            raise SandboxViolation(f"writes under real ~/.ziya were attempted: {v[:5]} (+{max(0, len(v) - 5)})")

    def _real_memory_ids(self) -> set:
        """Memory ids in the REAL memories.json (read-only decrypt)."""
        p = self.real_home / "memory" / "memories.json"
        try:
            raw = p.read_bytes()
            from app.utils.encryption import get_encryptor, is_encrypted
            if is_encrypted(raw):
                raw = get_encryptor().decrypt(raw)
            data = __import__("json").loads(raw)
            rows = data.get("memories", []) if isinstance(data, dict) else data
            return {r.get("id") for r in rows or [] if isinstance(r, dict)}
        except Exception:
            return set()

    def own_memory_ids(self) -> set:
        """Every memory id this sandbox store holds (replay-minted ids come
        from a seeded uuid4, so they cannot occur in the real store unless
        the replay wrote there)."""
        try:
            return {r.get("id") for r in self.stores["memory"]._load_memories()}
        except Exception:
            return set()

    def assert_memory_unchanged(self) -> None:
        """Before/after sha256 of every file under the real ~/.ziya/memory.

        Any change is reported loudly.  It is attributed to THIS process (and
        raised as SandboxViolation) when the write guard refused anything or
        a replay-minted memory id appears in the real store; otherwise the
        writer was another process -- the user's live Ziya server shares the
        home -- and ExternalStoreChange is raised so the run still exits
        non-zero after its checkpoint."""
        after = hash_tree(self.real_home / "memory")
        if after != self.hashes_before:
            changed = sorted(k for k in set(after) | set(self.hashes_before)
                             if after.get(k) != self.hashes_before.get(k))
            ours = (self._real_memory_ids() - self.real_ids_before) & self.own_memory_ids()
            sys.stderr.write(f"\n*** real ~/.ziya/memory changed during replay: {changed} ***\n")
            self.external_change = {"changed": changed, "replay_ids_in_real_store": len(ours),
                                    "guard_refusals": len(_Guard.violations)}
            if ours or _Guard.violations:
                sys.stderr.write("*** CRITICAL: attributed to THIS replay process ***\n")
                raise SandboxViolation(f"real ~/.ziya/memory changed by the replay: {changed}")
            sys.stderr.write("*** attributed to another process (guard refused 0 writes; 0 replay-minted "
                             "ids in the real store): the live Ziya server writes this home ***\n")
            raise ExternalStoreChange(self.external_change)

    def __enter__(self) -> "Sandbox":
        self.hashes_before = hash_tree(self.real_home / "memory")
        self.real_ids_before = self._real_memory_ids()
        self.external_change = None
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        sb_home, real = self.home, self.real_home

        import app.utils.paths as paths
        orig = paths.get_ziya_home
        self._orig_home = orig

        def get_ziya_home():  # noqa: D401 - replaces app.utils.paths.get_ziya_home
            f = sys._getframe(1)
            if _redirected(f.f_globals.get("__name__", "")):
                return sb_home
            return orig()

        get_ziya_home.__replay_redirect__ = True
        self._set(paths, "get_ziya_home", get_ziya_home)
        for name, mod in list(sys.modules.items()):
            if mod is not None and _redirected(name) and getattr(mod, "get_ziya_home", None) is orig:
                self._set(mod, "get_ziya_home", get_ziya_home)

        # Explicit singletons -> sandbox instances.
        from app.storage.memory import MemoryStorage
        import app.storage.memory as sm
        import app.storage.proposals as sp
        import app.services.embedding_service as es
        from app.storage.proposals import ProposalsStore
        from app.services.embedding_service import EmbeddingCache
        from app.config.env_registry import ziya_env
        self.stores["memory"] = MemoryStorage(memory_dir=self.memory_dir)
        self.stores["proposals"] = ProposalsStore(memory_dir=self.memory_dir)
        self.stores["embeddings"] = EmbeddingCache(self.memory_dir,
                                                   dim=ziya_env("ZIYA_EMBEDDING_DIM", default=es.DEFAULT_DIM))
        self._set(sm, "_instance", self.stores["memory"])
        self._set(sp, "_instance", self.stores["proposals"])
        self._set(es, "_cache", self.stores["embeddings"])

        try:
            mc = importlib.import_module("app.utils.memory_clock")
            self._set(mc, "_default_path", self.memory_dir / getattr(mc, "CLOCK_FILE", "domain_clocks.json"))
        except ImportError:
            pass

        # Audit writers: count event names only, never write.
        import app.utils.tool_audit_log as tal
        (self.home / "audit").mkdir(parents=True, exist_ok=True)
        self._set(tal, "_LOG_DIR", self.home / "audit")
        counts = self.audit_counts

        def _count(kind: str) -> Callable[..., None]:
            def _w(*a: Any, **k: Any) -> None:
                name = str(a[0]) if a else str(k.get("event_type") or k.get("tool_name") or kind)
                counts[name] = counts.get(name, 0) + 1
            return _w

        originals = {a: getattr(tal, a) for a in ("log_security_event", "log_tool_execution")
                     if hasattr(tal, a)}
        for attr in originals:
            self._set(tal, attr, _count(attr))
        # Module-level ``from ... import log_security_event`` bindings.
        for name, mod in list(sys.modules.items()):
            if mod is None or mod is tal or not name.startswith("app."):
                continue
            for attr, fn in originals.items():
                if vars(mod).get(attr) is fn:
                    self._set(mod, attr, getattr(tal, attr))

        # Memory tools are enabled in production for this user.
        import app.mcp.builtin_tools as bt
        orig_enabled = bt.is_builtin_category_enabled
        self._set(bt, "is_builtin_category_enabled",
                  lambda cat, *a, **k: True if cat == "memory" else orig_enabled(cat, *a, **k))

        if self.guard:
            _install_hook()
            _Guard.real_home = str(self.real_home)
            _Guard.violations = []
            _Guard.active = True
        return self

    def __exit__(self, et, ev, tb) -> None:
        _Guard.active = False
        try:
            es = sys.modules.get("app.services.embedding_service")
            if es is not None and es._cache is not None:
                es._cache.flush()
        except Exception:
            pass
        for obj, attr, val in reversed(self._undo):
            if val is _MISSING:
                try:
                    delattr(obj, attr)
                except AttributeError:
                    pass
            else:
                setattr(obj, attr, val)
        self._undo.clear()
        violations = list(_Guard.violations)
        self.assert_memory_unchanged()
        if violations and et is None:
            raise SandboxViolation(f"writes under real ~/.ziya were attempted: {violations[:5]}")


class _Missing:
    pass


_MISSING = _Missing()
