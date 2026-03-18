"""
Application Level Encryption (ALE) for data at rest.

Implements envelope encryption:
- Data Encryption Keys (DEKs): AES-256-GCM, one per project, stored wrapped
- Key Encryption Keys (KEKs): derived from plugin provider (Midway/KMS/passphrase)

This module is the single integration point for all storage layers.
Callers use get_encryptor() and call encrypt()/decrypt().

File format for encrypted data:
    ZIYA-ALE-V1\\x00  (12 bytes magic)
    version           (1 byte, currently 0x01)
    dek_id_len        (1 byte)
    dek_id            (variable, utf-8)
    nonce             (12 bytes, AES-GCM)
    ciphertext+tag    (variable, AES-256-GCM authenticated)
"""

import hashlib
import json
import os
import struct
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from app.utils.logging_utils import logger

MAGIC = b"ZIYA-ALE-V1\x00"
FORMAT_VERSION = 1

# Lazy-loaded to avoid hard dependency for community users
_cryptography_available: Optional[bool] = None


def _ensure_cryptography():
    """Check that the cryptography library is installed."""
    global _cryptography_available
    if _cryptography_available is None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
            _cryptography_available = True
        except ImportError:
            _cryptography_available = False
    if not _cryptography_available:
        raise ImportError(
            "Encryption requires the 'cryptography' package. "
            "Install with: pip install cryptography"
        )


