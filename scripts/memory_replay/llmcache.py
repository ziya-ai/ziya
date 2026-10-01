"""Record/replay cache for service-model calls and embeddings.

Wraps ``app.services.model_resolver.call_service_model`` and the embedding
provider (``app.services.embedding_service._provider``).

Key = sha256 of
  * ("llm", category, resolved endpoint, resolved model id, system prompt,
    user message, max_tokens, temperature), or
  * ("embed", embedding model id, dim, text as sent (first 8000 chars)).

Modes:
  * ``record`` -- hit: use it; miss: live call, store, use.
  * ``strict`` -- hit: use it; miss: raise ``CacheMiss`` (a BaseException, so
    memory code's ``except Exception`` cannot turn a miss into a silent
    degradation).  Misses are also counted, and the engine aborts on any.

Storage: one file per key, ``<root>/<ns>/<key[:2]>/<key>.enc``, the value JSON
encrypted with ``app.utils.encryption.get_encryptor()`` (category
session_data).  Creation is exclusive: the value is written to a private temp
file and published with ``os.link`` (fails with EEXIST exactly like
``O_CREAT|O_EXCL``, but a reader can never observe a half-written file).  If
another process published the key first, our live response is DISCARDED and
theirs is used, so concurrent arms sharing a namespace always see one
response per key.  Namespaces are directories: an A/A arm uses its own.

Two kinds of live failure, handled differently:

  * a model-side failure (validation, a refused prompt) is retried 3 times,
    then cached as an error entry and re-raised as RuntimeError on replay,
    so a record run and its strict replay take the same code path;
  * an INFRASTRUCTURE failure (credentials, network, throttling, or an
    embedding call that returned None) is never cached.  It is waited out
    for up to ``infra_wait_s`` and then raised as ReplayInfraError, which
    the engine turns into "stop at the last checkpoint, exit 5".  Entries
    recorded as outages by older versions are dropped on read.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np


class CacheMiss(BaseException):
    """Strict mode: a key was not in the cache."""


class ReplayInfraError(BaseException):
    """Live infrastructure (credentials, network, throttling) stayed
    unavailable past the wait window.

    A BaseException for the same reason as CacheMiss: memory code wraps
    model calls in ``except Exception`` and fails open (contradiction
    classify -> AGREES, extraction -> nothing), so an ordinary exception
    would be swallowed and the replay would silently take a code path that
    production only takes when the network is down.  Never cached.
    """


# An infrastructure failure says nothing about the model, so it must never
# become a cache entry.  Until 2026-09-27 it was: after 3 quick retries any
# persistent failure was stored as {"kind": "error"} and replayed forever,
# and 21 CredentialRetrievalError entries (credential expiry at 07:54, a
# train dropout at 18:36) were served back to every arm sharing the
# namespace -- invisibly, since the memory code fails open.
# One classifier for production and replay (app/utils/infra_errors.py), so
# what the harness refuses to cache is exactly what production pauses on.
# Loaded by PATH from the live tree: an arm's exported ``app`` package
# (e.g. the pre-session baseline) shadows the package and lacks the module.
def _load_infra_errors():
    import importlib.util
    _here = Path(__file__).resolve()
    _p = _here.parent.parent.parent / "app" / "utils" / "infra_errors.py"
    spec = importlib.util.spec_from_file_location("_replay_infra_errors", _p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ie = _load_infra_errors()
_INFRA_TYPES, _INFRA_MSG, is_infra_error = _ie.INFRA_TYPES, _ie.INFRA_MESSAGE, _ie.is_infra_error


def is_infra_entry(entry: Optional[Dict[str, Any]]) -> bool:
    """A cached value that is really a recorded outage: an ``error`` entry of
    an infra type/message, or an embedding recorded as ``none`` (the live
    provider returns None only when its call failed)."""
    if not entry:
        return False
    if entry.get("kind") == "none":
        return True
    return entry.get("kind") == "error" and (
        entry.get("type") in _INFRA_TYPES or bool(_INFRA_MSG.search(entry.get("msg") or "")))


class LLMCache:
    def __init__(self, root: Path, ns: str = "main", mode: str = "record",
                 retries: int = 3, backoff_s: float = 2.0,
                 infra_wait_s: float = 900.0, infra_backoff_max_s: float = 60.0) -> None:
        if mode not in ("record", "strict"):
            raise ValueError(f"cache mode {mode!r}")
        self.dir = Path(root) / ns
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ns, self.mode = ns, mode
        self.retries, self.backoff_s = retries, backoff_s
        # An outage is waited out (patchy train wifi, an mwinit away) up to
        # infra_wait_s per call before the chunk stops with ReplayInfraError.
        self.infra_wait_s, self.infra_backoff_max_s = infra_wait_s, infra_backoff_max_s
        self.stats: Dict[str, int] = {"llm_hit": 0, "llm_live": 0, "embed_hit": 0, "embed_live": 0,
                                      "miss": 0, "race_lost": 0, "errors_cached": 0,
                                      "infra_retries": 0, "infra_abort": 0,
                                      "poisoned_dropped": 0}
        self.last_infra_error: str = ""
        self._lock = threading.Lock()
        from app.utils.encryption import get_encryptor
        self._enc = get_encryptor()
        if not self._enc.is_enabled("session_data"):
            raise RuntimeError("ALE encryption is not enabled; refusing to write a plaintext LLM cache")
        self._undo: list = []
        self._resolved: Dict[str, Dict[str, Any]] = {}

    # -- storage -----------------------------------------------------------

    @staticmethod
    def key(parts: list) -> str:
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.enc"

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        p = self._path(key)
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            return None
        val = json.loads(self._enc.decrypt(raw))
        if is_infra_entry(val):
            # A recorded outage is not an answer.  Record mode drops it so the
            # key is re-fetched live; strict mode treats it as a miss.
            with self._lock:
                self.stats["poisoned_dropped"] += 1
            if self.mode == "record":
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            return None
        return val

    def _infra_wait(self, exc: Optional[BaseException], started: float, attempt: int) -> float:
        """Seconds to sleep before retrying an outage, or raise ReplayInfraError
        once ``infra_wait_s`` has elapsed since the first failure."""
        detail = f"{type(exc).__name__}: {str(exc)[:300]}" if exc is not None else "embedding returned None"
        self.last_infra_error = detail
        remaining = self.infra_wait_s - (time.monotonic() - started)
        if remaining <= 0:
            with self._lock:
                self.stats["infra_abort"] += 1
            raise ReplayInfraError(detail)
        with self._lock:
            self.stats["infra_retries"] += 1
        # Never sleep past the window, so the last retry lands at its edge.
        return max(0.0, min(self.infra_backoff_max_s,
                            self.backoff_s * (2 ** min(attempt, 8)), remaining))

    def put(self, key: str, value: Dict[str, Any]) -> Dict[str, Any]:
        """Publish ``value`` unless the key exists; return the winning value."""
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = self._enc.encrypt(json.dumps(value, ensure_ascii=False).encode("utf-8"), "session_data")
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, p)
                return value
            except FileExistsError:
                with self._lock:
                    self.stats["race_lost"] += 1
                return self.get(key)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    def _miss(self, what: str) -> None:
        with self._lock:
            self.stats["miss"] += 1
        raise CacheMiss(f"strict cache miss ({what}) in namespace {self.ns!r}")

    # -- LLM ---------------------------------------------------------------

    def _resolve(self, category: str) -> Dict[str, Any]:
        if category not in self._resolved:
            from app.services.model_resolver import resolve_service_model
            cfg = resolve_service_model(category)
            self._resolved[category] = {"endpoint": cfg.get("endpoint"), "model_id": cfg.get("model_id")}
        return self._resolved[category]

    def wrap_call(self, live: Callable[..., Any]) -> Callable[..., Any]:
        cache = self

        async def call_service_model(category: str, system_prompt: str, user_message: str,
                                     max_tokens: int = 2048, temperature: float = 0.2) -> str:
            r = cache._resolve(category)
            k = cache.key(["llm", category, r["endpoint"], r["model_id"], system_prompt,
                           user_message, int(max_tokens), float(temperature)])
            hit = cache.get(k)
            if hit is None:
                if cache.mode == "strict":
                    cache._miss(f"llm {category}")
                val = None
                err = None
                attempt = 0          # model-error attempts
                infra_n = 0          # outage retries
                infra_t0 = None
                while True:
                    try:
                        text = await live(category=category, system_prompt=system_prompt,
                                          user_message=user_message, max_tokens=max_tokens,
                                          temperature=temperature)
                        val = {"kind": "text", "text": text if isinstance(text, str) else str(text)}
                        break
                    except Exception as e:  # noqa: BLE001 - classified below
                        err = e
                        if is_infra_error(e):
                            # Wait the outage out; never cached (raises past the window).
                            infra_t0 = time.monotonic() if infra_t0 is None else infra_t0
                            await asyncio.sleep(cache._infra_wait(e, infra_t0, infra_n))
                            infra_n += 1
                            continue
                        attempt += 1
                        if attempt >= cache.retries:
                            break
                        await asyncio.sleep(cache.backoff_s * (4 ** (attempt - 1)))
                if val is None:
                    # A deterministic model-side failure (e.g. validation):
                    # recorded so a strict replay takes the same path.
                    val = {"kind": "error", "type": type(err).__name__, "msg": str(err)[:500]}
                    with cache._lock:
                        cache.stats["errors_cached"] += 1
                hit = cache.put(k, val)
                with cache._lock:
                    cache.stats["llm_live"] += 1
            else:
                with cache._lock:
                    cache.stats["llm_hit"] += 1
            if hit.get("kind") == "error":
                raise RuntimeError(f"(replayed) {hit.get('type')}: {hit.get('msg')}")
            return hit["text"]

        call_service_model.__replay_cache__ = True
        return call_service_model

    # -- embeddings --------------------------------------------------------

    def wrap_provider(self, provider: Any) -> Any:
        return CachedEmbeddingProvider(provider, self)

    # -- install -----------------------------------------------------------

    def install(self, live_call: Optional[Callable[..., Any]] = None,
                provider: Optional[Any] = None) -> None:
        """Patch model_resolver.call_service_model (and any module-level
        binding of it) and the embedding provider singleton."""
        import sys
        import app.services.model_resolver as mr
        import app.services.embedding_service as es
        orig = mr.call_service_model
        wrapped = self.wrap_call(live_call or orig)
        self._undo.append((mr, "call_service_model", orig))
        mr.call_service_model = wrapped
        for name, mod in list(sys.modules.items()):
            if mod is not None and name.startswith("app.") and vars(mod).get("call_service_model") is orig \
                    and mod is not mr:
                self._undo.append((mod, "call_service_model", orig))
                setattr(mod, "call_service_model", wrapped)
        base = provider if provider is not None else es.get_embedding_provider()
        self._undo.append((es, "_provider", es._provider))
        # Embeddings off (NoopProvider) stays off: wrapping it would defeat
        # callers' isinstance(provider, NoopProvider) short-circuits.
        es._provider = base if type(base).__name__ == "NoopProvider" else self.wrap_provider(base)

    def uninstall(self) -> None:
        for mod, attr, val in reversed(self._undo):
            setattr(mod, attr, val)
        self._undo.clear()


class CachedEmbeddingProvider:
    """Duck-types EmbeddingProvider; NOT a NoopProvider subclass, so callers'
    ``isinstance(provider, NoopProvider)`` checks behave as for the real one."""

    def __init__(self, inner: Any, cache: LLMCache) -> None:
        self.inner, self.cache = inner, cache
        self.model_id = getattr(inner, "_model_id", type(inner).__name__)
        # A NoopProvider underneath means embeddings are off in production
        # too; keep that visible to the engine.
        self.is_noop = type(inner).__name__ == "NoopProvider"

    @property
    def dim(self) -> int:
        return self.inner.dim

    @property
    def _dim(self) -> int:  # BedrockTitanProvider attribute some callers read
        return self.inner.dim

    def embed_text(self, text: str) -> Optional[np.ndarray]:
        if not text or not text.strip():
            return None
        sent = text[:8000]
        k = self.cache.key(["embed", self.model_id, int(self.inner.dim), sent])
        hit = self.cache.get(k)
        if hit is None:
            if self.cache.mode == "strict":
                self.cache._miss("embed")
            # The live provider returns None only when its call failed, so a
            # None is an outage: waited out, never cached ({"kind": "none"}
            # used to be stored and replayed as "no embedding" forever).
            vec = None
            t0 = time.monotonic()
            n = 0
            while vec is None:
                try:
                    vec = self.inner.embed_text(text)
                except Exception as e:  # noqa: BLE001
                    if not is_infra_error(e):
                        raise
                    time.sleep(self.cache._infra_wait(e, t0, n))
                    n += 1
                    continue
                if vec is None:
                    time.sleep(self.cache._infra_wait(None, t0, n))
                    n += 1
            val = {"kind": "vec", "dtype": "float32",
                   "b64": base64.b64encode(np.asarray(vec, dtype=np.float32).tobytes()).decode()}
            hit = self.cache.put(k, val)
            with self.cache._lock:
                self.cache.stats["embed_live"] += 1
        else:
            with self.cache._lock:
                self.cache.stats["embed_hit"] += 1
        if hit["kind"] == "none":
            return None
        return np.frombuffer(base64.b64decode(hit["b64"]), dtype=np.float32).copy()

    def embed_batch(self, texts):
        return [self.embed_text(t) for t in texts]
