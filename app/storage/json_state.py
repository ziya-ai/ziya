"""
Small durable JSON state store for the classified paths in ``app.utils.paths``.

First consumer: skill/MCP placement layers for the Capabilities Hub
(design/capabilities-hub.md).  Deliberately minimal: one JSON document per
file, whole-document replace, no schema.

Guarantees:
  * Reads/writes go through ``json_storage`` so files honour the at-rest
    encryption policy like every other session artifact.
  * ``write`` is atomic (temp file + ``os.replace``): a concurrent reader
    never sees a torn document.
  * ``update`` holds an ``fcntl`` lock across read-modify-write, so two
    server workers (or two tabs racing through the API) cannot lose each
    other's edits.  The lock is a sibling ``.lock`` file, never the data
    file, because ``os.replace`` would swap the locked inode out from under
    the waiter.
"""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

from app.utils.json_storage import read_json_file, write_json_file


class JsonStateStore:
    def __init__(self, path: Path, category: str = "session_data"):
        self.path = Path(path)
        self.category = category

    # -- primitives -------------------------------------------------------

    def read(self) -> Dict[str, Any]:
        """The stored document, or ``{}`` when absent or not an object."""
        try:
            data = read_json_file(self.path, self.category)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, data: Dict[str, Any]) -> None:
        """Replace the document atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        write_json_file(tmp, data, self.category)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    # -- read-modify-write -------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        with open(lock_path, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def update(self, fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> Dict[str, Any]:
        """Apply ``fn`` to the current document under lock and persist.

        ``fn`` may mutate its argument in place and return None, or return a
        replacement dict.  Returns the persisted document.
        """
        with self._locked():
            current = self.read()
            result = fn(current)
            new = current if result is None else result
            self.write(new)
            return new

    # -- convenience -------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self.read().get(key, default)

    def set(self, key: str, value: Any) -> Dict[str, Any]:
        return self.update(lambda d: d.__setitem__(key, value))
