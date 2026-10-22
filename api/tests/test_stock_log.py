"""Tests for stock_log.py (daily stock history).

Run: python -m pytest api/tests/test_stock_log.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the api/ modules

import pytest

import stock_log as sl


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "_DB", tmp_path / "stock.sqlite")


def test_one_snapshot_per_day_latest_wins():
    sl.save([("A-S", "A", 5, 2), ("A-M", "A", 0, 10)], date(2026, 10, 20))
    sl.save([("A-S", "A", 4, 2)], date(2026, 10, 20))          # a later refresh the same day
    sl.save([("A-S", "A", 3, 0)], date(2026, 10, 21))
    assert sl.days() == ["2026-10-20", "2026-10-21"]
    assert [(r["day"], r["sku"], r["stock_qty"]) for r in sl.read(style="A")] == [
        ("2026-10-20", "A-S", 4), ("2026-10-21", "A-S", 3)]


def test_read_by_sku():
    sl.save([("A-S", "A", 5, 2), ("B-S", "B", 1, 0)], date(2026, 10, 20))
    assert [r["style"] for r in sl.read(sku="B-S")] == ["B"]
