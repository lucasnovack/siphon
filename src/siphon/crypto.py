# src/siphon/crypto.py
import os

from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    key = os.getenv("SIPHON_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError(
            "SIPHON_ENCRYPTION_KEY not set — cannot encrypt/decrypt connection credentials"
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> bytes:
    """Encrypt a UTF-8 string. Returns Fernet token bytes."""
    return _get_fernet().encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    """Decrypt a Fernet token. Returns UTF-8 string."""
    return _get_fernet().decrypt(ciphertext).decode()