class WrappedDEK:
    """A Data Encryption Key entry in the keyring."""

    def __init__(self, dek_id: str, wrapped_key: bytes, kek_id: str,
                 created_at: float, status: str = "active"):
        self.dek_id = dek_id
        self.wrapped_key = wrapped_key  # DEK encrypted with KEK
        self.kek_id = kek_id
        self.created_at = created_at
        self.status = status  # "active" | "decrypt-only" | "retired"

    def to_dict(self) -> dict:
        return {
            "dek_id": self.dek_id,
            "wrapped_key": self.wrapped_key.hex(),
            "kek_id": self.kek_id,
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WrappedDEK":
        return cls(
            dek_id=d["dek_id"],
            wrapped_key=bytes.fromhex(d["wrapped_key"]),
            kek_id=d["kek_id"],
            created_at=d["created_at"],
            status=d.get("status", "active"),
        )


class Keyring:
    """Manages wrapped DEKs on disk at ~/.ziya/keyring.json."""

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            from app.utils.paths import get_ziya_home
            path = get_ziya_home() / "keyring.json"
        self.path = path
        self._entries: Dict[str, WrappedDEK] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            for entry in data.get("keys", []):
                w = WrappedDEK.from_dict(entry)
                self._entries[w.dek_id] = w
        except Exception as e:
            logger.warning(f"Failed to load keyring: {e}")

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"keys": [w.to_dict() for w in self._entries.values()]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(self.path)
        # Restrict permissions — keyring is sensitive material
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _backup(self):
        """Create a timestamped backup of the keyring before mutations."""
        if not self.path.exists():
            return
        backup_dir = self.path.parent / "keyring_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"keyring-{int(time.time())}.json"
        try:
            import shutil
            shutil.copy2(self.path, backup_path)
            # Keep only the 10 most recent backups
            backups = sorted(backup_dir.glob("keyring-*.json"))
            for old in backups[:-10]:
                old.unlink(missing_ok=True)
            logger.debug(f"🔑 Keyring backed up to {backup_path.name}")
        except Exception as e:
            logger.warning(f"🔑 Keyring backup failed (non-fatal): {e}")

    def get_active_dek(self) -> Optional[WrappedDEK]:
        with self._lock:
            for w in self._entries.values():
                if w.status == "active":
                    return w
        return None

    def get_dek(self, dek_id: str) -> Optional[WrappedDEK]:
        return self._entries.get(dek_id)

    def add_dek(self, wrapped: WrappedDEK):
        with self._lock:
            self._entries[wrapped.dek_id] = wrapped
            self._save()

    def rotate_active(self, new_wrapped: WrappedDEK):
        """Mark existing active DEK as decrypt-only, set new one active."""
        self._backup()
        with self._lock:
            for w in self._entries.values():
                if w.status == "active":
                    w.status = "decrypt-only"
            self._entries[new_wrapped.dek_id] = new_wrapped
            self._save()
        logger.info(f"🔑 DEK rotated: new DEK {new_wrapped.dek_id}")

    def rewrap_all(self, old_kek: bytes, new_kek: bytes, new_kek_id: str):
        """Re-wrap all DEKs with a new KEK (KEK rotation)."""
        self._backup()
        _ensure_cryptography()
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        with self._lock:
            for w in self._entries.values():
                if w.status == "retired":
                    continue
                # Unwrap with old KEK
                old_gcm = AESGCM(old_kek)
                nonce = w.wrapped_key[:12]
                raw_dek = old_gcm.decrypt(nonce, w.wrapped_key[12:], None)
                # Re-wrap with new KEK
                new_gcm = AESGCM(new_kek)
                new_nonce = os.urandom(12)
                new_wrapped = new_nonce + new_gcm.encrypt(new_nonce, raw_dek, None)
                w.wrapped_key = new_wrapped
                w.kek_id = new_kek_id
            self._save()
        logger.info(f"🔑 KEK rotation: re-wrapped {len(self._entries)} DEK(s)")


class DataEncryptor:
    """
    Main encryption interface for storage layers.

    Usage:
        encryptor = get_encryptor()
        if encryptor.is_enabled():
            ciphertext = encryptor.encrypt(plaintext_bytes, category="conversation_data")
            plaintext = encryptor.decrypt(ciphertext_bytes)
    """

    def __init__(self):
        self._policy = None
        self._kek: Optional[bytes] = None
        self._kek_id: Optional[str] = None
        self._keyring: Optional[Keyring] = None
        self._initialized = False
        self._lock = threading.Lock()

    def _initialize(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            try:
                from app.plugins import get_effective_encryption_policy, get_encryption_providers
                self._policy = get_effective_encryption_policy()

                if not self._policy.enabled:
                    logger.debug("🔐 ALE disabled (no encryption policy active)")
                    self._initialized = True
                    return

                _ensure_cryptography()
                self._keyring = Keyring()
                self._kek = self._resolve_kek()

                if self._kek is None:
                    logger.warning("🔐 ALE: Encryption policy requires encryption but KEK unavailable — falling back to plaintext")
                    self._policy.enabled = False
                    self._initialized = True
                    return

                # Ensure we have an active DEK
                active = self._keyring.get_active_dek()
                if active is None:
                    self._generate_dek()
                else:
                    # Check for KEK rotation
                    if active.kek_id != self._kek_id:
                        logger.info(f"🔑 KEK changed ({active.kek_id} → {self._kek_id}), re-wrapping DEKs")
                        # Try to derive old KEK for re-wrapping
                        # For now, generate new DEK (safest approach)
                        self._generate_dek()

                    # Check for DEK rotation
                    if self._policy.dek_rotation_interval:
                        age = time.time() - active.created_at
                        max_age = self._policy.dek_rotation_interval.total_seconds()
                        if age > max_age:
                            logger.info(f"🔑 DEK age ({age/86400:.1f} days) exceeds rotation interval ({max_age/86400:.1f} days)")
                            self._generate_dek()

                # Mark initialized BEFORE self-test so encrypt()/decrypt()
                # don't re-enter _initialize() and deadlock on self._lock.
                self._initialized = True

                if not self._self_test():
                    logger.error(
                        "🔐 ALE SELF-TEST FAILED — disabling encryption to protect data. "
                        "Existing encrypted files remain readable if keyring is intact."
                    )
                    self._policy.enabled = False
                    return

                # First-time activation: snapshot existing data before any writes
                self._backup_on_first_activation()

                logger.info(f"🔐 ALE initialized: {self._policy.kek_source} KEK, "
                            f"active DEK: {self._keyring.get_active_dek().dek_id if self._keyring.get_active_dek() else 'none'}")

            except ImportError as e:
                logger.warning(f"🔐 ALE: {e}")
                self._policy = None
                self._initialized = True
            except Exception as e:
                logger.error(f"🔐 ALE initialization failed: {e}")
                self._policy = None
                self._initialized = True

    def _self_test(self) -> bool:
        """Encrypt and decrypt a canary value to verify the full pipeline works."""
        try:
            canary = b"ZIYA_CANARY_" + os.urandom(16)
            encrypted = self.encrypt(canary, "self_test")
            if encrypted == canary:
                # encrypt() returned plaintext — means is_enabled was false,
                # which shouldn't happen here. Treat as failure.
                logger.error("🔐 Self-test: encrypt() returned plaintext unexpectedly")
                return False
            decrypted = self.decrypt(encrypted)
            if decrypted != canary:
                logger.error("🔐 Self-test: decrypted canary does not match original")
                return False
            logger.debug("🔐 Self-test passed: encrypt→decrypt round-trip OK")
            return True
        except Exception as e:
            logger.error(f"🔐 Self-test failed with exception: {e}")
            return False

    def _backup_on_first_activation(self):
        """
        One-time snapshot of ~/.ziya/projects/ before encryption writes begin.

        Creates ~/.ziya/pre_encryption_backup/ with a copy of all project
        data.  Only runs once — if the backup dir already exists, skips.
        """
        try:
            from app.utils.paths import get_ziya_home
            ziya_home = get_ziya_home()
            backup_dir = ziya_home / "pre_encryption_backup"

            if backup_dir.exists():
                return  # Already backed up from a previous activation

            projects_dir = ziya_home / "projects"
            if not projects_dir.exists():
                return  # Nothing to back up

            import shutil
            shutil.copytree(projects_dir, backup_dir / "projects")

            # Also back up file_states.json if it exists
            for name in ("file_states.json", "token_calibration.json"):
                src = ziya_home / name
                if src.exists():
                    shutil.copy2(src, backup_dir / name)

            logger.info(f"🔐 Pre-encryption backup created at {backup_dir}")
        except Exception as e:
            logger.warning(f"🔐 Pre-encryption backup failed (non-fatal): {e}")

    def _resolve_kek(self) -> Optional[bytes]:
        """Resolve the KEK from the configured source."""
        from app.plugins import get_encryption_providers

        # Try plugin providers first
        for provider in get_encryption_providers():
            try:
                if not provider.should_apply():
                    continue
                kek = provider.derive_kek()
                if kek and len(kek) == 32:
                    self._kek_id = provider.get_kek_identifier()
                    logger.debug(f"🔑 KEK from provider {provider.provider_id} (id: {self._kek_id[:8]}...)")
                    return kek
            except Exception as e:
                logger.warning(f"🔑 KEK derivation failed for {provider.provider_id}: {e}")

        # Fallback: passphrase from env var
        passphrase = os.environ.get("ZIYA_ENCRYPTION_KEY")
        if passphrase:
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=b"ziya-ale-v1-passphrase",
                iterations=600_000,
            )
            kek = kdf.derive(passphrase.encode())
            self._kek_id = f"passphrase-{hashlib.sha256(kek).hexdigest()[:12]}"
            logger.info("🔑 KEK derived from ZIYA_ENCRYPTION_KEY passphrase")
            return kek

        return None

    def _generate_dek(self):
        """Generate a new DEK, wrap it, and store in keyring."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        raw_dek = os.urandom(32)
        dek_id = hashlib.sha256(raw_dek).hexdigest()[:16]

        # Wrap DEK with KEK
        gcm = AESGCM(self._kek)
        nonce = os.urandom(12)
        wrapped = nonce + gcm.encrypt(nonce, raw_dek, None)

        entry = WrappedDEK(
            dek_id=dek_id,
            wrapped_key=wrapped,
            kek_id=self._kek_id,
            created_at=time.time(),
            status="active",
        )
        self._keyring.rotate_active(entry)

    def _unwrap_dek(self, wrapped: WrappedDEK) -> bytes:
        """Unwrap a DEK using the current KEK."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        gcm = AESGCM(self._kek)
        nonce = wrapped.wrapped_key[:12]
        return gcm.decrypt(nonce, wrapped.wrapped_key[12:], None)

    def is_enabled(self, category: str = "") -> bool:
        """Check if encryption is active, optionally for a category."""
        self._initialize()
        if not self._policy or not self._policy.enabled:
            return False
        if category:
            return self._policy.requires_encryption(category)
        return True

    def encrypt(self, plaintext: bytes, category: str = "") -> bytes:
        """Encrypt data, returning the binary envelope."""
        self._initialize()
        if not self.is_enabled(category):
            return plaintext

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        active = self._keyring.get_active_dek()
        if not active:
            logger.error("🔐 No active DEK — cannot encrypt")
            return plaintext

        raw_dek = self._unwrap_dek(active)
        gcm = AESGCM(raw_dek)
        nonce = os.urandom(12)
        ciphertext = gcm.encrypt(nonce, plaintext, None)

        dek_id_bytes = active.dek_id.encode("utf-8")

        # Build envelope: magic + version + dek_id_len + dek_id + nonce + ciphertext
        envelope = (
            MAGIC
            + struct.pack("BB", FORMAT_VERSION, len(dek_id_bytes))
            + dek_id_bytes
            + nonce
            + ciphertext
        )
        return envelope

    def decrypt(self, envelope: bytes) -> bytes:
        """Decrypt an encrypted envelope, returning plaintext."""
        self._initialize()

        if not envelope.startswith(MAGIC):
            # Not encrypted — return as-is (plaintext backward compatibility)
            return envelope

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        offset = len(MAGIC)
        version, dek_id_len = struct.unpack_from("BB", envelope, offset)
        offset += 2

        dek_id = envelope[offset:offset + dek_id_len].decode("utf-8")
        offset += dek_id_len

        nonce = envelope[offset:offset + 12]
        offset += 12

        ciphertext = envelope[offset:]

        wrapped = self._keyring.get_dek(dek_id)
        if not wrapped:
            raise ValueError(
                f"DEK {dek_id} not found in keyring — data cannot be decrypted. "
                f"If you changed ZIYA_ENCRYPTION_KEY, the old key is needed to read "
                f"existing data. Check ~/.ziya/keyring_backups/ for recovery."
            )

        try:
            raw_dek = self._unwrap_dek(wrapped)
            gcm = AESGCM(raw_dek)
            return gcm.decrypt(nonce, ciphertext, None)
        except Exception as e:
            raise ValueError(
                f"Decryption failed for DEK {dek_id}: {e}. "
                f"This usually means the KEK (passphrase or Midway cert) has changed. "
                f"Check ~/.ziya/keyring_backups/ for recovery options."
            ) from e


# Singleton
_encryptor: Optional[DataEncryptor] = None
_encryptor_lock = threading.Lock()


def get_encryptor() -> DataEncryptor:
    """Get the global DataEncryptor singleton."""
    global _encryptor
    if _encryptor is None:
        with _encryptor_lock:
            if _encryptor is None:
                _encryptor = DataEncryptor()
    return _encryptor


def is_encrypted(data: bytes) -> bool:
    """Check if data starts with the ALE magic bytes."""
    return data[:len(MAGIC)] == MAGIC
