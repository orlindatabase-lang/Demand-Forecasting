"""Tests for the "N SKUs" drill-down (/api/weekly-sales/skus/{style}):
data._split_by_share (how a Style's forecast is split over its SKUs) and
data._sku_key (one SKU spelled two ways or relisted counts once).

Run: python -m pytest api/tests/test_style_skus.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("DEFER_INITIAL_LOAD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the api/ modules

import data  # noqa: E402


def test_held_series_keeps_the_first_value_of_each_week(monkeypatch):
    monkeypatch.setattr(data, "SNAPSHOT_DATE", date(2026, 11, 4))
    monkeypatch.setattr(data, "_LATEST_HOLD", {})
    assert data._held_series("417-03", [10.0, 20.0], True) == [10.0, 20.0]
    assert data._held_series("417-03", [99.0, 99.0, 30.0], True) == [10.0, 20.0, 30.0]


def test_held_series_follows_the_dates_a_week_later(monkeypatch):
    monkeypatch.setattr(data, "SNAPSHOT_DATE", date(2026, 11, 4))
    monkeypatch.setattr(data, "_LATEST_HOLD", {})
    data._held_series("417-03", [10.0, 20.0], True)
    monkeypatch.setattr(data, "SNAPSHOT_DATE", date(2026, 11, 11))
    assert data._held_series("417-03", [99.0, 40.0], True) == [20.0, 40.0]


def test_held_series_records_nothing_for_a_run_rate_only_forecast(monkeypatch):
    monkeypatch.setattr(data, "SNAPSHOT_DATE", date(2026, 11, 4))
    monkeypatch.setattr(data, "_LATEST_HOLD", {})
    assert data._held_series("417-03", [10.0], False) == [10.0]
    assert data._LATEST_HOLD == {} and data._held_series("417-03", None, True) is None


def test_split_adds_back_up_to_the_style_forecast():
    parts = data._split_by_share(100, [1, 1, 1])
    assert sum(parts) == 100 and sorted(parts) == [33, 33, 34]


def test_split_follows_the_sale_share():
    assert data._split_by_share(60, [10, 20, 30]) == [10, 20, 30]


def test_no_sale_splits_equally_and_no_forecast_gives_zeros():
    assert data._split_by_share(4, [0, 0]) == [2, 2]
    assert data._split_by_share(0, [5, 5]) == [0, 0]


def test_dash_and_underscore_spellings_are_the_same_sku():
    assert data._sku_key("474-03_M") == data._sku_key("474-03-M") == "474-03-M"
    assert data._sku_key(" 474-03 xl ") == "474-03-XL"
    assert data._sku_key("474-03-M") != data._sku_key("474-03-XL")


def test_a_letter_after_the_size_is_a_relisting_of_the_same_sku():
    assert data._sku_key("005-03-XL-A") == data._sku_key("005-03-XL") == "005-03-XL"
    assert data._sku_key("474-05-XS_B") == "474-05-XS"
    # not a size + one letter: a different variant, kept apart
    assert data._sku_key("442-01-BB-M") == "442-01-BB-M"
    assert data._sku_key("404-01-Without Dupatta-L") == "404-01-WITHOUT-DUPATTA-L"


def test_another_design_number_before_the_size_is_the_same_sku():
    assert data._sku_key("423-03-427-03-L", "423-03") == data._sku_key("423-03-L", "423-03") == "423-03-L"
    assert data._sku_key("423-03-427-03-XL-A", "423-03") == "423-03-XL"
    # without the Style, or when no size follows, the code is left as it is
    assert data._sku_key("423-03-427-03-L") == "423-03-427-03-L"
    assert data._sku_key("K-7502_13-14 Years", "K-7502") == "K-7502-13-14-YEARS"


def test_2xl_is_xxl_and_3xl_is_xxxl():
    assert data._sku_key("001-04-2XL") == data._sku_key("001-04-XXL") == "001-04-XXL"
    assert data._sku_key("001-04-2XL-A") == "001-04-XXL"
    assert data._sku_key("001-04-3XL") == data._sku_key("001-04_XXXL") == "001-04-XXXL"
