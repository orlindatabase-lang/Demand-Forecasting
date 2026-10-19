"""Tests for forecast_freeze.py: completed report weeks keep the value last
saved while they ran; upcoming and running weeks follow the live value.

Run: python -m pytest api/tests/test_forecast_freeze.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the api/ modules

import pytest

import forecast_freeze as ff

ENDS = {"2026-09-16": date(2026, 9, 23), "2026-09-24": date(2026, 9, 30), "2026-10-01": date(2026, 10, 7),
        "2026-10-08": date(2026, 10, 15), "2026-10-16": date(2026, 10, 23)}


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "_DB", tmp_path / "freeze.sqlite")


def test_upcoming_weeks_follow_live_values():
    ff.apply("s", {"A": {"2026-10-01": 20}}, ENDS, date(2026, 9, 29))
    out = ff.apply("s", {"A": {"2026-10-01": 25}}, ENDS, date(2026, 9, 30))
    assert out["A"] == {"2026-10-01": 25}


def test_running_week_follows_live_values_then_locks_when_it_ends():
    ff.apply("s", {"A": {"2026-10-01": 12}}, ENDS, date(2026, 9, 30))       # day before the week
    out = ff.apply("s", {"A": {"2026-10-01": 99}}, ENDS, date(2026, 10, 1))  # first day, retrained
    assert out["A"]["2026-10-01"] == 99                                      # running: live
    out = ff.apply("s", {"A": {"2026-10-01": 40}}, ENDS, date(2026, 10, 7))  # last day
    assert out["A"]["2026-10-01"] == 40
    out = ff.apply("s", {"A": {"2026-10-01": 55}}, ENDS, date(2026, 10, 9))  # after it ends
    assert out["A"]["2026-10-01"] == 40                                      # locked at its last value
    assert ff.locked("s") == {"A": {"2026-10-01": 40}}


def test_running_week_locked_by_the_old_rule_is_reopened():
    import sqlite3
    with sqlite3.connect(ff._DB) as conn:
        conn.executescript(ff._SCHEMA)
        conn.execute("INSERT INTO frozen_forecast (scope, key, period_start, qty, status, updated_on) VALUES ('s', 'A', '2026-10-01', 12, 'locked', '2026-10-01')")
        conn.execute("INSERT INTO frozen_forecast (scope, key, period_start, qty, status, updated_on) VALUES ('s', 'Gone', '2026-10-01', 5, 'locked', '2026-10-01')")
    out = ff.apply("s", {"A": {"2026-10-01": 30}}, ENDS, date(2026, 10, 3))
    assert out["A"]["2026-10-01"] == 30
    assert ff.locked("s") == {}                                   # both reopened, incl. a key not in today's values
    assert ff.stored("s") == {"A": {"2026-10-01": 30}, "Gone": {"2026-10-01": 5}}


def test_week_first_seen_after_completion_locks_at_first_value():
    out = ff.apply("s", {"A": {"2026-09-16": 7}}, ENDS, date(2026, 9, 29))
    assert out["A"]["2026-09-16"] == 7
    out = ff.apply("s", {"A": {"2026-09-16": 8}}, ENDS, date(2026, 9, 30))
    assert out["A"]["2026-09-16"] == 7


def test_locked_value_survives_missing_live_cell():
    ff.apply("s", {"A": {"2026-09-16": 7}}, ENDS, date(2026, 9, 29))
    out = ff.apply("s", {"A": {}}, ENDS, date(2026, 9, 30))
    assert out["A"] == {"2026-09-16": 7}


def test_scopes_and_keys_are_independent():
    ff.apply("s1", {"A": {"2026-09-16": 1}, "B": {"2026-09-16": 2}}, ENDS, date(2026, 9, 29))
    ff.apply("s2", {"A": {"2026-09-16": 3}}, ENDS, date(2026, 9, 29))
    assert ff.locked("s1", {"A"}) == {"A": {"2026-09-16": 1}}
    assert ff.locked("s2") == {"A": {"2026-09-16": 3}}


def test_value_at_week_start_is_kept():
    ff.apply("s", {"A": {"2026-10-01": 12}}, ENDS, date(2026, 9, 30))       # day before the week
    ff.apply("s", {"A": {"2026-10-01": 99}}, ENDS, date(2026, 10, 1))       # started, retrained
    ff.apply("s", {"A": {"2026-10-01": 40}}, ENDS, date(2026, 10, 5))
    assert ff.start_values("s") == {"A": {"2026-10-01": 12}}                 # the value from before it started
    out = ff.apply("s", {"A": {"2026-10-01": 55}}, ENDS, date(2026, 10, 9))  # ended
    assert out["A"]["2026-10-01"] == 40 and ff.start_values("s") == {"A": {"2026-10-01": 12}}


def test_old_store_gets_the_start_column():
    import sqlite3
    with sqlite3.connect(ff._DB) as conn:
        conn.execute("""CREATE TABLE frozen_forecast (scope TEXT NOT NULL, key TEXT NOT NULL, period_start TEXT NOT NULL,
                        qty REAL NOT NULL, status TEXT NOT NULL, updated_on TEXT NOT NULL, PRIMARY KEY (scope, key, period_start))""")
        conn.execute("INSERT INTO frozen_forecast VALUES ('s', 'A', '2026-10-01', 7, 'open', '2026-09-30')")
    ff.apply("s", {"A": {"2026-10-01": 9}}, ENDS, date(2026, 10, 2))
    assert ff.start_values("s") == {"A": {"2026-10-01": 7}}


def test_start_value_unknown_when_nothing_was_saved_before_the_start():
    ff.apply("s", {"A": {"2026-10-01": 30}}, ENDS, date(2026, 10, 3))       # first seen mid-week
    ff.apply("s", {"A": {"2026-10-01": 31}}, ENDS, date(2026, 10, 4))
    assert ff.start_values("s") == {}


def test_no_two_month_snapshot_by_default():
    # 2026-10-19: the 2-month freeze is off - only completed weeks are frozen.
    ff.apply("s", {"A": {"2026-10-01": 10, "2026-10-08": 20, "2026-10-16": 30}}, ENDS, date(2026, 10, 3))
    assert ff.frozen_values("s") == ({}, "", "")


def test_two_month_freeze_is_kept_until_its_window_ends(monkeypatch):
    monkeypatch.setattr(ff, "SNAPSHOT_ENABLED", True)   # FORECAST_2M_FREEZE=on
    ff.apply("s", {"A": {"2026-10-01": 10, "2026-10-08": 20, "2026-10-16": 30}}, ENDS, date(2026, 10, 3))
    vals, on, until = ff.frozen_values("s")
    assert vals == {"A": {"2026-10-01": 10, "2026-10-08": 20, "2026-10-16": 30}} and on == "2026-10-03"
    # later refreshes with new forecasts do not move the frozen numbers
    ff.apply("s", {"A": {"2026-10-01": 11, "2026-10-08": 99, "2026-10-16": 77}}, ENDS, date(2026, 10, 20))
    assert ff.frozen_values("s")[0] == vals and until == "2026-12-04"
