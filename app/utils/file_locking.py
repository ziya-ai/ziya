"""
Exclusive cross-process locks on an open file, on POSIX and Windows.

POSIX keeps the exact ``fcntl.flock`` semantics the storage layer was built
on.  ``fcntl`` does not exist on Windows, and importing it at module level
made the whole server fail to start there, so Windows uses ``msvcrt``
byte-range locks instead.

Windows byte-range locks are mandatory, not advisory: a locked byte cannot
be read through any other handle.  The lock is therefore taken on a single
byte far past end-of-file (the trick SQLite uses for its lock bytes), so
holding it never blocks a reader of the file's actual contents.  The file
position is restored afterwards, so a file object wrapping the descriptor
is unaffected.
"""

import os
import time
from typing import IO, Union

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

# Lock byte offset on Windows; must stay below 2 GiB for msvcrt.locking.
_WINDOWS_LOCK_OFFSET = 0x40000000
_WINDOWS_RETRY_SECONDS = 0.05

FileLike = Union[int, IO]


def _fileno(f: FileLike) -> int:
    return f if isinstance(f, int) else f.fileno()


def _windows_locking(fd: int, mode: int) -> None:
    position = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
    try:
        msvcrt.locking(fd, mode, 1)
    finally:
        os.lseek(fd, position, os.SEEK_SET)


def lock_exclusive(f: FileLike, blocking: bool = True) -> bool:
    """Take an exclusive lock on *f* (a file object or descriptor).

    Returns True once held.  With ``blocking=False``, returns False instead
    of waiting when another descriptor holds the lock.
    """
    fd = _fileno(f)
    if fcntl is not None:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError:
            return False
        return True
    while True:
        try:
            _windows_locking(fd, msvcrt.LK_NBLCK)
            return True
        except OSError:
            # msvcrt.LK_LOCK gives up after ten one-second retries, so poll
            # with the non-blocking mode to get a true blocking acquire.
            if not blocking:
                return False
            time.sleep(_WINDOWS_RETRY_SECONDS)


def unlock(f: FileLike) -> None:
    """Release a lock taken with :func:`lock_exclusive`."""
    fd = _fileno(f)
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    else:
        _windows_locking(fd, msvcrt.LK_UNLCK)
