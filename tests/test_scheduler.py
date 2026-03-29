# tests/test_scheduler.py
"""Unit tests for scheduler helpers that do not require a real APScheduler instance."""
import uuid

import pytest

from siphon.scheduler import _parse_cron, _uuid_to_lock_key


class TestParseCron:
    def test_valid_five_field_cron(self):
        kwargs = _parse_cron("0 3 * * *")
        assert kwargs == {
            "minute": "0",
            "hour": "3",
            "day": "*",
            "month": "*",
            "day_of_week": "*",
        }

    def test_invalid_cron_raises(self):
        with pytest.raises(ValueError, match="5 fields"):
            _parse_cron("0 3 *")

    def test_cron_with_step(self):
        kwargs = _parse_cron("*/15 * * * *")
        assert kwargs["minute"] == "*/15"

    def test_strips_whitespace(self):
        kwargs = _parse_cron("  0 6 * * 1  ")
        assert kwargs["hour"] == "6"
        assert kwargs["day_of_week"] == "1"


class TestUuidToLockKey:
    def test_returns_positive_int(self):
        key = _uuid_to_lock_key(str(uuid.uuid4()))
        assert isinstance(key, int)
        assert key >= 0

    def test_fits_in_bigint(self):
        for _ in range(20):
            key = _uuid_to_lock_key(str(uuid.uuid4()))
            assert key <= 0x7FFFFFFFFFFFFFFF

    def test_deterministic(self):
        uid = str(uuid.uuid4())
        assert _uuid_to_lock_key(uid) == _uuid_to_lock_key(uid)

    def test_different_uuids_differ(self):
        keys = {_uuid_to_lock_key(str(uuid.uuid4())) for _ in range(50)}
        assert len(keys) > 1


class TestSyncScheduleNoOp:
    """When scheduler is None (no DATABASE_URL), sync_schedule must not crash."""

    @pytest.mark.asyncio
    async def test_sync_schedule_noop_when_no_scheduler(self):
        import siphon.scheduler as sched
        original = sched._scheduler
        sched._scheduler = None
        try:
            await sched.sync_schedule(uuid.uuid4(), "0 3 * * *", True)
        finally:
            sched._scheduler = original

    @pytest.mark.asyncio
    async def test_remove_schedule_noop_when_no_scheduler(self):
        import siphon.scheduler as sched
        original = sched._scheduler
        sched._scheduler = None
        try:
            await sched.remove_schedule(uuid.uuid4())
        finally:
            sched._scheduler = original
