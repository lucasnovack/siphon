# tests/test_jwt_utils.py
import hashlib
import time
import uuid

import pytest

from siphon.auth.jwt_utils import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_create_access_token_is_decodable():
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "admin")
    payload = decode_access_token(token)
    assert payload["sub"] == str(user_id)
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_access_token_expires():
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "operator", expires_minutes=0)
    time.sleep(0.1)
    import jwt as pyjwt
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_access_token(token)


def test_decode_access_token_rejects_tampered_token():
    import jwt as pyjwt
    user_id = uuid.uuid4()
    token = create_access_token(user_id, "admin")
    tampered = token[:-3] + "xxx"
    with pytest.raises(pyjwt.InvalidSignatureError):
        decode_access_token(tampered)


def test_create_refresh_token_returns_three_values():
    from datetime import UTC, datetime
    token_str, token_hash, expires_at = create_refresh_token()
    assert isinstance(token_str, str)
    assert len(token_str) > 20
    assert token_hash == hashlib.sha256(token_str.encode()).hexdigest()
    assert len(token_hash) == 64
    assert expires_at > datetime.now(tz=UTC)


def test_refresh_token_uniqueness():
    token1, _, _ = create_refresh_token()
    token2, _, _ = create_refresh_token()
    assert token1 != token2


def test_hash_password_and_verify():
    hashed = hash_password("s3cr3t!")
    assert hashed != "s3cr3t!"
    assert verify_password("s3cr3t!", hashed)
    assert not verify_password("wrong", hashed)


def test_verify_password_wrong_returns_false():
    hashed = hash_password("correcthorse")
    assert not verify_password("batterystaple", hashed)
