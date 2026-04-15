import os
import random
import time

import structlog

logger = structlog.get_logger()

_RETRYABLE_ERRORS = (ConnectionError, TimeoutError, OSError)

_DEFAULT_MAX_RETRIES = int(os.getenv("SIPHON_MAX_RETRIES", "3"))
_DEFAULT_BACKOFF_BASE = float(os.getenv("SIPHON_RETRY_BACKOFF_BASE", "2"))


def _with_retry(fn, max_retries: int = _DEFAULT_MAX_RETRIES, backoff_base: float = _DEFAULT_BACKOFF_BASE):
    """Call fn(), retrying up to max_retries times on transient errors.

    Uses exponential backoff with jitter: sleep = backoff_base * 2^attempt * uniform(0.5, 1.5).
    Raises immediately on ValueError, TypeError, or other non-retryable errors.
    """
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except _RETRYABLE_ERRORS as exc:
            if attempt == max_retries:
                raise
            sleep = backoff_base * (2 ** attempt) * random.uniform(0.5, 1.5)
            logger.warning(
                "retry_attempt",
                attempt=attempt + 1,
                max_retries=max_retries,
                error=str(exc),
                retry_in_seconds=round(sleep, 1),
            )
            time.sleep(sleep)
