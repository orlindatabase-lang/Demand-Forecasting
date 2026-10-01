"""Tests for forecast_freeze.py: completed report weeks keep the value saved
while they were still open; running/upcoming weeks follow the live value.

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


def test_open_weeks_follow_live_values():
    ff.apply("s", {"A": {"2026-09-24": 10, "2026-10-01": 20}}, ENDS, date(2026, 9, 29))
    out = ff.apply("s", {"A": {"2026-09-24": 12, "2026-10-01": 25}}, ENDS, date(2026, 9, 30))
    assert out["A"] == {"2026-09-24": 12, "2026-10-01": 25}


def test_completed_week_keeps_last_open_value():
    ff.apply("s", {"A": {"2026-09-24": 12}}, ENDS, date(2026, 9, 30))       # last day of the week
    out = ff.apply("s", {"A": {"2026-09-24": 99}}, ENDS, date(2026, 10, 1))  # week over, retrained
    assert out["A"]["2026-09-24"] == 12
    out = ff.apply("s", {"A": {"2026-09-24": 55}}, ENDS, date(2026, 10, 9))  # later refreshes
    assert out["A"]["2026-09-24"] == 12
    assert ff.locked("s") == {"A": {"2026-09-24": 12}}


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
