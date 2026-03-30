# Connections

Connections store credentials for data sources and destinations. The `config` object is encrypted at rest using Fernet symmetric encryption — raw credentials are never returned by the API.

---

## Concepts

| Field | Description |
|---|---|
| `name` | Unique identifier for humans and pipeline references |
| `type` | Driver/protocol: `sql`, `sftp`, or `s3_parquet` |
| `config` | Protocol-specific credentials (encrypted, write-only) |
| `key_version` | Increments each time `config` is updated — useful for auditing rotations |

**Permissions**: only `admin` users can create, update, or delete connections. All authenticated users can list and read connections (credentials are never included in responses).

---

## Via the UI

1. Navigate to **Connections → New Connection**
2. Enter a name and choose a type from the dropdown
3. Fill in the fields for the chosen type
4. Click **Save**

To delete, click the trash icon on the connections list. Deleting a connection will break any pipeline that references it.

---

## Via the API

All endpoints require a Bearer token from `POST /api/v1/auth/token`.

### List connections

```http
GET /api/v1/connections
Authorization: Bearer <token>
```

Response: `Connection[]`

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "name": "prod-mysql",
    "type": "sql",
    "key_version": 1,
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z"
  }
]
```

### Get a single connection

```http
GET /api/v1/connections/{id}
Authorization: Bearer <token>
```

### Create a connection

```http
POST /api/v1/connections
Authorization: Bearer <token>
Content-Type: application/json
```

Body:

```json
{
  "name": "prod-mysql",
  "type": "sql",
  "config": {
    "connection": "mysql://user:pass@host:3306/db"
  }
}
```

Returns `201 Created` with the `Connection` object. Returns `409` if the name already exists.

### Update a connection

Only `name` and `config` are updatable. Updating `config` increments `key_version`.

```http
PUT /api/v1/connections/{id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "prod-mysql-v2",
  "config": { "connection": "mysql://user:newpass@host:3306/db" }
}
```

### Delete a connection

```http
DELETE /api/v1/connections/{id}
Authorization: Bearer <token>
```

Returns `204 No Content`.

### Test a saved connection

```http
POST /api/v1/connections/{id}/test
Authorization: Bearer <token>
```

Returns `{"status": "ok"}` or `400` with the error message. The test runs the saved (decrypted) config against the real endpoint.

### List available types

```http
GET /api/v1/connections/types/list
Authorization: Bearer <token>
```

Returns the type metadata used by the UI to render the dynamic form fields.

---

## Connection types

### `sql` — SQL Database

Uses [ConnectorX](https://github.com/sfu-db/connector-x) for extraction. Supports MySQL, PostgreSQL, SQL Server, SQLite, Redshift, and Oracle (via `oracledb`).

| Field | Description |
|---|---|
| `connection` | Full connection URL |

**URL formats:**

```
# PostgreSQL
postgresql://user:pass@host:5432/db

# MySQL / MariaDB
mysql://user:pass@host:3306/db

# SQL Server
mssql://user:pass@host:1433/db

# Oracle
oracle+oracledb://user:pass@host:1521/service_name

# SQLite (local path — only useful for dev/testing)
sqlite:///./path/to/file.db
```

For **PostgreSQL** connections, a `connect_timeout` is automatically injected if not present in the URL (default: 30s, configurable via `SIPHON_CONNECT_TIMEOUT`). MySQL and other databases do not support this parameter via URL and it is not injected for them.

**Example:**

```json
{
  "name": "prod-mysql",
  "type": "sql",
  "config": {
    "connection": "mysql://siphon:secret@mysql.internal:3306/production"
  }
}
```

---

### `sftp` — SFTP Server

Uses Paramiko. The connection test opens an SSH session and immediately closes it.

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | — | Hostname or IP |
| `port` | integer | 22 | SSH port |
| `username` | string | — | SSH username |
| `password` | string | — | SSH password |

> Key-based authentication is not currently supported through the UI/API config. Use password auth or extend the SFTP plugin.

**Example:**

```json
{
  "name": "vendor-sftp",
  "type": "sftp",
  "config": {
    "host": "sftp.vendor.com",
    "port": 22,
    "username": "siphon",
    "password": "secret"
  }
}
```

**Host key verification:**

By default the SFTP plugin uses `RejectPolicy` — it will refuse unknown hosts. Configure host keys via environment variables:

| Variable | Description |
|---|---|
| `SIPHON_SFTP_KNOWN_HOSTS` | Path to a `known_hosts` file inside the container |
| `SIPHON_SFTP_HOST_KEY` | Base64-encoded RSA host key for a single host |

---

### `s3_parquet` — S3 / MinIO / Object Storage

Uses PyArrow's `S3FileSystem`. Works with AWS S3, MinIO, and any S3-compatible endpoint.

| Field | Description |
|---|---|
| `endpoint` | Host and port of the S3 endpoint (e.g. `minio.internal:9000`) |
| `access_key` | Access key / AWS Access Key ID |
| `secret_key` | Secret key / AWS Secret Access Key |

The HTTP scheme (`http` vs `https`) is controlled by the `SIPHON_S3_SCHEME` environment variable (default: `https`). For local MinIO without TLS, set `SIPHON_S3_SCHEME=http`.

**Example (MinIO):**

```json
{
  "name": "local-minio",
  "type": "s3_parquet",
  "config": {
    "endpoint": "minio:9000",
    "access_key": "minioadmin",
    "secret_key": "minioadmin"
  }
}
```

**Example (AWS S3):**

```json
{
  "name": "aws-bronze",
  "type": "s3_parquet",
  "config": {
    "endpoint": "s3.amazonaws.com",
    "access_key": "AKIAIOSFODNN7EXAMPLE",
    "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
  }
}
```

---

## Security

### Encryption at rest

All `config` objects are encrypted using [Fernet](https://cryptography.io/en/latest/fernet/) (AES-128-CBC + HMAC-SHA256) before being stored in the database. The key is set via `SIPHON_ENCRYPTION_KEY`.

To generate a key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Rotate a key**: update `SIPHON_ENCRYPTION_KEY` and re-save each connection via PUT. `key_version` increments on each config update, so you can track which connections have been rotated.

### SSRF protection

To prevent connections to internal infrastructure, set `SIPHON_ALLOWED_HOSTS` to a comma-separated list of allowed hostnames, IPs, or CIDR ranges:

```
SIPHON_ALLOWED_HOSTS=10.0.0.0/8,db.internal,sftp.vendor.com
```

When set, any connection whose host is not in the list is rejected before the driver is invoked — both on test and on pipeline execution.

---

## Adding a new connection type (developer guide)

There are three places to touch:

### 1. Register the Source plugin

Create `src/siphon/plugins/sources/my_type.py`:

```python
import pyarrow as pa
from siphon.plugins.sources import register
from siphon.plugins.sources.base import Source

