# tests/test_variables.py
import os
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from siphon.variables import resolve


def _fixed_now(year=2026, month=3, day=25, hour=10, tz="America/Sao_Paulo"):
    return datetime(year, month, day, hour, 0, 0, tzinfo=ZoneInfo(tz))


class TestResolve:
    def test_today_replaced(self):
        with patch("siphon.variables._now", return_value=_fixed_now()):
            result = resolve("SELECT * FROM t WHERE dt = @TODAY")
        assert result == "SELECT * FROM t WHERE dt = '2026-03-25'"

    def test_min_date_replaced(self):
        result = resolve("WHERE dt > @MIN_DATE")
        assert result == "WHERE dt > '1997-01-01'"

    def test_last_month_replaced(self):
        with patch("siphon.variables._now", return_value=_fixed_now(month=3)):
            result = resolve("WHERE dt >= @LAST_MONTH")
        assert result == "WHERE dt >= '2026-02-01'"

    def test_next_month_replaced(self):
        with patch("siphon.variables._now", return_value=_fixed_now(month=3)):
            result = resolve("WHERE dt < @NEXT_MONTH")
        assert result == "WHERE dt < '2026-04-01'"

    def test_multiple_variables_in_same_query(self):
        with patch("siphon.variables._now", return_value=_fixed_now()):
            result = resolve("WHERE dt BETWEEN @LAST_MONTH AND @TODAY")
        assert result == "WHERE dt BETWEEN '2026-02-01' AND '2026-03-25'"

    def test_no_variables_unchanged(self):
        query = "SELECT id, name FROM users"
        assert resolve(query) == query

    def test_timezone_env_var_respected(self):
        # UTC midnight = still yesterday in Sao Paulo (UTC-3)
        utc_midnight = datetime(2026, 3, 26, 0, 30, 0, tzinfo=ZoneInfo("UTC"))
        sp_tz = ZoneInfo("America/Sao_Paulo")
        expected_sp_date = utc_midnight.astimezone(sp_tz).strftime("'%Y-%m-%d'")
        with patch("siphon.variables._now", return_value=utc_midnight.astimezone(sp_tz)):
            result = resolve("@TODAY")
        assert result == expected_sp_date

    def test_december_last_month_wraps_year(self):
        # January → last month should be December of previous year
        with patch("siphon.variables._now", return_value=_fixed_now(year=2026, month=1)):
            result = resolve("@LAST_MONTH")
        assert result == "'2025-12-01'"

    def test_december_next_month_wraps_year(self):
        # December → next month should be January of next year
        with patch("siphon.variables._now", return_value=_fixed_now(year=2025, month=12)):
            result = resolve("@NEXT_MONTH")
        assert result == "'2026-01-01'"
