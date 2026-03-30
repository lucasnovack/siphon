# Deployment

Siphon ships as a single Docker image that serves both the API and the React UI. The only external dependency for full functionality is PostgreSQL. MinIO (or any S3-compatible store) is the destination for extracted data, not a Siphon dependency.

---

## Docker Compose (local / staging)

The included `docker-compose.yml` starts Siphon + PostgreSQL with a single command:

```bash
docker compose up --build
```

The UI is available at `http://localhost:8000`. Log in with the bootstrap admin credentials set in the compose file.

### Default configuration

```yaml
services:
  siphon:
    build: .
    ports: ["8000:8000"]
    environment:
      DATABASE_URL: "postgresql+asyncpg://siphon:siphon@postgres:5432/siphon"
      SIPHON_JWT_SECRET: "dev-jwt-secret-change-in-production"
      SIPHON_ENCRYPTION_KEY: "aMgwZ5VXJPcxLd3zoM510Dtb59-O6HhCUG8DhlZUiAo="
      SIPHON_ADMIN_EMAIL: "admin@example.com"
      SIPHON_ADMIN_PASSWORD: "changeme123"
    extra_hosts:
      - "host.docker.internal:host-gateway"   # allows containers to reach host services (Linux)

  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: siphon
      POSTGRES_USER: siphon
      POSTGRES_PASSWORD: siphon
    volumes:
      - postgres_data:/var/lib/postgresql/data
```

> **Note:** `host.docker.internal:host-gateway` lets the Siphon container reach services running on the Docker host (e.g., a local MySQL or MinIO for testing). This is a Linux-only workaround; on macOS and Windows, `host.docker.internal` resolves automatically.

### Using the `siphon.sh` helper

```bash
./siphon.sh start     # docker compose up --build -d
./siphon.sh stop      # docker compose down
./siphon.sh restart   # stop + start
./siphon.sh logs      # docker compose logs -f
./siphon.sh status    # docker compose ps
./siphon.sh reset     # docker compose down -v (destroys database)
```

---

## Environment variables

### Required in production

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL DSN with async driver: `postgresql+asyncpg://user:pass@host:5432/db` |
| `SIPHON_JWT_SECRET` | Random 256-bit secret for signing JWTs. Generate with: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `SIPHON_ENCRYPTION_KEY` | Fernet key for encrypting connection credentials. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

### Bootstrap (first run only)

| Variable | Description |
|---|---|
| `SIPHON_ADMIN_EMAIL` | Email for the auto-created admin user (only if DB has no users) |
| `SIPHON_ADMIN_PASSWORD` | Password for the auto-created admin user |

These variables are ignored once any user exists in the database. Remove them after the first deploy.

### Capacity & performance

| Variable | Default | Description |
|---|---|---|
| `SIPHON_PORT` | `8000` | HTTP port |
| `SIPHON_MAX_WORKERS` | `10` | Concurrent extraction threads |
| `SIPHON_MAX_QUEUE` | `50` | Max jobs waiting to start |
| `SIPHON_JOB_TIMEOUT` | `3600` | Max duration (seconds) per job |
| `SIPHON_JOB_TTL_SECONDS` | `3600` | How long to keep completed jobs in memory |
| `SIPHON_DRAIN_TIMEOUT` | `3600` | Graceful shutdown timeout (should equal JOB_TIMEOUT) |
| `SIPHON_MAX_REQUEST_SIZE_MB` | `1` | Max HTTP request body size |

### SQL sources

| Variable | Default | Description |
|---|---|---|
| `SIPHON_CONNECT_TIMEOUT` | `30` | PostgreSQL connect timeout in seconds |
| `SIPHON_ALLOWED_HOSTS` | `""` (permissive) | Comma-separated list of allowed source hosts/IPs/CIDRs for SSRF protection |
| `SIPHON_TIMEZONE` | `America/Sao_Paulo` | Timezone for `@TODAY` and date variable resolution |
| `SIPHON_MIN_DATE` | `1997-01-01` | Value for `@MIN_DATE` variable |
| `SIPHON_ORACLE_CHUNK_SIZE` | `50000` | Rows per batch for Oracle streaming |

### S3 / object storage

| Variable | Default | Description |
|---|---|---|
| `SIPHON_S3_SCHEME` | `https` | `http` or `https`. Use `http` for local MinIO without TLS |
| `SIPHON_ALLOWED_S3_PREFIX` | `bronze/` | Destination paths must start with this prefix |

### SFTP

| Variable | Default | Description |
|---|---|---|
| `SIPHON_MAX_FILE_SIZE_MB` | `500` | Max size of a single SFTP file |
| `SIPHON_SFTP_KNOWN_HOSTS` | — | Path to a `known_hosts` file inside the container |
| `SIPHON_SFTP_HOST_KEY` | — | Base64-encoded RSA host public key (single-host alternative to known_hosts) |

### Logging

| Variable | Default | Description |
|---|---|---|
| `SIPHON_LOG_FORMAT` | `text` | `text` or `json` (structured JSON for log aggregators) |

### Legacy (Airflow)

| Variable | Default | Description |
|---|---|---|
| `SIPHON_API_KEY` | — | Static API key for Airflow DAGs using `POST /jobs` |
| `SIPHON_ENABLE_SYNC_EXTRACT` | `false` | Enable `POST /extract` (synchronous, blocks until done) |
| `SIPHON_FRONTEND_DIR` | `/app/frontend/dist` | Path to built React assets inside the container |

---

