"""Tests for weekly_log.py's settling period: a finished week's actual keeps
updating for SETTLE_DAYS days (late marketplace orders), then locks.

Run: python -m pytest api/test_weekly_log.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

import weekly_log as wl


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(wl, "_DB", tmp_path / "log.sqlite")


def _save(week_start: str, week_end: str, as_of: str, styles=("A", "B")) -> None:
    rows = [{"style": s, "sub_category": "x", "category": "y", "forecast_qty": 10,
             "forecast_2m_qty": 100, "available_qty": 40, "suggested_production_qty": 60} for s in styles]
    wl.save_running_week(rows, "September 2026", "W4", date.fromisoformat(week_start),
                         date.fromisoformat(week_end), date.fromisoformat(as_of))


def _row(style: str, week_start: str) -> dict:
    return next(r for r in wl.read(style) if r["week_start"] == week_start)


def test_week_settles_then_completes():
    _save("2026-09-24", "2026-09-30", "2026-09-28")
    # running week not finished yet -> nothing to read
    assert wl.weeks_needing_actual(date(2026, 9, 29)) == []

    # data covers the week's last day -> first actual, settling
    assert wl.weeks_needing_actual(date(2026, 10, 1)) == [("2026-09-24", "2026-09-30")]
    assert wl.update_actual("2026-09-24", "2026-09-30", {"A": 50, "B": 5}, date(2026, 10, 1), date(2026, 10, 1)) == "settling"
    assert _row("A", "2026-09-24")["actual_qty"] == 50

    # late orders arrive -> actual updated, still settling
    assert wl.weeks_needing_actual(date(2026, 10, 2)) == [("2026-09-24", "2026-09-30")]
    assert wl.update_actual("2026-09-24", "2026-09-30", {"A": 58, "B": 6}, date(2026, 10, 2), date(2026, 10, 2)) == "settling"
    r = _row("A", "2026-09-24")
    assert (r["actual_qty"], r["status"], r["completed_on"]) == (58, "settling", "2026-10-02")
    # forecast / stock / suggestion untouched
    assert (r["forecast_qty"], r["forecast_2m_qty"], r["available_qty"], r["suggested_production_qty"]) == (10, 100, 40, 60)

    # 14 days after the week ended -> final update, completed
    assert wl.update_actual("2026-09-24", "2026-09-30", {"A": 60, "B": 6}, date(2026, 10, 14), date(2026, 10, 14)) == "completed"
    assert _row("A", "2026-09-24")["status"] == "completed"
    # never read again
    assert wl.weeks_needing_actual(date(2026, 10, 20)) == []


def test_week_completed_before_settling_existed_is_reopened_inside_window():
    _save("2026-09-24", "2026-09-30", "2026-09-28")
    # a row locked the day after the week ended, by the code before settling existed
    with wl._connect() as conn:
        conn.execute("UPDATE weekly_production SET actual_qty = 50, completed_on = '2026-10-01', status = 'completed'")
    assert _row("A", "2026-09-24")["status"] == "completed"
    # still within 14 days of its end -> picked up again and corrected
    assert wl.weeks_needing_actual(date(2026, 10, 3)) == [("2026-09-24", "2026-09-30")]
    assert wl.update_actual("2026-09-24", "2026-09-30", {"A": 58}, date(2026, 10, 3), date(2026, 10, 3)) == "settling"
    assert _row("A", "2026-09-24")["actual_qty"] == 58


def test_old_completed_weeks_are_not_touched():
    _save("2026-09-01", "2026-09-07", "2026-09-05")
    wl.update_actual("2026-09-01", "2026-09-07", {"A": 30}, date(2026, 9, 22), date(2026, 9, 22))
    assert _row("A", "2026-09-01")["status"] == "completed"
    assert wl.weeks_needing_actual(date(2026, 10, 3)) == []


def test_running_week_values_still_refresh_while_open_only():
    _save("2026-09-24", "2026-09-30", "2026-09-25")
    wl.update_actual("2026-09-24", "2026-09-30", {"A": 50}, date(2026, 10, 1), date(2026, 10, 1))
    # a later save for the same week must not overwrite the settled week's locked values
    rows = [{"style": "A", "sub_category": "x", "category": "y", "forecast_qty": 99,
             "forecast_2m_qty": 999, "available_qty": 1, "suggested_production_qty": 998}]
    wl.save_running_week(rows, "September 2026", "W4", date(2026, 9, 24), date(2026, 9, 30), date(2026, 10, 2))
    r = _row("A", "2026-09-24")
    assert (r["forecast_2m_qty"], r["available_qty"], r["suggested_production_qty"]) == (100, 40, 60)


def test_retention_counts_settling_and_completed_weeks():
    starts = ["2026-08-01", "2026-08-08", "2026-08-16", "2026-08-24", "2026-09-01", "2026-09-08",
              "2026-09-16", "2026-09-24", "2026-10-01"]
    ends = ["2026-08-07", "2026-08-15", "2026-08-23", "2026-08-31", "2026-09-07", "2026-09-15",
            "2026-09-23", "2026-09-30", "2026-10-07"]
    for ws, we in zip(starts, ends):
        _save(ws, we, ws, styles=("A",))
        wl.update_actual(ws, we, {"A": 1}, date.fromisoformat(we), date.fromisoformat(we))
    kept = sorted({r["week_start"] for r in wl.read()})
    assert len(kept) == wl.KEEP_COMPLETED_WEEKS
    assert kept[0] == "2026-08-08"
