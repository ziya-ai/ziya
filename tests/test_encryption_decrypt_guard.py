"""
Regression coverage for PenPal #148 [LOW, CWE-476]: NULL deref in
DataEncryptor.decrypt() when encryption is disabled or uninitialized.

decrypt() checked the MAGIC prefix then dereferenced self._keyring.get_dek()
with no None guard. When encryption was disabled/uninitialized (keyring is
None), an already-encrypted (magic-prefixed) file raised a bare
AttributeError — swallowed by callers' broad `except`, so the file read back
as None/[] and its data silently vanished. Fixed by a controlled ValueError
AFTER the MAGIC check (so plaintext passthrough is preserved).
"""
import struct
import pytest

from app.utils.encryption import DataEncryptor, MAGIC, FORMAT_VERSION


def _disabled_encryptor():
    """A DataEncryptor in the disabled/uninitialized state (no key material),
    without running _initialize() (which would consult the real environment)."""
    enc = DataEncryptor.__new__(DataEncryptor)
    enc._kek = None
    enc._kek_id = None
    enc._keyring = None
    enc._initialized = True   # so decrypt()'s _initialize() is a no-op
    enc._policy = None        # disabled
    return enc


def _encrypted_envelope(dek_id: bytes = b"dek_test") -> bytes:
    """A well-formed encrypted-envelope header so decrypt() advances PAST the
    MAGIC check to the key-material guard."""
    return (
        MAGIC
        + struct.pack("BB", FORMAT_VERSION, len(dek_id))
        + dek_id
        + b"\x00" * 12       # nonce
        + b"ciphertextbytes"
    )


class TestDisabledDecryptGuard:
    def test_plaintext_passthrough_preserved_when_disabled(self):
        # The common backward-compat case: a NON-encrypted file read while
        # encryption is disabled must pass through untouched (NOT raise).
        enc = _disabled_encryptor()
        plain = b'{"some": "plaintext json"}'
        assert enc.decrypt(plain) == plain

    def test_encrypted_envelope_without_keys_raises_controlled_error(self):
        # An actually-encrypted file with no key material must raise a
        # controlled ValueError, NOT a bare AttributeError (which callers
        # swallow into silent data loss).
        enc = _disabled_encryptor()
        with pytest.raises(ValueError) as ei:
            enc.decrypt(_encrypted_envelope())
        msg = str(ei.value).lower()
        assert "encryption is disabled" in msg or "key material is unavailable" in msg

    def test_does_not_raise_attributeerror(self):
        # Explicit non-regression: the failure mode was AttributeError.
        enc = _disabled_encryptor()
        try:
            enc.decrypt(_encrypted_envelope())
        except ValueError:
            pass  # expected
        except AttributeError:
            pytest.fail("decrypt() still raises the swallowed AttributeError")

    def test_kek_none_but_keyring_present_also_guarded(self):
        # Secondary path: KEK provider absent leaves _kek=None even when a
        # Keyring() was instantiated. Must still be a controlled ValueError.
        enc = _disabled_encryptor()
        enc._keyring = object()   # non-None, but _kek is None
        enc._kek = None
        with pytest.raises(ValueError):
            enc.decrypt(_encrypted_envelope())


class TestNegativeControlPreFix:
    """Proves the guard is non-vacuous: without it, dereferencing a None
    keyring on a magic-prefixed envelope is an AttributeError."""

    def test_prefix_deref_is_attributeerror(self):
        env = _encrypted_envelope()
        assert env.startswith(MAGIC)
        keyring = None
        with pytest.raises(AttributeError):
            keyring.get_dek("dek_test")   # the exact pre-fix dereference
