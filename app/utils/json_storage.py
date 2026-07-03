"""
Encryption-aware JSON file helpers for route handlers that manage their
own small metadata files (folders.json, conversations.json,
registry_favorites.json) outside of the BaseStorage class hierarchy.

These mirror BaseStorage._read_json()/_write_json() so that route-level
file I/O honors Application Level Encryption (ALE) the same way storage
classes do, instead of writing plaintext JSON regardless of policy
[PenPal #143, CWE-311].
"""

import json
from pathlib import Path
from typing import Any, Optional, Union

from app.utils.encryption import get_encryptor, is_encrypted
def read_json_file(path: Union[str, Path], category: str = "session_data") -> Optional[Any]:
    """Read a JSON file, decrypting with ALE if the file is encrypted.

    Returns None if the file does not exist. *category* is unused for
    reads (encryption is auto-detected via magic bytes) but kept for
    symmetry with write_json_file() and future policy hooks.
    """
    p = Path(path)
    if not p.exists():
        return None
    raw = p.read_bytes()
    if not raw:
        return None
    if is_encrypted(raw):
        raw = get_encryptor().decrypt(raw)
    return json.loads(raw)


def write_json_file(path: Union[str, Path], data: Any, category: str = "session_data") -> None:
    """Write *data* as JSON, encrypting with ALE when the policy requires it.

    Writes atomically via a temp file + rename so a crash mid-write never
    leaves a partially-written file in place.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    encryptor = get_encryptor()
    plaintext = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    payload = encryptor.encrypt(plaintext, category) if encryptor.is_enabled(category) else plaintext
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(p)  # atomic on POSIX
