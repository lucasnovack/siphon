# Authentication & Authorization

Siphon has two authentication mechanisms: **JWT tokens** (for the UI and direct API consumers) and a **legacy API key** (for Airflow and backward-compatible clients). Both are accepted on the same endpoints.

---

## How authentication works

### JWT flow (UI and new API clients)

```
1. POST /api/v1/auth/login  { email, password }
         │
         ▼
   Server validates password (bcrypt)
   Issues access_token (15-min JWT, returned in response body)
   Issues refresh_token (7-day, stored as hash in DB, sent as httpOnly cookie)
         │
         ▼
2. Client stores access_token in memory (never localStorage)
   Attaches it as Authorization: Bearer <access_token> on every request
         │
         ▼
3. When access_token expires (401 response):
   Frontend automatically calls POST /api/v1/auth/refresh
   Server validates the httpOnly cookie, issues new access_token + new refresh cookie
   Original request is retried with the new token
         │
         ▼
4. POST /api/v1/auth/logout
   Server revokes the refresh token in DB
   Cookie is cleared
```

### Legacy API key (Airflow)

Set `SIPHON_API_KEY` in the environment. Pass it as:

```http
Authorization: Bearer <SIPHON_API_KEY>
```

The API key works on all endpoints, including `/jobs` (legacy extraction endpoint). If `SIPHON_API_KEY` is not set, API key auth is disabled and only JWT works.

---

## Endpoints

### Login

```http
POST /api/v1/auth/login
Content-Type: application/json

{ "email": "admin@example.com", "password": "changeme123" }
```

Response:

```json
{
  "access_token": "eyJhbGci...",
  "expires_in": 900
}
```

**Rate limit**: 10 requests per minute per IP. Exceeding returns HTTP 429.

### Refresh

```http
POST /api/v1/auth/refresh
```

No body required. The refresh token is read from the `refresh_token` httpOnly cookie.

Response: same shape as login. The old refresh token is immediately revoked; a new one is set in the cookie.

**Reuse detection**: if a refresh token that has already been used is presented, Siphon considers the session compromised and revokes **all** active sessions for that user. This protects against stolen tokens.

### Logout

```http
POST /api/v1/auth/logout
Authorization: Bearer <access_token>
```

Revokes the current refresh token. The access token will expire naturally (within 15 minutes).

### Current user

```http
GET /api/v1/auth/me
Authorization: Bearer <access_token>
```

Response:

```json
{
  "id": "uuid",
  "email": "admin@example.com",
  "name": null,
  "role": "admin",
  "is_active": true,
  "created_at": "2026-01-01T00:00:00Z"
}
```

---

## Roles

Siphon has two roles:

| Role | Permissions |
|---|---|
| `admin` | Full access: read + write connections, pipelines, schedules, users |
| `operator` | Read-only: list/view connections, pipelines, runs. Cannot create, update, or delete anything |

Write operations that require admin return **HTTP 403** for operator users.

---

## User management

Users are managed via the API or the **Settings → Users** page in the UI.

### Bootstrap admin

The first admin user can be auto-created on startup by setting:

```
SIPHON_ADMIN_EMAIL=admin@example.com
SIPHON_ADMIN_PASSWORD=changeme123
```

This only takes effect if **no users exist** in the database. Once any user exists, these variables are ignored. Change the password immediately after first login.

### Create a user

```http
POST /api/v1/users
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "email": "operator@example.com",
  "password": "secure-password",
  "role": "operator"
}
```

### Update a user

```http
PUT /api/v1/users/{id}
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "role": "admin",
  "password": "new-password",
  "is_active": true
}
```

All fields are optional. Only the fields you include are updated.

### Disable a user (soft delete)

```http
PUT /api/v1/users/{id}
Authorization: Bearer <admin-token>
Content-Type: application/json

{ "is_active": false }
```

Disabled users cannot log in and all their active sessions become invalid on the next request.

---

## Credential security

### Passwords

User passwords are hashed with **bcrypt** (12 rounds) via passlib. Plaintext passwords are never stored or logged.

### JWT tokens

Access tokens are signed with **HMAC-SHA256** using `SIPHON_JWT_SECRET`. The token payload includes:

```json
{
  "sub": "user-uuid",
  "role": "admin",
  "type": "access",
  "exp": 1743000000
}
```

Tokens expire after **15 minutes**. After expiry, the frontend automatically refreshes using the httpOnly cookie.

### Refresh tokens

Refresh tokens are:
- Random 32-byte strings, URL-safe base64 encoded
- Stored in the database as a SHA-256 hash (never plaintext)
- Valid for **7 days**
- Sent and stored only as httpOnly, Secure, SameSite=Strict cookies
- Rotated on every use (old token revoked, new token issued)

### Connection credentials

Database passwords, SFTP credentials, and S3 keys are encrypted at rest using **Fernet** (AES-128-CBC + HMAC-SHA256). See [connections.md](connections.md#security) for details.

---

## Secrets configuration

| Variable | Default | Description |
|---|---|---|
| `SIPHON_JWT_SECRET` | `dev-secret-change-in-production` | JWT signing key. **Change this before production.** |
| `SIPHON_ENCRYPTION_KEY` | — | Fernet key for connection credentials. **Required if using connections.** |
| `SIPHON_API_KEY` | — | Legacy API key for Airflow integration. Optional |

### Generating a JWT secret

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Generating a Fernet key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Securing production deployments

1. **Set `SIPHON_JWT_SECRET`** to a random 256-bit value. The default `dev-secret-change-in-production` is public and must not be used in production.

2. **Set `SIPHON_ENCRYPTION_KEY`** to a Fernet key generated as above. Losing this key means all stored connection credentials become unreadable.

3. **Rotate the bootstrap admin password** immediately after first login.

4. **Use HTTPS**. The refresh token is marked `Secure`, so it will not be sent over plain HTTP. If your ingress handles TLS termination, ensure the backend receives the `X-Forwarded-Proto: https` header.

5. **Scope `SIPHON_API_KEY`** to Airflow only. Do not share it with humans; use JWT for human access.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401 Unauthorized` on all requests | Access token expired and refresh failed | Re-login |
| `403 Forbidden` | Correct credentials but insufficient role | Use an admin account for write operations |
| `429 Too Many Requests` on login | Rate limit hit | Wait 1 minute |
| All sessions logged out unexpectedly | Refresh token reuse detected | Someone else used your refresh token; change password |
| `SIPHON_ENCRYPTION_KEY not set` | Container missing env var | Set the key in docker-compose or Kubernetes secret |
| Connections test fails after key rotation | Old credentials encrypted with previous key | Re-save each connection after key rotation |
