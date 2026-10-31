"""Tests for forecast_api.py: the week list behind /api/forecast/latest and
/api/forecast/frozen (end dates and running / upcoming / completed status).

Run: python -m pytest api/tests/test_forecast_api.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("DEFER_INITIAL_LOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the api/ modules

import forecast_api  # noqa: E402


def _grid():
    weeks = lambda month, starts: NS(label=month, weeks=[NS(weekStart=s, label=f"W{i + 1}") for i, s in enumerate(starts)])
    return NS(months=[
        weeks("October 2026", ["2026-10-01", "2026-10-08", "2026-10-16", "2026-10-24"]),
        weeks("November 2026", ["2026-11-01", "2026-11-08", "2026-11-16", "2026-11-24"]),
    ])


def test_weeks_end_the_day_before_the_next_and_the_last_at_month_end():
    w = forecast_api._weeks(_grid(), date(2026, 10, 31))
    assert [(x.weekStart, x.weekEnd) for x in w[3:5]] == [("2026-10-24", "2026-10-31"), ("2026-11-01", "2026-11-07")]
    assert w[-1].weekEnd == "2026-11-30"
    assert (w[3].label, w[3].month) == ("Oct W4", "2026-10")


def test_week_status_follows_today():
    w = forecast_api._weeks(_grid(), date(2026, 10, 31))
    assert [x.status for x in w[2:5]] == ["completed", "running", "upcoming"]