## Docker image

The image is built in three stages to keep the final image small:

```
Stage 1: frontend-builder (Node 22)
  → pnpm install + pnpm build → frontend/dist/

Stage 2: builder (Python 3.12)
  → uv sync → .venv/

Stage 3: runtime (Python 3.12-slim, ~200MB)
  → .venv/ + src/ + alembic/ + frontend/dist/
  → Non-root user (UID 1000)
  → CMD: alembic upgrade head && uvicorn siphon.main:app
```

**Security properties:**
- Runs as non-root (`siphon` user, UID 1000)
- Read-only filesystem by default (only `/tmp` is writable)
- No package manager in the final image
- No build tools, test dependencies, or source maps

**Build locally:**
```bash
docker build -t siphon:local .
```

**Image size check:**
```bash
docker images siphon
```

---

## Database migrations

Migrations run automatically on startup via `alembic upgrade head` (the CMD in the Dockerfile). This is safe for rolling deploys because Alembic is idempotent — running against an already-migrated database is a no-op.

If you need to run migrations manually:

```bash
docker compose exec siphon alembic upgrade head
docker compose exec siphon alembic current      # check current version
docker compose exec siphon alembic history      # list all versions
```

---

## Kubernetes

Siphon is designed for Kubernetes. Here is a minimal deployment configuration:

### Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: siphon
spec:
  replicas: 2
  selector:
    matchLabels:
      app: siphon
  template:
    spec:
      containers:
        - name: siphon
          image: your-registry/siphon:latest
          ports:
            - containerPort: 8000
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: siphon-secrets
                  key: database-url
            - name: SIPHON_JWT_SECRET
              valueFrom:
                secretKeyRef:
                  name: siphon-secrets
                  key: jwt-secret
            - name: SIPHON_ENCRYPTION_KEY
              valueFrom:
                secretKeyRef:
                  name: siphon-secrets
                  key: encryption-key
          livenessProbe:
            httpGet:
              path: /health/live
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health/ready
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 10
          resources:
            requests:
              memory: "256Mi"
              cpu: "100m"
            limits:
              memory: "1Gi"
              cpu: "1000m"
          securityContext:
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 1000
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
      terminationGracePeriodSeconds: 3660   # > SIPHON_DRAIN_TIMEOUT
```

### Secrets

```bash
kubectl create secret generic siphon-secrets \
  --from-literal=database-url="postgresql+asyncpg://siphon:pass@postgres:5432/siphon" \
  --from-literal=jwt-secret="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  --from-literal=encryption-key="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
```

### Multi-pod considerations

Siphon is safe to run with multiple replicas because:

- **Scheduler**: APScheduler uses a PostgreSQL advisory lock (`pg_try_advisory_xact_lock`) to prevent the same scheduled job from firing on multiple pods simultaneously
- **Job queue**: Each pod has its own in-memory queue; jobs are not shared between pods. Airflow should point to a single pod (or use consistent-hash load balancing based on pipeline ID) if job distribution matters
- **Database**: All state is in PostgreSQL; pods are stateless

### Graceful shutdown

When Kubernetes sends SIGTERM:
1. Pod stops accepting new jobs (`/health/ready` returns 503)
2. Kubernetes stops routing traffic (after a few seconds, based on readiness probe interval)
3. Running jobs continue until complete or `SIPHON_DRAIN_TIMEOUT` expires
4. Process exits cleanly

Set `terminationGracePeriodSeconds` in the Deployment to be slightly larger than `SIPHON_DRAIN_TIMEOUT` to avoid SIGKILL interrupting a long extraction.

---

## Upgrading

1. Build and push the new image
2. Update the Deployment image tag
3. Kubernetes performs a rolling update: old pods drain before new ones start
4. Alembic runs automatically on startup and applies any new migrations

**If a migration requires a column addition or index:** Alembic runs this before uvicorn starts, so the API is not reachable until the migration completes. This means schema changes that take >30 seconds may cause liveness probe failures. For large tables, run the migration manually before the deploy.

---

## Monitoring

### Prometheus scrape config

```yaml
scrape_configs:
  - job_name: siphon
    static_configs:
      - targets: ['siphon:8000']
    metrics_path: /metrics
```

### Key metrics to alert on

| Metric | Alert condition | Suggested threshold |
|---|---|---|
| `siphon_jobs_total{status="failed"}` (rate) | Sudden increase in failures | > 3 failures in 5 minutes |
| `siphon_queue_depth` | Queue backing up | > 40 (with max_queue=50) |
| `siphon_job_duration_seconds` (p95) | Jobs taking too long | > 1800s (30 min) |
| `siphon_schema_changes_total` (rate) | Unexpected schema changes | Any increase |
| `/health/ready` | Service not accepting jobs | HTTP status != 200 |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Container exits immediately | `DATABASE_URL` invalid or PostgreSQL unreachable | Check DB connection string; verify network |
| Container healthy but UI shows 503 | `frontend/dist` not in image (build failed) | Check `pnpm build` step in `docker build` output |
| Jobs stuck in `queued` | All worker threads busy | Increase `SIPHON_MAX_WORKERS` |
| `alembic upgrade head` fails on start | DB version ahead of code (rollback scenario) | Re-deploy correct image version |
| Logs show `SIPHON_JWT_SECRET not set` | Warning, not error — default secret is used | Set `SIPHON_JWT_SECRET` in production |
| Pod OOMKilled | Large extraction loading too much into memory | Enable DQ buffering guard; reduce batch size |
