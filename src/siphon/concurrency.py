# src/siphon/concurrency.py
"""Redis-backed per-connection concurrency semaphore with TTL self-healing.

Uses an atomic Lua script over a Redis Sorted Set (keyed by job_id with
expiration epoch as score) so crashed workers cannot permanently leak slots.
If REDIS_URL is not configured or Redis is unreachable, callers fall back to
the PostgreSQL concurrency check.
"""
import contextlib
import os
import time

import structlog

logger = structlog.get_logger()

_KEY_PREFIX = "siphon:conn_slots:"

# Atomic Lua script:
# KEYS[1] = sorted set key for the connection
# ARGV[1] = job_id
# ARGV[2] = limit (max concurrent jobs)
# ARGV[3] = now (unix timestamp float)
# ARGV[4] = expires_at (now + ttl_seconds)
# ARGV[5] = key_ttl (int seconds)
# Returns {acquired (1|0), count_before_or_after}
_ACQUIRE_LUA = """
local key = KEYS[1]
local job_id = ARGV[1]
local limit = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local expires_at = tonumber(ARGV[4])
local key_ttl = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', key, '-inf', now)

if redis.call('ZSCORE', key, job_id) then
    redis.call('ZADD', key, expires_at, job_id)
    redis.call('EXPIRE', key, key_ttl)
    return {1, redis.call('ZCARD', key)}
end

local current = redis.call('ZCARD', key)
if current >= limit then
    return {0, current}
end

redis.call('ZADD', key, expires_at, job_id)
redis.call('EXPIRE', key, key_ttl)
return {1, current + 1}
"""


def _get_redis_client(redis_url: str | None = None):
    url = redis_url if redis_url is not None else os.getenv("REDIS_URL")
    if not url:
        return None
    import redis

    return redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)


def acquire_connection_slot(
    connection_id: str,
    job_id: str,
    limit: int,
    ttl_seconds: int = 3660,
    redis_url: str | None = None,
) -> tuple[bool, int] | None:
    """Attempt to acquire a concurrency slot for *connection_id* in Redis.

    Returns:
        (True, count)  — slot acquired (or already held by job_id)
        (False, count) — concurrency limit reached
        None           — Redis not configured or unreachable (fallback to DB)
    """
    client = _get_redis_client(redis_url)
    if client is None:
        return None

    now = time.time()
    expires_at = now + ttl_seconds
    key_ttl = int(ttl_seconds) + 60
    key = f"{_KEY_PREFIX}{connection_id}"

    try:
        res = client.eval(_ACQUIRE_LUA, 1, key, job_id, limit, now, expires_at, key_ttl)
        acquired = bool(int(res[0]))
        count = int(res[1])
        return acquired, count
    except Exception as exc:
        logger.warning(
            "redis_concurrency_fallback",
            connection_id=connection_id,
            job_id=job_id,
            error=str(exc),
        )
        return None
    finally:
        with contextlib.suppress(Exception):
            client.close()


def release_connection_slot(
    connection_id: str,
    job_id: str,
    redis_url: str | None = None,
) -> None:
    """Release the concurrency slot for *job_id* on *connection_id* (best-effort)."""
    client = _get_redis_client(redis_url)
    if client is None:
        return

    key = f"{_KEY_PREFIX}{connection_id}"
    try:
        client.zrem(key, job_id)
    except Exception as exc:
        logger.warning(
            "redis_concurrency_release_failed",
            connection_id=connection_id,
            job_id=job_id,
            error=str(exc),
        )
    finally:
        with contextlib.suppress(Exception):
            client.close()
