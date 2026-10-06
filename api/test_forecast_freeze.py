"""Tests for forecast_freeze.py: running and completed report weeks keep the
value saved before they started; upcoming weeks follow the live value.

Run: python -m pytest api/test_forecast_freeze.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

import forecast_freeze as ff

ENDS = {"2026-09-16": date(2026, 9, 23), "2026-09-24": date(2026, 9, 30), "2026-10-01": date(2026, 10, 7)}


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ff, "_DB", tmp_path / "freeze.sqlite")


def test_upcoming_weeks_follow_live_values():
    ff.apply("s", {"A": {"2026-10-01": 20}}, ENDS, date(2026, 9, 29))
    out = ff.apply("s", {"A": {"2026-10-01": 25}}, ENDS, date(2026, 9, 30))
    assert out["A"] == {"2026-10-01": 25}


def test_running_week_keeps_value_saved_before_it_started():
    ff.apply("s", {"A": {"2026-10-01": 12}}, ENDS, date(2026, 9, 30))       # day before the week
    out = ff.apply("s", {"A": {"2026-10-01": 99}}, ENDS, date(2026, 10, 1))  # first day, retrained
    assert out["A"]["2026-10-01"] == 12
    out = ff.apply("s", {"A": {"2026-10-01": 40}}, ENDS, date(2026, 10, 5))  # mid-week refresh
    assert out["A"]["2026-10-01"] == 12
    out = ff.apply("s", {"A": {"2026-10-01": 55}}, ENDS, date(2026, 10, 9))  # after it ends
    assert out["A"]["2026-10-01"] == 12
    assert ff.locked("s") == {"A": {"2026-10-01": 12}}


def test_running_week_first_seen_mid_week_locks_at_first_value():
    out = ff.apply("s", {"A": {"2026-10-01": 30}}, ENDS, date(2026, 10, 3))
    assert out["A"]["2026-10-01"] == 30
    out = ff.apply("s", {"A": {"2026-10-01": 31}}, ENDS, date(2026, 10, 4))
    assert out["A"]["2026-10-01"] == 30


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
