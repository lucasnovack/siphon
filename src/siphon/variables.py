# src/siphon/variables.py
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta

_TIMEZONE = ZoneInfo(os.getenv("SIPHON_TIMEZONE", "America/Sao_Paulo"))
_MIN_DATE = os.getenv("SIPHON_MIN_DATE", "1997-01-01")


def _now() -> datetime:
    """Returns current datetime in the configured timezone. Separated for testability."""
    return datetime.now(tz=_TIMEZONE)


def resolve(query: str) -> str:
    """Resolve dynamic date variables in a SQL query before execution.

    Variables:
        @TODAY      → current date in SIPHON_TIMEZONE, e.g. '2026-03-25'
        @MIN_DATE   → configured minimum extraction date (SIPHON_MIN_DATE), e.g. '1997-01-01'
        @LAST_MONTH → first day of previous month, e.g. '2026-02-01'
        @NEXT_MONTH → first day of next month, e.g. '2026-04-01'
    """
    now = _now()
    last_month = now - relativedelta(months=1)
    next_month = now + relativedelta(months=1)

    return (
        query
        .replace("@TODAY", now.strftime("'%Y-%m-%d'"))
        .replace("@MIN_DATE", f"'{_MIN_DATE}'")
        .replace("@LAST_MONTH", last_month.strftime("'%Y-%m-01'"))
        .replace("@NEXT_MONTH", next_month.strftime("'%Y-%m-01'"))
    )
