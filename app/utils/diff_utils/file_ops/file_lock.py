"""
Per-target-file locking for the diff-application pipelines.

PenPal #124 [CWE-667]: ``/api/apply-changes`` and ``/api/unapply-changes``
both dispatch their pipeline via ``run_in_threadpool``, so two concurrent
requests against the same file execute the unlocked read-modify-write cycle
on separate OS threads *even under the default single-worker uvicorn* — the
last writer silently clobbers the first, corrupting the developer's source
file with a 200 OK on both requests.  (Under ``uvicorn --workers N`` the
same interleaving happens across processes.)

This module provides a single shared lock keyed on the target file's real
path.  Both ``apply_diff_pipeline`` and ``apply_reverse_diff_pipeline`` wrap
their read-modify-write body with it.

Design notes:
  * ``is_singleton=True`` is load-bearing.  The reverse pipeline's Stage 4
    (``_try_reversed_diff_full_pipeline``) calls ``apply_diff_pipeline`` on
    the SAME thread and SAME file, so the two wraps re-enter the same lock.
    filelock is reentrant only on a single instance; the singleton makes the
    two call sites share one instance, so the nested acquire re-enters
    (thread-local counter) instead of self-deadlocking on the timeout.
    Cross-thread / cross-process acquires still block — verified empirically.
  * The lock file lives under the system temp dir keyed by a hash of the
    target's ``realpath`` — NOT ``<file>.lock`` next to the source (which
    would litter committable ``.lock`` files through the project tree).
    ``realpath`` (not ``abspath``) so two paths to the same inode via a
    symlink collapse to one lock.
  * A 10s timeout prevents a crashed holder from deadlocking the pipeline
    forever; mirrors the ``token_calibrator.py`` FileLock precedent.
"""

import hashlib
import os
import tempfile

from filelock import FileLock

# Fixed timeout at every construction: is_singleton raises ValueError if the
# same lock path is ever constructed with differing args, so this constant
# MUST be the only timeout used for these locks.
_LOCK_TIMEOUT_SECONDS = 10
_LOCK_DIR = os.path.join(tempfile.gettempdir(), "ziya_diff_locks")


def diff_file_lock(file_path: str) -> FileLock:
    """Return the shared reentrant lock guarding *file_path*'s pipeline R-M-W.

    Same-thread nested acquisition (reverse pipeline → Stage 4 forward
    pipeline) re-enters; concurrent threads/processes block until release.
    """
    try:
        os.makedirs(_LOCK_DIR, exist_ok=True)
    except OSError:
        pass
    key = hashlib.sha256(os.path.realpath(file_path).encode("utf-8")).hexdigest()[:16]
    lock_path = os.path.join(_LOCK_DIR, key + ".lock")
    return FileLock(lock_path, timeout=_LOCK_TIMEOUT_SECONDS, is_singleton=True)
