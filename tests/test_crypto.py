# tests/test_crypto.py
import importlib

import pytest


def test_encrypt_decrypt_roundtrip(monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SIPHON_ENCRYPTION_KEY", key)
    import siphon.crypto as crypto
    importlib.reload(crypto)
    plaintext = '{"connection": "mysql://user:pass@host/db"}'
    encrypted = crypto.encrypt(plaintext)
    assert isinstance(encrypted, bytes)
    assert crypto.decrypt(encrypted) == plaintext


def test_encrypt_raises_without_key(monkeypatch):
    monkeypatch.delenv("SIPHON_ENCRYPTION_KEY", raising=False)
    import siphon.crypto as crypto
    importlib.reload(crypto)
    with pytest.raises(RuntimeError, match="SIPHON_ENCRYPTION_KEY"):
        crypto.encrypt("test")
