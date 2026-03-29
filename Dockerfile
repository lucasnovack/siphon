# ── Stage 0: frontend builder ─────────────────────────────────────────────────
FROM node:22-slim AS frontend-builder

RUN npm install -g pnpm

WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Install dependencies into /app/.venv — no cache, no dev deps
RUN uv sync --frozen --no-dev --no-cache

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user (NFR-13): UID 1000
RUN groupadd -r siphon && useradd -r -g siphon -u 1000 siphon

WORKDIR /app

# Copy only the virtualenv and source — no uv, no pip cache, no build artifacts
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SIPHON_PORT=8000

EXPOSE ${SIPHON_PORT}

USER siphon

# /tmp is the only writable directory (NFR-13 — read-only fs enforced at runtime via securityContext)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${SIPHON_PORT}/health/live')"

# Run DB migrations (no-op if DATABASE_URL is unset), then start the server.
# --workers 1: in-memory state is not shared between processes.
CMD ["sh", "-c", "[ -n \"$DATABASE_URL\" ] && alembic upgrade head; uvicorn siphon.main:app --host 0.0.0.0 --port ${SIPHON_PORT} --workers 1"]
