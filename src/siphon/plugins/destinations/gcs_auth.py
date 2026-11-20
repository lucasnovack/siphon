# src/siphon/plugins/destinations/gcs_auth.py
"""Google Cloud Storage authentication and PyArrow GcsFileSystem factory.

Supports all standard GCP authentication modes without extra dependencies:
- ``adc`` (default): Application Default Credentials (AIP-4110) — automatically
  integrates with GCE VM Metadata Server, GKE Workload Identity, Cloud Run,
  GOOGLE_APPLICATION_CREDENTIALS, or local gcloud ADC without storing keys.
- ``service_account``: Thread-safe RFC 7523 OAuth2 JWT bearer exchange using
  the Service Account JSON stored encrypted in a Siphon Connection.
- ``anonymous``: Anonymous access (public buckets or local fake-gcs-server).
- Optional ``target_service_account`` impersonation with ``adc`` or ``service_account``.
"""
import json
import os
import time
from datetime import UTC, datetime, timedelta

import jwt
import pyarrow.fs as pafs
import requests

_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
_GCS_RW_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"


def exchange_service_account_token(
    credentials_json: str | dict,
    scope: str = _GCS_RW_SCOPE,
    timeout: int = 10,
) -> tuple[str, datetime]:
    """Exchange a GCP Service Account JSON for an OAuth2 (access_token, expiration)."""
    if isinstance(credentials_json, str):
        raw = credentials_json.strip()
        if not raw:
            raise ValueError("credentials_json is required when auth_mode='service_account'")
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid Service Account JSON") from exc
    elif isinstance(credentials_json, dict):
        info = credentials_json
    else:
        raise ValueError("credentials_json must be a JSON string or dict")

    client_email = info.get("client_email")
    private_key = info.get("private_key")
    if not client_email or not private_key:
        raise ValueError("Service Account JSON must contain 'client_email' and 'private_key'")

    token_uri = info.get("token_uri") or _DEFAULT_TOKEN_URI
    now = int(time.time())
    payload = {
        "iss": client_email,
        "sub": client_email,
        "aud": token_uri,
        "scope": scope,
        "iat": now,
        "exp": now + 3600,
    }
    assertion = jwt.encode(payload, private_key, algorithm="RS256")

    resp = requests.post(
        token_uri,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    access_token = data["access_token"]
    expires_in = int(data.get("expires_in", 3600))
    expiration = datetime.now(tz=UTC) + timedelta(seconds=expires_in)
    return access_token, expiration


def build_gcs_filesystem(
    *,
    auth_mode: str = "adc",
    credentials_json: str | dict | None = None,
    project_id: str | None = None,
    target_service_account: str | None = None,
    endpoint_override: str | None = None,
    scheme: str | None = None,
) -> pafs.GcsFileSystem:
    """Construct a PyArrow GcsFileSystem for the requested authentication mode."""
    mode = (auth_mode or "adc").strip().lower()
    kwargs: dict = {}

    if project_id:
        kwargs["project_id"] = project_id
    if target_service_account:
        kwargs["target_service_account"] = target_service_account
    if endpoint_override:
        kwargs["endpoint_override"] = endpoint_override
    effective_scheme = scheme or os.getenv("SIPHON_GCS_SCHEME")
    if effective_scheme:
        kwargs["scheme"] = effective_scheme

    if mode == "anonymous":
        kwargs["anonymous"] = True
    elif mode == "service_account":
        if not credentials_json:
            raise ValueError("credentials_json is required when auth_mode='service_account'")
        token, expiration = exchange_service_account_token(credentials_json)
        kwargs["access_token"] = token
        kwargs["credential_token_expiration"] = expiration
    elif mode == "adc":
        # If credentials_json was explicitly supplied even in default mode, honor it;
        # otherwise let PyArrow resolve ADC / GCP Metadata Server natively.
        if credentials_json:
            token, expiration = exchange_service_account_token(credentials_json)
            kwargs["access_token"] = token
            kwargs["credential_token_expiration"] = expiration
    else:
        raise ValueError(f"Unsupported GCS auth_mode: {auth_mode!r}")

    return pafs.GcsFileSystem(**kwargs)