@register("my_type")        # must match the type string used everywhere
class MyTypeSource(Source):
    def __init__(self, host: str, api_key: str) -> None:
        self.host = host
        self.api_key = api_key

    def extract(self) -> pa.Table:
        # fetch data, return as Arrow table
        ...
```

The `@register` decorator adds the class to `_REGISTRY`. The autodiscovery in `plugins/sources/__init__.py` imports all modules in the package at startup, so no manual wiring is needed.

For large sources that shouldn't be fully loaded into memory, override `extract_batches()` instead and `yield` Arrow tables:

```python
def extract_batches(self, chunk_size: int = 1000):
    for page in self._paginate():
        yield pa.Table.from_pydict(page)
```

### 2. Add the connection type literal and metadata

In `src/siphon/connections/router.py`:

**Add to the `ConnType` literal** (line 21):

```python
ConnType = Literal["sql", "sftp", "s3_parquet", "my_type"]
```

**Add to `_CONNECTION_TYPES`** (bottom of file):

```python
{
    "type": "my_type",
    "label": "My Data Source",
    "fields": [
        {"name": "host",    "type": "string",  "label": "Host",    "required": True},
        {"name": "api_key", "type": "string",  "label": "API Key", "secret": True, "required": True},
    ],
},
```

Field schema:

| Key | Values | Description |
|---|---|---|
| `name` | string | Key used in `config` dict — must match `__init__` parameter |
| `type` | `string`, `integer` | Input type hint |
| `label` | string | Display label in UI |
| `secret` | bool | Renders as password input and is excluded from logs |
| `required` | bool | Shown with `*` in UI |
| `placeholder` | string | Input placeholder |
| `default` | any | Default value shown in UI |

### 3. Add the connectivity test

In the `_test_connection` function in `router.py`, add a branch:

```python
elif conn_type == "my_type":
    import requests
    _validate_host(f"dummy://{config['host']}")
    resp = requests.get(f"https://{config['host']}/ping",
                        headers={"Authorization": config["api_key"]}, timeout=10)
    resp.raise_for_status()
```

Call `_validate_host` with the target host to respect `SIPHON_ALLOWED_HOSTS`.

### 4. Wire the config to the pipeline

In the worker (`src/siphon/worker.py`), connection configs are unpacked and passed to the Source constructor. Verify the key names in your `config` dict match the `__init__` parameters of your Source class exactly.

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `SIPHON_ENCRYPTION_KEY not set` | Container started without the env var | Set `SIPHON_ENCRYPTION_KEY` in docker-compose or environment |
| `Host 'x.x.x.x' not in SIPHON_ALLOWED_HOSTS` | SSRF guard active | Add the host to `SIPHON_ALLOWED_HOSTS` |
| `Connection 'name' already exists` (409) | Duplicate name | Use a different name or delete the existing connection first |
| `Connection test failed: …` (400) | Driver-level error | Check credentials, host reachability, firewall rules |
| No types in dropdown (UI) | Auth token expired or types endpoint unreachable | Re-login; check `/api/v1/connections/types/list` in browser DevTools Network tab |
